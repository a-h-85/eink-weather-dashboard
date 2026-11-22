"""Deutscher Wetterdienst (DWD) data via Bright Sky (https://brightsky.dev): free JSON API, no key.

- Current conditions: real measurements from the nearest DWD station (updated every 10 min).
- Forecast: DWD MOSMIX, the DWD's statistically corrected point forecast for that station,
  including hourly precipitation probability.

Data source: Deutscher Wetterdienst (CC BY 4.0), so show "DWD" on the display.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from weather import day_summary, icon_code, include_now, severity

API = "https://api.brightsky.dev"


def _get(path: str, params: dict) -> dict:
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "eink-weather-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def fetch_raw(lat: float, lon: float, tz: str) -> dict:
    today = datetime.now(ZoneInfo(tz)).date()
    common = {"lat": lat, "lon": lon, "tz": tz}
    return {
        "lat": lat, "lon": lon,
        "current": _get("/current_weather", common),
        "forecast": _get("/weather", {**common, "date": today.isoformat(),
                                      "last_date": (today + timedelta(days=2)).isoformat()}),
    }


def sun_up(lat: float, lon: float, when: datetime) -> bool:
    """Is the sun above the horizon (NOAA approximation, good to a few minutes)?"""
    t = when.astimezone(timezone.utc)
    hour = t.hour + t.minute / 60
    g = 2 * math.pi / 365 * (t.timetuple().tm_yday - 1 + (hour - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    ha = math.radians((hour * 60 + eqtime + 4 * lon) / 4 - 180)
    la = math.radians(lat)
    cos_zen = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    elevation = 90 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zen))))
    return elevation > -0.833


def to_wmo(icon: str | None, condition: str | None, precip_mm: float | None) -> int:
    """Map Bright Sky icon/condition (+ amount for intensity) to a WMO weather code."""
    mm = precip_mm or 0
    cond = condition or ""
    icon = icon or ""
    if cond == "thunderstorm" or icon == "thunderstorm":
        return 95
    if cond == "hail" or icon == "hail":
        return 89
    if cond == "snow" or icon == "snow":
        return 71 if mm < 0.5 else 73 if mm < 2 else 75
    if cond == "sleet" or icon == "sleet":
        return 68
    if cond == "rain" or icon == "rain":
        return 61 if mm < 1 else 63 if mm < 4 else 65
    if cond == "fog" or icon == "fog":
        return 45
    if icon.startswith("clear"):
        return 0
    if icon.startswith("partly-cloudy"):
        return 2
    return 3  # cloudy, wind, unknown


def summarize(raw: dict, tz: str, now: datetime | None = None) -> dict:
    zone = ZoneInfo(tz)
    now = now or datetime.now(zone)
    lat, lon = raw["lat"], raw["lon"]

    hours = {}
    for w in raw["forecast"]["weather"]:
        t = datetime.fromisoformat(w["timestamp"]).astimezone(zone)
        hours[t.replace(tzinfo=None)] = w

    def prob(w):
        return w.get("precipitation_probability") or 0

    def code(w):  # forecast hour: rain icon only if likely and/or substantial enough
        return icon_code(to_wmo(w.get("icon"), w.get("condition"), w.get("precipitation")),
                         w.get("precipitation_probability"), w.get("precipitation"))

    def is_day(t: datetime):
        return sun_up(lat, lon, t.replace(tzinfo=zone))

    this_hour = now.replace(minute=0, second=0, microsecond=0, tzinfo=None)

    def mm(t: datetime) -> float:
        """Rain amount during hour t..t+1 (Bright Sky stores the sum of the preceding hour)."""
        w = hours.get(t + timedelta(hours=1))
        return (w.get("precipitation") or 0) if w else 0

    c = raw["current"]["weather"]
    current = {
        "temp": c.get("temperature"),
        "humidity": c.get("relative_humidity") or 0,
        "rain": prob(hours[this_hour]) if this_hour in hours else 0,
        # measured at the station during the last 60 min; forecast for this hour if missing
        "mm": c["precipitation_60"] if c.get("precipitation_60") is not None else mm(this_hour),
        "code": to_wmo(c.get("icon"), c.get("condition"), c.get("precipitation_60")),
        "is_day": sun_up(lat, lon, now),
    }

    blocks = []
    start = this_hour + timedelta(hours=1)
    for b in range(3):
        b_start = start + timedelta(hours=3 * b)
        ws = [hours[t] for t in (b_start + timedelta(hours=k) for k in range(3)) if t in hours]
        if not ws:
            continue
        temps = [w["temperature"] for w in ws if w.get("temperature") is not None]
        codes = [code(w) for w in ws]
        blocks.append({
            "start": b_start.strftime("%H"),
            "end": (b_start + timedelta(hours=3)).strftime("%H"),
            "temp": sum(temps) / len(temps) if temps else None,
            "rain": max(prob(w) for w in ws),
            "mm": sum(mm(b_start + timedelta(hours=k)) for k in range(3)),
            "code": max(codes, key=lambda x: (severity(x), x)),
            "is_day": is_day(b_start + timedelta(hours=1)),
        })

    def day(date, since=None) -> dict:
        rows = [(t, w.get("temperature"), prob(w), code(w), mm(t)) for t, w in sorted(hours.items()) if t.date() == date]
        return day_summary(rows, since)

    today = day(now.date(), since=this_hour)
    include_now(today, current["temp"])
    tomorrow = day((now + timedelta(days=1)).date())

    return {"now": now.isoformat(timespec="minutes"), "current": current,
            "blocks": blocks, "today": today, "tomorrow": tomorrow}
