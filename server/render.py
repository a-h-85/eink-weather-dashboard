"""Render the dashboard as a 1-bit image sized for the e-paper panel (400x300)."""

from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import icons

W, H = 400, 300
BLACK, WHITE = 0, 255

FONTS = Path(__file__).resolve().parent / "fonts"   # Fira Sans (SIL OFL 1.1), see fonts/OFL.txt
FONT_CANDIDATES = {
    False: [
        str(FONTS / "FiraSans-Regular.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",          # Raspberry Pi OS (fonts-dejavu-core)
        "/System/Library/Fonts/Supplemental/Arial.ttf",             # macOS, for local preview
        "C:/Windows/Fonts/arial.ttf",
    ],
    True: [
        str(FONTS / "FiraSans-SemiBold.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ],
}

TEXT = {
    "de": {
        "days": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
        "months": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
                   "September", "Oktober", "November", "Dezember"],
        "date": "{d}. {m} {y}",
        "now": "Aktuell",
        "today": "Heute",
        "tomorrow": "Morgen",
        "updated": "Stand {t}",
        "stale": "DATEN VERALTET",
        "nodata": "Keine Wetterdaten",
        "block": "{a}–{b}",
    },
    "en": {
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        "months": ["January", "February", "March", "April", "May", "June", "July", "August",
                   "September", "October", "November", "December"],
        "date": "{m} {d}, {y}",
        "now": "Now",
        "today": "Today",
        "tomorrow": "Tomorrow",
        "updated": "Updated {t}",
        "stale": "DATA OUTDATED",
        "nodata": "No weather data",
        "block": "{a}–{b}h",
    },
}

WMO = {
    0: ("Klar", "Clear"), 1: ("Überwiegend klar", "Mainly clear"), 2: ("Teils bewölkt", "Partly cloudy"),
    3: ("Bedeckt", "Overcast"), 45: ("Nebel", "Fog"), 48: ("Raureifnebel", "Rime fog"),
    51: ("Leichter Niesel", "Light drizzle"), 53: ("Nieselregen", "Drizzle"), 55: ("Starker Niesel", "Heavy drizzle"),
    56: ("Gefrierender Niesel", "Freezing drizzle"), 57: ("Gefrierender Niesel", "Freezing drizzle"),
    61: ("Leichter Regen", "Light rain"), 63: ("Regen", "Rain"), 65: ("Starker Regen", "Heavy rain"),
    66: ("Gefrierender Regen", "Freezing rain"), 67: ("Gefrierender Regen", "Freezing rain"),
    68: ("Schneeregen", "Sleet"), 89: ("Hagel", "Hail"),
    71: ("Leichter Schneefall", "Light snow"), 73: ("Schneefall", "Snow"), 75: ("Starker Schneefall", "Heavy snow"),
    77: ("Schneegriesel", "Snow grains"), 80: ("Leichte Schauer", "Light showers"), 81: ("Regenschauer", "Showers"),
    82: ("Heftige Schauer", "Violent showers"), 85: ("Schneeschauer", "Snow showers"),
    86: ("Starke Schneeschauer", "Heavy snow showers"), 95: ("Gewitter", "Thunderstorm"),
    96: ("Gewitter, Hagel", "Thunderstorm, hail"), 99: ("Gewitter, Hagel", "Thunderstorm, hail"),
}


@lru_cache(maxsize=None)
def font(size: int, bold: bool = False):
    for path in FONT_CANDIDATES[bold]:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def fmt_temp(t) -> str:
    if t is None:
        return "–"
    v = round(t)
    if v == 0:
        v = 0  # avoid "-0"
    return f"{v}°".replace("-", "−")


def describe(code: int, lang: str) -> str:
    de, en = WMO.get(code, ("", ""))
    return de if lang == "de" else en


HEAD_Y = 38    # header rule
MID_Y = 160    # rule between "now/today" and the 3h blocks
FOOT_Y = 284   # footer rule
LABEL = 16     # bold size of every section label (Aktuell, Heute, 17–20, Morgen)


def fit(d, text: str, max_w: float, size: int, bold: bool = True, min_size: int = 12):
    """Largest font <= size in which text fits into max_w."""
    while size > min_size and d.textlength(text, font=font(size, bold)) > max_w:
        size -= 1
    return font(size, bold)


def _header(d, now: datetime, t: dict):
    # one size, one baseline: weekday bold on the left, date regular on the right
    # (both shrink together if a long weekday + month don't fit, e.g. "Donnerstag … September")
    y = 27
    day = t["days"][now.weekday()]
    date = t["date"].format(d=now.day, m=t["months"][now.month - 1], y=now.year)
    size = 24
    while size > 16 and (d.textlength(day, font=font(size, True)) + d.textlength(date, font=font(size))
                         > W - 24 - 20):
        size -= 1
    d.text((12, y), day, font=font(size, True), fill=BLACK, anchor="ls")
    d.text((W - 12, y), date, font=font(size), fill=BLACK, anchor="rs")
    d.line((0, HEAD_Y, W, HEAD_Y), fill=BLACK, width=2)


def fmt_mm(v, lang: str) -> str:
    v = v or 0
    if v < 0.05:
        txt = "0"
    elif v < 10:
        txt = f"{v:.1f}"
    else:
        txt = f"{round(v)}"
    return (txt.replace(".", ",") if lang == "de" else txt) + " mm"


def _rain(d, cx, y, pct, mm, lang, size, max_w, gap=None):
    """Centred on cx: umbrella + "nn%" (probability) and drop + "n,n mm" (amount).

    Shrinks the font until both fit into max_w.
    """
    a, b = f"{round(pct)}%", fmt_mm(mm, lang)
    while True:
        f = font(size)
        u = round(size * 0.9)
        g = gap if gap is not None else round(size * 0.6)
        wa, wb = d.textlength(a, font=f), d.textlength(b, font=f)
        total = u + 2 + wa + g + u + wb
        if total <= max_w or size <= 10:
            break
        size -= 1
    x = cx - total / 2
    icons.draw_umbrella(d, x, y - u / 2 - 1, u)
    d.text((x + u + 2, y), a, font=f, fill=BLACK, anchor="lm")
    x += u + 2 + wa + g
    icons.draw_drop(d, x - 1, y - u / 2 - 1, u)
    d.text((x + u - 1, y), b, font=f, fill=BLACK, anchor="lm")


def _line(d, cx, y, draw_icon, txt, size):
    """Small icon + text, centred on cx, vertically centred on y."""
    f = font(size)
    u = round(size * 0.9)
    tw = d.textlength(txt, font=f)
    x = cx - (u + 3 + tw) / 2
    draw_icon(d, x, y - u / 2 - 1, u)
    d.text((x + u + 3, y), txt, font=f, fill=BLACK, anchor="lm")


def _hilo_line(d, cx, y, tmax, tmin, max_w, size, sep="/"):
    """One line centred on cx: high (bold) / low (regular), e.g. "8° / 4°".
    Shrinks the font until it fits max_w."""
    hi, lo = fmt_temp(tmax), fmt_temp(tmin)
    while True:
        fb, fr = font(size, True), font(size)
        gap = round(size * 0.35)
        w_hi, w_lo = d.textlength(hi, font=fb), d.textlength(lo, font=fr)
        w_sep = d.textlength(sep, font=fr) + 2 * gap if sep else round(size * 0.6)
        total = w_hi + w_sep + w_lo
        if total <= max_w or size <= 12:
            break
        size -= 1
    x = cx - total / 2
    d.text((x, y), hi, font=fb, fill=BLACK, anchor="lm")
    if sep:
        d.text((x + w_hi + gap, y), sep, font=fr, fill=BLACK, anchor="lm")
    d.text((x + w_hi + w_sep, y), lo, font=fr, fill=BLACK, anchor="lm")


def _now_today(d, model: dict, t: dict, lang: str):
    """Middle band: two equal halves, icon left, numbers right, rain risk on a shared line."""
    half = W // 2
    d.line((half, HEAD_Y + 8, half, MID_Y - 8), fill=BLACK, width=1)
    label_y, icon_y, icon_s, rain_y = HEAD_Y + 16, HEAD_Y + 26, 66, MID_Y - 17

    c, td = model["current"], model.get("today")
    for i, label in enumerate((t["now"], t["today"])):
        x0 = i * half
        d.text((x0 + half // 2, label_y), label, font=font(LABEL, True), fill=BLACK, anchor="mm")
        cx = x0 + 150               # centre of the number column (x0+104 .. x0+196)
        if i == 0:
            icons.draw_weather(d, c["code"], c["is_day"], x0 + 20, icon_y, icon_s)
            txt = fmt_temp(c["temp"])
            d.text((cx, icon_y + icon_s / 2 - 4), txt, font=fit(d, txt, 92, 50), fill=BLACK, anchor="mm")
            _rain(d, x0 + half // 2, rain_y, c["rain"], c.get("mm"), lang, 18, half - 20, gap=16)
        elif td:
            icons.draw_weather(d, td["code"], td.get("is_day", True), x0 + 20, icon_y, icon_s)
            ty = icon_y + icon_s / 2 - 4
            for k, (val, bold) in enumerate(((td["tmax"], True), (td["tmin"], False))):  # high above low
                txt = fmt_temp(val)
                d.text((cx, ty - 17 + k * 34), txt, font=fit(d, txt, 92, 32, bold), fill=BLACK, anchor="mm")
            _rain(d, x0 + half // 2, rain_y, td["rain"], td.get("mm"), lang, 18, half - 20, gap=16)


def render(model: dict | None, *, lang: str = "de", location: str = "", now: datetime,
           battery_pct: int | None = None, mains: bool = False, stale: bool = False,
           source: str = "") -> Image.Image:
    t = TEXT.get(lang, TEXT["en"])
    img = Image.new("L", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    _header(d, now, t)

    if model is None:
        d.text((W // 2, 150), t["nodata"], font=font(28, True), fill=BLACK, anchor="mm")
    else:
        _now_today(d, model, t, lang)
        d.line((0, MID_Y, W, MID_Y), fill=BLACK, width=2)

        # --- 3 rolling 3h blocks + tomorrow -------------------------------
        cols = [(t["block"].format(a=b["start"], b=b["end"]), b["code"], b["is_day"],
                 fmt_temp(b["temp"]), b["rain"], b.get("mm")) for b in model["blocks"]]
        tm = model["tomorrow"]
        cols.append((t["tomorrow"], tm["code"], True, (tm["tmax"], tm["tmin"]), tm["rain"], tm.get("mm")))

        cw = W // 4
        for i, (label, code, is_day, temp, rain, mm) in enumerate(cols):
            x = i * cw
            cx = x + cw // 2
            if i:
                if i == 3:
                    d.line((x, MID_Y, x, FOOT_Y - 2), fill=BLACK, width=3)
                else:
                    d.line((x, MID_Y + 8, x, FOOT_Y - 10), fill=BLACK, width=1)
            d.text((cx, MID_Y + 14), label, font=font(LABEL, True), fill=BLACK, anchor="mm")
            icons.draw_weather(d, code, is_day, cx - 23, MID_Y + 22, 46)
            if isinstance(temp, tuple):
                _hilo_line(d, cx, 244, *temp, cw - 8, 18)
            else:
                d.text((cx, 244), temp, font=font(20, True), fill=BLACK, anchor="mm")
            _line(d, cx, 261, icons.draw_umbrella, f"{round(rain)}%", 14)
            _line(d, cx, 275, icons.draw_drop, fmt_mm(mm, lang), 14)

    # --- footer --------------------------------------------------------------
    d.line((0, FOOT_Y, W, FOOT_Y), fill=BLACK, width=1)
    parts = [location] if location else []
    if model is not None:
        if source:
            parts.append(source)
        parts.append(t["updated"].format(t=now.strftime("%H:%M")))
    rx = W - 8
    if battery_pct is not None:
        txt = f"{battery_pct}%"
        d.text((rx, 292), txt, font=font(12), fill=BLACK, anchor="rm")
        rx -= d.textlength(txt, font=font(12)) + 4
        icons.draw_battery(d, rx - 22, 287, 22, 10, battery_pct)
        rx -= 30
    elif mains:
        icons.draw_plug(d, rx - 22, 287, 22, 10)
        rx -= 30
    if stale:
        f = font(12, True)
        tw = d.textlength(t["stale"], font=f)
        d.rectangle((rx - tw - 8, 285, rx, 300), fill=BLACK)
        d.text((rx - 4, 292), t["stale"], font=f, fill=WHITE, anchor="rm")
        rx -= tw + 8
    # left text gets what is left: shrink a little, then drop the location if still too long
    room = rx - 8 - 8
    size = 12
    while True:
        left = "  ·  ".join(parts)
        if d.textlength(left, font=font(size)) <= room or not parts:
            break
        if size > 10:
            size -= 1
        else:
            parts = parts[1:]
            size = 12
    d.text((8, 292), left, font=font(size), fill=BLACK, anchor="lm")

    # hard threshold -> crisp 1-bit image
    return img.point(lambda p: 255 if p > 140 else 0).convert("1", dither=Image.Dither.NONE)


def to_epd_bytes(img: Image.Image) -> bytes:
    """Pack as 1 bit/pixel, row-major, MSB first, 1 = black (what the ESP32 sketch expects)."""
    raw = img.tobytes()  # mode "1": 1 = white
    return bytes(b ^ 0xFF for b in raw)
