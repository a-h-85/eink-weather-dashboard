#!/usr/bin/env python3
"""E-ink weather dashboard server (runs on the Raspberry Pi).

Endpoints
  GET /display.bin?bat=<mV>  raw 1-bit frame for the ESP32 (400*300/8 = 15000 bytes)
                             header X-Sleep-Seconds: how long the ESP32 should sleep
  GET /preview.png           the same frame as PNG
  GET /api/weather           the summarized weather data as JSON

Weather source: DWD via Bright Sky (default) or Open-Meteo; the other one is used as fallback.
  GET /                      small preview page

Usage
  python3 server.py                     # serve (config.toml next to this file)
  python3 server.py --render out.png    # render one frame and exit
  python3 server.py --find "Hamburg"    # look up a place name for [location]
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import threading
from collections import deque
import time
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # Python 3.9/3.10 (Raspberry Pi OS Bullseye): pip/apt package "tomli"
    import tomli as tomllib
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import brightsky
import location
import render
import weather

# name -> (fetch_raw, summarize, footer credit)
PROVIDERS = {
    "dwd": (brightsky.fetch_raw, brightsky.summarize, "DWD"),
    "openmeteo": (weather.fetch_raw, weather.summarize, "Open-Meteo"),
}

HERE = Path(__file__).resolve().parent
CACHE_SECONDS = 10 * 60        # refresh the weather in the background at least this often
PREFETCH_LEAD = 60             # ...and this many seconds before each ESP32 wake-up
SLOT_OFFSET = 20               # the ESP32 wakes this many seconds after :00 / :30
STALE_SECONDS = 2 * 60 * 60    # show "data outdated" after this
MAINS_BELOW_MV = 1000          # reported battery voltage below this = running on USB power
BATTERY_WINDOW = 6             # updates (= 3 h at 30 min) to smooth the battery reading over
BATTERY_FILE = HERE / ".battery-history.json"
SETTINGS_FILE = HERE / ".settings.json"   # choices made on the preview page (display language)
LANGUAGES = {"en": "English", "de": "Deutsch"}

log = logging.getLogger("eink")


# NiMH (e.g. Eneloop) at a light load: volts per cell -> remaining %. Rough, but far better than linear.
NIMH_CURVE = [(1.07, 0), (1.10, 3), (1.15, 8), (1.18, 13), (1.21, 22), (1.23, 32), (1.25, 47),
              (1.27, 62), (1.29, 76), (1.31, 86), (1.34, 94), (1.38, 100)]


def nimh_pct(v_cell: float) -> int:
    pts = NIMH_CURVE
    if v_cell <= pts[0][0]:
        return 0
    for (v0, p0), (v1, p1) in zip(pts, pts[1:]):
        if v_cell <= v1:
            return round(p0 + (p1 - p0) * (v_cell - v0) / (v1 - v0))
    return 100


class Dashboard:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.tz = cfg["location"]["timezone"]
        self.lock = threading.Lock()
        self.raw: dict | None = None
        self.provider = ""
        first = cfg.get("weather", {}).get("provider", "dwd")
        self.order = [first] + [p for p in PROVIDERS if p != first]
        self.fetched_at = 0.0
        self.battery_mv: int | None = None   # last value the ESP32 reported
        # The ESP32's ADC sometimes reads ~120 mV low for a few wake-ups. Battery voltage only
        # falls, so the display uses the highest reading of the last BATTERY_WINDOW updates.
        self.battery_hist: deque[int] = deque(maxlen=BATTERY_WINDOW)
        try:
            saved = json.loads(BATTERY_FILE.read_text())
            self.battery_hist.extend(saved.get("history", []))
            self.battery_mv = saved.get("last")
        except (OSError, ValueError):
            pass

    @property
    def language(self) -> str:
        """Display language: the preview page's choice, else config.toml, else English."""
        try:
            lang = json.loads(SETTINGS_FILE.read_text()).get("language")
            if lang in LANGUAGES:
                return lang
        except (OSError, ValueError):
            pass
        lang = self.cfg.get("display", {}).get("language", "en")
        return lang if lang in LANGUAGES else "en"

    def set_language(self, lang: str):
        try:
            settings = json.loads(SETTINGS_FILE.read_text())
        except (OSError, ValueError):
            settings = {}
        settings["language"] = lang
        SETTINGS_FILE.write_text(json.dumps(settings))
        log.info("display language set to %s", lang)

    def fetch(self) -> bool:
        """Download the forecast (preferred provider first, the other as fallback).
        Runs in the background thread, so the ESP32 never waits for a weather service."""
        loc = self.cfg["location"]
        for name in self.order:
            try:
                raw = PROVIDERS[name][0](loc["latitude"], loc["longitude"], self.tz)
            except Exception as e:
                log.warning("forecast fetch from %s failed: %s", name, e)
                continue
            with self.lock:
                self.raw, self.provider, self.fetched_at = raw, name, time.time()
            log.info("fetched forecast from %s", name)
            return True
        return False  # all failed: keep serving the last good data

    def model(self) -> tuple[dict | None, bool]:
        if self.raw is None:  # first start (or --render): nothing cached yet
            self.fetch()
        with self.lock:
            if self.raw is None:
                return None, False
            stale = time.time() - self.fetched_at > STALE_SECONDS
            return PROVIDERS[self.provider][1](self.raw, self.tz), stale

    def next_wake(self, now: float) -> float:
        """Unix time of the ESP32's next wake-up (:00 / :30 + SLOT_OFFSET)."""
        period = int(self.cfg["display"].get("refresh_minutes", 30)) * 60
        local = datetime.fromtimestamp(now, ZoneInfo(self.tz))
        since_midnight = local.hour * 3600 + local.minute * 60 + local.second + local.microsecond / 1e6
        return now + period - since_midnight % period + SLOT_OFFSET

    def prefetch_forever(self):
        """Keep the weather fresh: every CACHE_SECONDS and PREFETCH_LEAD s before each wake-up."""
        period = int(self.cfg["display"].get("refresh_minutes", 30)) * 60
        while True:
            ok = self.fetch()
            now = time.time()
            target = self.next_wake(now) - PREFETCH_LEAD
            if target - now < 5:                      # just fetched for this slot -> the next one
                target += period
            wait = min(CACHE_SECONDS, target - now)
            if not ok:
                wait = min(wait, 60)                  # retry sooner after a failure
            time.sleep(max(1.0, wait))

    def report_battery(self, mv: int):
        if mv < MAINS_BELOW_MV:
            self.battery_hist.clear()        # on USB: start over when the battery comes back
        else:
            self.battery_hist.append(mv)
        self.battery_mv = mv
        try:
            BATTERY_FILE.write_text(json.dumps({"last": mv, "history": list(self.battery_hist)}))
        except OSError as e:
            log.warning("could not save battery history: %s", e)

    def battery_smoothed(self) -> int | None:
        return max(self.battery_hist) if self.battery_hist else self.battery_mv

    def on_mains(self) -> bool:
        """The ESP reports ~0 mV when no battery is connected (USB power / no divider)."""
        return self.battery_mv is not None and self.battery_mv < MAINS_BELOW_MV

    def battery_pct(self) -> int | None:
        if self.battery_mv is None or self.on_mains():
            return None
        mv = self.battery_smoothed()
        b = self.cfg.get("battery", {})
        if b.get("type") == "nimh":
            return nimh_pct(mv / 1000 / int(b.get("cells", 3)))
        lo, hi = b.get("empty_mv", 3300), b.get("full_mv", 4150)
        return max(0, min(100, round((mv - lo) * 100 / (hi - lo))))

    def image(self):
        model, stale = self.model()
        return render.render(model, lang=self.language,
                             source=PROVIDERS[self.provider][2] if self.provider else "",
                             location=self.cfg["location"].get("name", ""),
                             now=datetime.now(ZoneInfo(self.tz)),
                             battery_pct=self.battery_pct(), mains=self.on_mains(), stale=stale)

    def sleep_seconds(self) -> int:
        """Sleep until shortly after the next :00 / :30 so updates stay aligned to the clock."""
        period = int(self.cfg["display"].get("refresh_minutes", 30)) * 60
        now = time.time()
        secs = round(self.next_wake(now) - now)
        if secs < 300:  # woke up a bit early -> skip to the following slot
            secs += period
        return secs


