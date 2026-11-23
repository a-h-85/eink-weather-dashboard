"""Battery level for the footer.

NiMH cells sit on a flat voltage plateau for most of their life: between "full" and "half
empty" the voltage moves by only 50-80 mV per cell, less than the ESP32's ADC error. So for
NiMH the level is **counted**, not read: the ESP32 reports how long it was awake (failed
WiFi attempts included), the server adds the sleep current, and

    level = 1 - used mAh / capacity

The voltage still guards the end of the discharge, where NiMH drops steeply, and detects
fresh batteries (a jump up). Li-ion and alkaline cells (`type = "linear"`) have a sloped
discharge curve, so for them the smoothed voltage is mapped linearly.

State lives in .battery-history.json next to the server and survives restarts.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from pathlib import Path

log = logging.getLogger("eink")

MAINS_BELOW_MV = 1000   # reported voltage below this = no battery (USB power / no divider)
WINDOW = 6              # readings (3 h at 30 min) for the smoothed voltage
SWAP_JUMP_MV = 150      # smoothed voltage rising by this much = fresh batteries
GUARD_V_CELL = 1.15     # below this NiMH is nearly empty; trust the voltage from here on

# NiMH (e.g. Eneloop) at a light load: volts per cell -> remaining %. Only used near empty.
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


class Battery:
    def __init__(self, cfg: dict, path: Path):
        self.cfg = cfg
        self.path = path
        self.type = cfg.get("type", "nimh")
        self.cells = int(cfg.get("cells", 3))
        self.capacity = float(cfg.get("capacity_mah", 1900))
        self.active_ma = float(cfg.get("active_ma", 75))        # average while awake (WiFi + refresh)
        self.sleep_ua = float(cfg.get("sleep_ua", 80))          # ESP32 + regulator + divider asleep
        self.fallback_awake_s = float(cfg.get("fallback_awake_s", 6))  # firmware without awake=
        self.last_mv: int | None = None
        self.hist: deque[int] = deque(maxlen=WINDOW)
        self.used_mah = 0.0
        self.since: float | None = None          # when these batteries went in
        self.last_report: float | None = None
        self.rssi: int | None = None
        self.last_awake_ms: int | None = None
        self.failed_wakes = 0                    # failed wake-ups since these batteries went in
        self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self):
        try:
            s = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        self.last_mv = s.get("last")
        self.hist.extend(s.get("history", []))
        self.used_mah = float(s.get("used_mah", 0.0))
        self.since = s.get("since")
        self.last_report = s.get("last_report")
        self.rssi = s.get("rssi")
        self.last_awake_ms = s.get("last_awake_ms")
        self.failed_wakes = int(s.get("failed_wakes", 0))

    def _save(self):
        state = {"last": self.last_mv, "history": list(self.hist), "used_mah": round(self.used_mah, 3),
                 "since": self.since, "last_report": self.last_report, "rssi": self.rssi,
                 "last_awake_ms": self.last_awake_ms, "failed_wakes": self.failed_wakes}
        try:
            self.path.write_text(json.dumps(state))
        except OSError as e:
            log.warning("could not save battery state: %s", e)

    # ------------------------------------------------------------------ updates
    def reset(self, now: float | None = None, reason: str = "new batteries"):
        now = now or time.time()
        self.used_mah, self.since, self.failed_wakes = 0.0, now, 0
        self.hist.clear()
        log.info("battery counter reset (%s)", reason)
        self._save()

    def report(self, mv: int, awake_ms: int | None = None, wakes: int | None = None,
               rssi: int | None = None, now: float | None = None):
        now = now or time.time()
        if mv < MAINS_BELOW_MV:                      # on USB: no battery drain to count
            self.last_mv, self.last_report = mv, now
            self._save()
            return
        # Fresh batteries show up as a clear voltage jump. After a USB phase (flashing) the same,
        # partly used cells may come back, so that alone is no reason to start over.
        prev = self.smoothed()
        if self.since is None or (prev is not None and mv > prev + SWAP_JUMP_MV):
            self.reset(now, "first report" if self.since is None else "voltage jumped up: new batteries")
        # sleep since the previous report, plus every wake-up the ESP32 reported
        if self.last_report is not None and self.last_report < now and not self.on_mains():
            self.used_mah += (now - self.last_report) / 3600 * self.sleep_ua / 1000
        awake_s = awake_ms / 1000 if awake_ms is not None else self.fallback_awake_s
        self.used_mah += awake_s / 3600 * self.active_ma
        if wakes:
            self.failed_wakes += max(0, wakes - 1)
        self.hist.append(mv)
        self.last_mv, self.last_report, self.rssi, self.last_awake_ms = mv, now, rssi, awake_ms
        self._save()

    # ------------------------------------------------------------------ readings
    def on_mains(self) -> bool:
        return self.last_mv is not None and self.last_mv < MAINS_BELOW_MV

    def smoothed(self) -> int | None:
        """Highest of the last readings: the ADC sometimes reads low, the voltage never rises."""
        return max(self.hist) if self.hist else None

    def pct(self) -> int | None:
        if self.last_mv is None or self.on_mains():
            return None
        mv = self.smoothed() or self.last_mv
        if self.type == "nimh":
            counted = max(0, min(100, round(100 * (1 - self.used_mah / self.capacity))))
            v_cell = mv / 1000 / self.cells
            return min(counted, nimh_pct(v_cell)) if v_cell < GUARD_V_CELL else counted
        lo, hi = self.cfg.get("empty_mv", 3300), self.cfg.get("full_mv", 4150)
        return max(0, min(100, round((mv - lo) * 100 / (hi - lo))))

    def info(self) -> dict:
        days = (self.last_report - self.since) / 86400 if self.since and self.last_report else None
        per_day = self.used_mah / days if days and days > 0.5 else None
        left = (self.capacity - self.used_mah) / per_day if per_day else None
        return {"percent": self.pct(), "mains": self.on_mains(), "last_mv": self.last_mv,
                "smoothed_mv": self.smoothed(), "used_mah": round(self.used_mah, 1),
                "capacity_mah": self.capacity, "days_in_use": round(days, 2) if days else None,
                "mah_per_day": round(per_day, 1) if per_day else None,
                "days_left": round(left) if left else None, "failed_wakes": self.failed_wakes,
                "rssi_dbm": self.rssi, "last_awake_ms": self.last_awake_ms}
