"""Weather icons drawn with Pillow primitives (no image files, any size, 1-bit friendly).

All functions draw black (0) on white (255) into an "L" image.
Codes are WMO weather codes as returned by Open-Meteo.
"""

from __future__ import annotations

import math

BLACK, WHITE = 0, 255


def _w(s: float) -> int:
    return max(2, round(s / 18))


def _cloud(d, ox, oy, s, w):
    """Outlined cloud. Unit shape spans x .12-.92, y .20-.80 of a box of size s at (ox, oy)."""
    circles = [(0.33, 0.56, 0.19), (0.57, 0.45, 0.25), (0.77, 0.60, 0.16)]
    rect = (0.12, 0.52, 0.92, 0.80)

    def shape(inset, fill):
        for cx, cy, r in circles:
            rr = r * s - inset
            d.ellipse((ox + cx * s - rr, oy + cy * s - rr, ox + cx * s + rr, oy + cy * s + rr), fill=fill)
        x0, y0, x1, y1 = rect
        d.rounded_rectangle((ox + x0 * s + inset, oy + y0 * s + inset, ox + x1 * s - inset, oy + y1 * s - inset),
                            radius=max(1, 0.14 * s - inset), fill=fill)

    shape(0, BLACK)
    shape(w, WHITE)


def _sun(d, cx, cy, r, w):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=BLACK, width=w)
    for k in range(8):
        a = k * math.pi / 4
        d.line((cx + math.cos(a) * r * 1.4, cy + math.sin(a) * r * 1.4,
                cx + math.cos(a) * r * 1.85, cy + math.sin(a) * r * 1.85), fill=BLACK, width=w)


def _moon(d, cx, cy, r, w):
    """Filled crescent."""
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=BLACK)
    bx, by, br = cx + r * 0.45, cy - r * 0.35, r * 0.8
    d.ellipse((bx - br, by - br, bx + br, by + br), fill=WHITE)


def _sky(d, x, y, s, w, is_day, cx=0.5, cy=0.5, r=0.22):
    (_sun if is_day else _moon)(d, x + cx * s, y + cy * s, r * s, w)


def _rain(d, x, y, s, w, n=4, length=0.2):
    for k in range(n):
        u = 0.30 + k * (0.48 / max(1, n - 1))
        d.line((x + u * s, y + 0.68 * s, x + (u - 0.07) * s, y + (0.68 + length) * s), fill=BLACK, width=w)


def _snow(d, x, y, s, w):
    for u, v in ((0.32, 0.76), (0.55, 0.86), (0.76, 0.74)):
        r = 0.07 * s
        cx, cy = x + u * s, y + v * s
        for k in range(3):
            a = k * math.pi / 3 + math.pi / 2
            d.line((cx - math.cos(a) * r, cy - math.sin(a) * r, cx + math.cos(a) * r, cy + math.sin(a) * r),
                   fill=BLACK, width=max(1, w - 1))


def _bolt(d, x, y, s):
    pts = [(0.55, 0.58), (0.40, 0.80), (0.52, 0.80), (0.44, 0.98), (0.66, 0.72), (0.54, 0.72), (0.62, 0.58)]
    d.polygon([(x + u * s, y + v * s) for u, v in pts], fill=BLACK)


