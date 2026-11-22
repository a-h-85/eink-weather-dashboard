"""Fetch the forecast from Open-Meteo (free, no API key) and boil it down to
exactly what the dashboard shows."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

API_URL = "https://api.open-meteo.com/v1/forecast"


def fetch_raw(lat: float, lon: float, tz: str) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": tz,
        "forecast_days": 2,
        "current": "temperature_2m,relative_humidity_2m,weather_code,is_day",
        "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code,is_day",
    }
    url = API_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "eink-weather-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


# How "significant" a WMO weather code is, used to pick one icon for a 3h block.
def severity(code: int) -> int:
    if code >= 95:
        return 8  # thunderstorm
    if 71 <= code <= 77 or code in (85, 86, 89):
        return 7  # snow / hail
    if 61 <= code <= 68 or 80 <= code <= 82:
        return 6  # rain / showers
    if 51 <= code <= 57:
        return 5  # drizzle
    if code in (45, 48):
        return 4  # fog
    return code  # 0..3: clear .. overcast


# When does a forecast hour get a rain/snow icon? Probability and expected amount together:
RAIN_MIN_PROB = 15       # below this: never
RAIN_HEAVY_MM = 1.0      # this much expected: show it even if the probability is modest (showers)
RAIN_PROB = 30           # usual case: at least this likely ...
RAIN_MIN_MM = 0.1        # ... and at least a measurable amount
RAIN_SURE_PROB = 60      # this likely: show it even if only a trace is expected
STORM_PROB = 30          # thunderstorms: probability only (they can be fairly dry)


def icon_code(code: int, prob, mm=None) -> int:
    """Forecast hour: keep a precipitation code only if it is likely and/or substantial enough,
    otherwise show it as overcast. prob in %, mm = expected amount for that hour (None = unknown)."""
    if severity(code) < 5:
        return code
    p = prob if prob is not None else 100   # unknown probability: decide on the amount alone
    m = mm if mm is not None else RAIN_MIN_MM
    if code >= 95:
        return code if p >= STORM_PROB else 3
    if p < RAIN_MIN_PROB:
        return 3
    if m >= RAIN_HEAVY_MM or (p >= RAIN_PROB and m >= RAIN_MIN_MM) or p >= RAIN_SURE_PROB:
        return code
    return 3


def day_summary(rows: list[tuple[datetime, float | None, float, int, float]],
                since: datetime | None = None) -> dict:
    """Boil one calendar day of hourly (time, temp, rain %, icon code, rain mm) rows down to one tile.

    - min / max: the whole day (00-24), so the morning low stays visible in the evening
    - rain %, rain amount (sum) and icon use the same hours: from `since` on (today: the rest of the day; tomorrow: all)
    - icon: precipitation in any of those hours wins (most severe; only counted if icon_code keeps it),
      otherwise the most common sky during their daytime part (08-19). When no daytime hours
      are left (today, evening) the night hours decide and the icon gets a moon.
    """
    temps = [r[1] for r in rows if r[1] is not None]
    rest = [r for r in rows if since is None or r[0] >= since] or rows[-1:]
    daytime = [r for r in rest if 8 <= r[0].hour <= 19]
    precip = [r[3] for r in rest if severity(r[3]) >= 5]
    sky = [r[3] for r in (daytime or rest)]
    if precip:
        code = max(precip, key=lambda c: (severity(c), c))
    elif sky:
        code = Counter(sky).most_common(1)[0][0]
    else:
        code = 3
    return {
        "tmin": min(temps) if temps else None,
        "tmax": max(temps) if temps else None,
        "rain": max((r[2] for r in rest), default=0),
        "mm": sum(r[4] for r in rest),
        "code": code,
        "is_day": bool(daytime) or not rest,
    }


def _num(v, default=0):
    return default if v is None else v


def summarize(raw: dict, tz: str, now: datetime | None = None) -> dict:
    """Turn the Open-Meteo response into the dashboard model.

    Rolling window: three 3-hour blocks starting at the next full hour,
    e.g. at 14:10 -> 15-18, 18-21, 21-24.
    """
    now = now or datetime.now(ZoneInfo(tz))
    now_local = now.replace(tzinfo=None)

    h = raw["hourly"]
    times = [datetime.fromisoformat(t) for t in h["time"]]
    index = {t: i for i, t in enumerate(times)}

    this_hour = now_local.replace(minute=0, second=0, microsecond=0)
    cur_i = index.get(this_hour, 0)

    def mm(t: datetime) -> float:
        """Rain amount during hour t..t+1 (Open-Meteo stores the sum of the preceding hour)."""
        i = index.get(t + timedelta(hours=1))
        return _num(h["precipitation"][i]) if i is not None else 0

    c = raw["current"]
    current = {
        "temp": c["temperature_2m"],
        "humidity": c["relative_humidity_2m"],
        "rain": _num(h["precipitation_probability"][cur_i]),
        "mm": mm(this_hour),
        "code": c["weather_code"],
        "is_day": bool(c["is_day"]),
    }

    blocks = []
    start = this_hour + timedelta(hours=1)
    for b in range(3):
        b_start = start + timedelta(hours=3 * b)
        idx = [index[t] for t in (b_start + timedelta(hours=k) for k in range(3)) if t in index]
        if not idx:
            continue
        temps = [h["temperature_2m"][i] for i in idx if h["temperature_2m"][i] is not None]
        codes = [icon_code(h["weather_code"][i], h["precipitation_probability"][i], h["precipitation"][i])
                 for i in idx if h["weather_code"][i] is not None]
        mid = idx[len(idx) // 2]
        blocks.append({
            "start": b_start.strftime("%H"),
            "end": (b_start + timedelta(hours=3)).strftime("%H"),
            "temp": sum(temps) / len(temps) if temps else None,
            "rain": max(_num(h["precipitation_probability"][i]) for i in idx),
            "mm": sum(mm(b_start + timedelta(hours=k)) for k in range(3)),
            "code": max(codes, key=lambda x: (severity(x), x)) if codes else 0,
            "is_day": bool(h["is_day"][mid]),
        })

    def day(date, since=None) -> dict:
        rows = [(t, h["temperature_2m"][i], _num(h["precipitation_probability"][i]),
                 icon_code(h["weather_code"][i], h["precipitation_probability"][i], h["precipitation"][i]),
                 mm(t))
                for t, i in index.items() if t.date() == date and h["weather_code"][i] is not None]
        return day_summary(sorted(rows), since)

    today = day(now_local.date(), since=this_hour)
    tomorrow = day((now_local + timedelta(days=1)).date())

    return {"now": now.isoformat(timespec="minutes"), "current": current,
            "blocks": blocks, "today": today, "tomorrow": tomorrow}