class Handler(BaseHTTPRequestHandler):
    dash: Dashboard

    def _send(self, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, str(v))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        try:
            if url.path == "/display.bin":
                if "bat" in q and q["bat"][0].isdigit():
                    self.dash.report_battery(int(q["bat"][0]))
                body = render.to_epd_bytes(self.dash.image())
                self._send(body, "application/octet-stream",
                           {"X-Sleep-Seconds": self.dash.sleep_seconds()})
            elif url.path == "/preview.png":
                buf = io.BytesIO()
                self.dash.image().save(buf, "PNG")
                self._send(buf.getvalue(), "image/png")
            elif url.path == "/api/weather":
                model, stale = self.dash.model()
                body = json.dumps({"provider": self.dash.provider, "stale": stale, "battery_mv": self.dash.battery_mv, "battery_mv_smoothed": self.dash.battery_smoothed(),
                                   "weather": model}, indent=2, ensure_ascii=False)
                self._send(body.encode(), "application/json; charset=utf-8")
            elif url.path == "/language":
                lang = q.get("set", [""])[0]
                if lang in LANGUAGES:
                    self.dash.set_language(lang)
                self.send_response(303)
                self.send_header("Location", "/")
                self.end_headers()
            elif url.path == "/":
                self._send(self.index_page().encode(), "text/html; charset=utf-8")
            else:
                self.send_error(404)
        except Exception:
            log.exception("request failed")
            self.send_error(500)

    def index_page(self) -> str:
        cur = self.dash.language
        buttons = "".join(
            f'<a class="btn{" on" if code == cur else ""}" href="/language?set={code}">{name}</a>'
            for code, name in LANGUAGES.items())
        return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>E-Ink Dashboard</title>
