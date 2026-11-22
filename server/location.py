"""Turn `place = "Hamburg"` into coordinates and a timezone (Open-Meteo geocoding, free, no key).

The [location] section of config.toml can be as short as

    [location]
    place = "Hamburg"

Optional keys: `country` (ISO code such as "DE", to pick the right town when the name exists
in several countries), `name` (text in the footer), or `latitude` / `longitude` / `timezone`
to skip the lookup entirely. A successful lookup is cached next to the server, so the Pi only
asks once.

    python3 server.py --find "Springfield"      # list matches to choose from
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://geocoding-api.open-meteo.com/v1/search"
log = logging.getLogger("eink")


def search(place: str, country: str | None = None, count: int = 5) -> list[dict]:
    params = {"name": place, "count": count, "language": "en", "format": "json"}
    if country:
        params["countryCode"] = country.upper()
    req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}",
                                 headers={"User-Agent": "eink-weather-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp).get("results") or []


def describe(r: dict) -> str:
    where = ", ".join(x for x in (r.get("admin1"), r.get("country")) if x)
    return (f"{r['name']} ({where})  lat {r['latitude']:.4f}  lon {r['longitude']:.4f}  "
            f"{r.get('timezone', '?')}  population {r.get('population', '?')}")


def resolve(loc: dict, cache_file: Path) -> dict:
    """Return the [location] dict with latitude, longitude, timezone and name filled in."""
    loc = dict(loc)
    if all(k in loc for k in ("latitude", "longitude", "timezone")):
        loc.setdefault("name", loc.get("place", ""))
        return loc
    place = loc.get("place")
    if not place:
        raise SystemExit("config.toml: set [location] place = \"Your town\" "
                         "(or latitude, longitude and timezone)")
    key = f"{place}|{loc.get('country', '')}".lower()
    cache = {}
    try:
        cache = json.loads(cache_file.read_text())
    except (OSError, ValueError):
        pass
    hit = cache.get(key)
    if hit is None:
        results = search(place, loc.get("country"), count=1)
        if not results:
            raise SystemExit(f"config.toml: no place called {place!r} found. "
                             f"Try `python3 server.py --find {place!r}` or add `country = \"..\"`.")
        r = results[0]
        hit = {"latitude": r["latitude"], "longitude": r["longitude"],
               "timezone": r.get("timezone") or "UTC", "name": r["name"]}
        cache[key] = hit
        try:
            cache_file.write_text(json.dumps(cache, indent=2))
        except OSError as e:
            log.warning("could not cache the location: %s", e)
        log.info("location %r -> %s", place, describe(r))
    for k in ("latitude", "longitude", "timezone"):
        loc.setdefault(k, hit[k])
    loc.setdefault("name", hit["name"])
    return loc
