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
CACHE_SECONDS = 10 * 60        # don't ask Open-Meteo more often than this
STALE_SECONDS = 2 * 60 * 60    # show "data outdated" after this
MAINS_BELOW_MV = 1000          # reported battery voltage below this = running on USB power

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
        self.battery_mv: int | None = None

    def _refresh(self):
        if time.time() - self.fetched_at < CACHE_SECONDS and self.raw is not None:
            return
        loc = self.cfg["location"]
        for name in self.order:  # preferred provider first, the other as fallback
            try:
                self.raw = PROVIDERS[name][0](loc["latitude"], loc["longitude"], self.tz)
                self.provider = name
                self.fetched_at = time.time()
                log.info("fetched forecast from %s", name)
                return
            except Exception as e:
                log.warning("forecast fetch from %s failed: %s", name, e)
        # all failed: keep serving the last good data

    def model(self) -> tuple[dict | None, bool]:
        with self.lock:
            self._refresh()
            if self.raw is None:
                return None, False
            stale = time.time() - self.fetched_at > STALE_SECONDS
            return PROVIDERS[self.provider][1](self.raw, self.tz), stale

    def on_mains(self) -> bool:
        """The ESP reports ~0 mV when no battery is connected (USB power / no divider)."""
        return self.battery_mv is not None and self.battery_mv < MAINS_BELOW_MV

    def battery_pct(self) -> int | None:
        if self.battery_mv is None or self.on_mains():
            return None
        b = self.cfg.get("battery", {})
        if b.get("type") == "nimh":
            return nimh_pct(self.battery_mv / 1000 / int(b.get("cells", 3)))
        lo, hi = b.get("empty_mv", 3300), b.get("full_mv", 4150)
        return max(0, min(100, round((self.battery_mv - lo) * 100 / (hi - lo))))

    def image(self):
        model, stale = self.model()
        return render.render(model, lang=self.cfg["display"].get("language", "en"),
                             source=PROVIDERS[self.provider][2] if self.provider else "",
                             location=self.cfg["location"].get("name", ""),
                             now=datetime.now(ZoneInfo(self.tz)),
                             battery_pct=self.battery_pct(), mains=self.on_mains(), stale=stale)

    def sleep_seconds(self) -> int:
        """Sleep until shortly after the next :00 / :30 so updates stay aligned to the clock."""
        period = int(self.cfg["display"].get("refresh_minutes", 30)) * 60
        now = datetime.now(ZoneInfo(self.tz))
        since_midnight = now.hour * 3600 + now.minute * 60 + now.second
        secs = period - since_midnight % period + 20
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
                    self.dash.battery_mv = int(q["bat"][0])
                body = render.to_epd_bytes(self.dash.image())
                self._send(body, "application/octet-stream",
                           {"X-Sleep-Seconds": self.dash.sleep_seconds()})
            elif url.path == "/preview.png":
                buf = io.BytesIO()
                self.dash.image().save(buf, "PNG")
                self._send(buf.getvalue(), "image/png")
            elif url.path == "/api/weather":
                model, stale = self.dash.model()
                body = json.dumps({"provider": self.dash.provider, "stale": stale, "battery_mv": self.dash.battery_mv,
                                   "weather": model}, indent=2, ensure_ascii=False)
                self._send(body.encode(), "application/json; charset=utf-8")
            elif url.path == "/":
                html = ('<!doctype html><meta name="viewport" content="width=device-width">'
                        '<title>E-Ink Dashboard</title><body style="background:#ccc;text-align:center">'
                        '<img src="/preview.png" style="width:800px;max-width:100%;image-rendering:pixelated;'
                        'border:12px solid #fff;margin-top:24px"><p><a href="/api/weather">JSON</a></p>')
                self._send(html.encode(), "text/html; charset=utf-8")
            else:
                self.send_error(404)
        except Exception:
            log.exception("request failed")
            self.send_error(500)

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
    srv = cfg.get("server", {})
    host, port = srv.get("host", "0.0.0.0"), int(srv.get("port", 8080))
    log.info("listening on http://%s:%d", host, port)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