<style>
 body{{background:#d5d8d6;font:15px system-ui,sans-serif;color:#1d2421;text-align:center;margin:0;padding:24px 16px}}
 img{{width:800px;max-width:100%;image-rendering:pixelated;border:12px solid #fff;box-sizing:border-box}}
 .bar{{display:flex;gap:8px;justify-content:center;align-items:center;margin:16px 0 6px;flex-wrap:wrap}}
 .btn{{padding:7px 16px;border:1px solid #1d2421;border-radius:6px;color:#1d2421;text-decoration:none;background:#fff}}
 .btn.on{{background:#1d2421;color:#fff}}
 p{{color:#4b5450;margin:6px 0}} a.small{{color:#4b5450}}
</style>
<img src="/preview.png" alt="Current dashboard frame">
<div class="bar"><span>Display language</span>{buttons}</div>
<p>The e-paper picks this up at its next update (:00 / :30).</p>
<p><a class="small" href="/api/weather">Weather data (JSON)</a></p>"""

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.toml"))
    ap.add_argument("--render", metavar="PNG", help="render one frame to a PNG and exit")
    ap.add_argument("--find", metavar="PLACE", help="list places with this name and exit")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.find:
        for r in location.search(args.find) or []:
            print(location.describe(r))
        return
    with open(args.config, "rb") as f:
        cfg = tomllib.load(f)
    cfg["location"] = location.resolve(cfg.get("location", {}), HERE / ".location-cache.json")
    dash = Dashboard(cfg)

    if args.render:
        dash.image().save(args.render)
        print(f"wrote {args.render}")
        return

    Handler.dash = dash
    threading.Thread(target=dash.prefetch_forever, name="prefetch", daemon=True).start()
    srv = cfg.get("server", {})
    host, port = srv.get("host", "0.0.0.0"), int(srv.get("port", 8080))
    log.info("listening on http://%s:%d", host, port)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