def draw_weather(d, code: int, is_day: bool, x: float, y: float, s: float):
    """Draw the icon for a WMO code into the box (x, y, x+s, y+s)."""
    w = _w(s)
    up = -0.13 * s  # lift the cloud when something falls out of it

    if code == 0:
        _sky(d, x, y, s, w, is_day)
    elif code in (1, 2):
        _sky(d, x, y, s, w, is_day, cx=0.36, cy=0.34, r=0.15 if code == 2 else 0.17)
        _cloud(d, x + 0.20 * s, y + 0.20 * s, s * 0.8, w)
    elif code == 3:
        _cloud(d, x, y, s, w)
    elif code in (45, 48):
        _cloud(d, x + 0.05 * s, y + up, s * 0.9, w)
        for k, (u0, u1) in enumerate(((0.18, 0.82), (0.10, 0.74), (0.22, 0.90))):
            v = 0.70 + k * 0.11
            d.line((x + u0 * s, y + v * s, x + u1 * s, y + v * s), fill=BLACK, width=w)
    elif 51 <= code <= 57:
        _cloud(d, x + 0.05 * s, y + up, s * 0.9, w)
        _rain(d, x, y, s, w, n=3, length=0.1)
    elif 61 <= code <= 67 or 80 <= code <= 82:
        if code >= 80 and s >= 70:  # showers: sun/moon peeking out (only on the big icon)
            _sky(d, x, y, s, w, is_day, cx=0.26, cy=0.20, r=0.11)
        _cloud(d, x + 0.05 * s, y + up, s * 0.9, w)
        _rain(d, x, y, s, w, n=4 if code in (63, 65, 81, 82) else 3)
    elif code == 68:  # sleet: rain and snow
        _cloud(d, x + 0.05 * s, y + up, s * 0.9, w)
        for u, v in ((0.30, 0.68), (0.62, 0.68)):
            d.line((x + u * s, y + v * s, x + (u - 0.06) * s, y + (v + 0.17) * s), fill=BLACK, width=w)
        r = 0.07 * s
        cx, cy = x + 0.46 * s, y + 0.80 * s
        for k in range(3):
            a = k * math.pi / 3 + math.pi / 2
            d.line((cx - math.cos(a) * r, cy - math.sin(a) * r, cx + math.cos(a) * r, cy + math.sin(a) * r),
                   fill=BLACK, width=max(1, w - 1))
    elif code == 89:  # hail: cloud + solid pellets
        _cloud(d, x + 0.05 * s, y + up, s * 0.9, w)
        r = 0.055 * s
        for u, v in ((0.32, 0.74), (0.55, 0.86), (0.76, 0.72), (0.44, 0.94)):
            d.ellipse((x + u * s - r, y + v * s - r, x + u * s + r, y + v * s + r), fill=BLACK)
    elif 71 <= code <= 77 or code in (85, 86):
        _cloud(d, x + 0.05 * s, y + up, s * 0.9, w)
        _snow(d, x, y, s, w)
    elif code >= 95:
        _cloud(d, x + 0.05 * s, y + up, s * 0.9, w)
        _bolt(d, x, y, s)
    else:
        _cloud(d, x, y, s, w)


def draw_drop(d, x, y, s):
    """Humidity drop in box (x, y, s)."""
    r = 0.3 * s
    cx, cy = x + 0.5 * s, y + 0.64 * s
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=BLACK)
    d.polygon([(cx, y + 0.05 * s), (cx - r * 0.95, cy - r * 0.3), (cx + r * 0.95, cy - r * 0.3)], fill=BLACK)


def draw_umbrella(d, x, y, s):
    """Rain-risk umbrella in box (x, y, s)."""
    w = _w(s * 1.6)
    d.pieslice((x + 0.05 * s, y + 0.08 * s, x + 0.95 * s, y + 0.88 * s), 180, 360, fill=BLACK)
    cx = x + 0.5 * s
    d.line((cx, y + 0.45 * s, cx, y + 0.85 * s), fill=BLACK, width=w)
    d.arc((cx - 0.16 * s, y + 0.72 * s, cx + 0.02 * s, y + 0.95 * s), 0, 180, fill=BLACK, width=w)


def draw_battery(d, x, y, w, h, pct):
    d.rectangle((x, y, x + w - 3, y + h), outline=BLACK, width=1)
    d.rectangle((x + w - 3, y + h * 0.3, x + w, y + h * 0.7), fill=BLACK)
    fill_w = max(0, min(1, pct / 100)) * (w - 7)
    if fill_w >= 1:
        d.rectangle((x + 2, y + 2, x + 2 + fill_w, y + h - 2), fill=BLACK)


def draw_plug(d, x, y, w, h):
    """Mains plug lying on its side (same footprint as draw_battery): cable, body, two pins."""
    cy = y + h / 2
    d.line((x, cy, x + w * 0.35, cy), fill=BLACK, width=2)                      # cable
    d.rounded_rectangle((x + w * 0.35, y, x + w * 0.72, y + h), radius=2, fill=BLACK)  # body
    for py in (y + h * 0.25, y + h * 0.75):                                        # pins
        d.line((x + w * 0.72, py, x + w, py), fill=BLACK, width=2)
