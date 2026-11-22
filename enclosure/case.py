#!/usr/bin/env python3
"""Parametric 3D-printable case for the e-ink weather dashboard.

Parts (STL, millimetres):
  case_front.stl  front shell with display window (print face down, no supports)
  case_lid.stl    back lid with battery cradle, hold-down posts, keyholes (print outside down)
  case_stand.stl  desk stand, tilts the case back by STAND_TILT degrees (optional)
Preview-only dummies (not for printing) go to preview/: display board, battery holder, XIAO, Pololu.

Everything sits behind the display: 3xAA holder, Pololu, XIAO. Board, battery and cables are
held by the front locators, the lid posts and a bit of foam / velcro.

    python3 -m pip install manifold3d numpy
    python3 case.py            # writes stl/ and preview/

Coordinates: x = width (left -> right, seen from the front), y = height (bottom -> top),
z = depth (0 = front face, growing towards the back).
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
from manifold3d import CrossSection, JoinType, Manifold, set_circular_segments

set_circular_segments(48)
HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------- measure & adjust these
BOARD_W, BOARD_H = 93.5, 78.5      # Pico-ePaper-4.2 driver board outline (Waveshare)
BOARD_T = 3.2                      # e-paper glass + PCB (front of panel to back of PCB)
STANDOFF_H = 5.0                   # copper standoffs on the back corners of the board (measured)
AA_W, AA_H = 84.8, 63.6            # pixel area (Waveshare); sits inside the visible white area
# Visible (white) area of the glass, measured from the board edges, seen from the front.
# The wide border is on the flex-cable side (bottom).
VIS_LEFT, VIS_RIGHT, VIS_TOP, VIS_BOTTOM = 2.0, 2.0, 2.0, 10.0
WIN_INSET = 0.5                    # frame overlaps the visible area by this much per side
WIN_W = BOARD_W - VIS_LEFT - VIS_RIGHT - 2 * WIN_INSET
WIN_H = BOARD_H - VIS_TOP - VIS_BOTTOM - 2 * WIN_INSET
WIN_DX = (VIS_LEFT - VIS_RIGHT) / 2      # window centre vs. board centre
WIN_DY = (VIS_BOTTOM - VIS_TOP) / 2      # (+y = up)

BAT_L, BAT_W, BAT_T = 68.0, 49.0, 20.0   # 3xAA holder, side by side (measured)

WALL = 2.0          # side walls
FRONT = 2.0         # front face (window chamfered 45 deg)
LID_T = 2.5         # back lid
MARGIN_X = 12.0     # room left/right of the board (8-pin cable leaves sideways)
MARGIN_Y = 5.0      # room above/below the board
CASE_DEPTH = 45.0   # total outside depth incl. lid (room for headers, dupont plugs, 3xAA, cables)
RADIUS = 4.0        # outer corner radius
SCREW_HOLE = 2.5    # bosses: M3 self-tapping screw (or 2.9 mm sheet-metal screw)
SCREW_CLEAR = 3.4   # lid holes
STAND_TILT = 15.0   # desk stand: lean back by this many degrees

# ---------------------------------------------------------------- derived
IN_W = BOARD_W + 2 * MARGIN_X
IN_H = BOARD_H + 2 * MARGIN_Y
W = IN_W + 2 * WALL
H = IN_H + 2 * WALL
SHELL_D = CASE_DEPTH - LID_T                      # front shell depth (open back)
DEPTH_BEHIND = SHELL_D - FRONT - BOARD_T          # free depth behind the PCB
D = SHELL_D + LID_T                               # total depth
BX0, BY0 = (W - BOARD_W) / 2, (H - BOARD_H) / 2   # board lower-left corner
BOSS_R = 3.6
BOSS_AT = WALL + BOSS_R + 0.4                     # boss centre distance from the outer edge


def box(x, y, z, sx, sy, sz) -> Manifold:
    return Manifold.cube((sx, sy, sz)).translate((x, y, z))


def rounded_plate(w, h, r, t, inset=0.0) -> Manifold:
    """Rounded rectangle w x h (corner radius r) extruded by t, lower-left at (inset, inset)."""
    cs = CrossSection.square((w - 2 * r, h - 2 * r)).translate((r, r)).offset(r, JoinType.Round)
    return cs.extrude(t).translate((inset, inset, 0))


def boss_positions():
    return [(BOSS_AT, BOSS_AT), (W - BOSS_AT, BOSS_AT), (BOSS_AT, H - BOSS_AT), (W - BOSS_AT, H - BOSS_AT)]


# ---------------------------------------------------------------- front shell
def front_shell() -> Manifold:
    outer = rounded_plate(W, H, RADIUS, SHELL_D)
    inner = rounded_plate(IN_W, IN_H, max(0.5, RADIUS - WALL), SHELL_D, inset=WALL).translate((0, 0, FRONT))
    shell = outer - inner

    # display window, chamfered 45 deg towards the front so the frame casts no shadow
    cx, cy = W / 2 + WIN_DX, H / 2 + WIN_DY
    ww, wh = WIN_W, WIN_H
    back = box(cx - ww / 2, cy - wh / 2, FRONT - 0.01, ww, wh, 0.02)
    front = box(cx - ww / 2 - FRONT, cy - wh / 2 - FRONT, -0.01, ww + 2 * FRONT, wh + 2 * FRONT, 0.02)
    shell -= Manifold.batch_hull([back, front])
    shell -= box(cx - ww / 2, cy - wh / 2, FRONT - 0.05, ww, wh, 1.0)

    # L-shaped corner locators that keep the board centred on the window
    c, t, L, hgt = 0.3, 1.2, 7.0, 3.0
    for (x, y, sx, sy) in [(BX0, BY0, -1, -1), (BX0 + BOARD_W, BY0, 1, -1),
                           (BX0, BY0 + BOARD_H, -1, 1), (BX0 + BOARD_W, BY0 + BOARD_H, 1, 1)]:
        # outer corner of the L (sx/sy = outward direction of this board corner)
        cx0 = x + sx * (c + t)
        cy0 = y + sy * (c + t)
        vx = min(cx0, x + sx * c)                      # vertical leg, along the board's side edge
        vy = cy0 if sy < 0 else cy0 - L
        hx = cx0 if sx < 0 else cx0 - L                # horizontal leg, along the top/bottom edge
        hy = min(cy0, y + sy * c)
        shell += box(vx, vy, FRONT, t, L, hgt) + box(hx, hy, FRONT, L, t, hgt)

    # screw bosses in the four corners (blind holes from the back)
    for (x, y) in boss_positions():
        boss = Manifold.cylinder(SHELL_D - FRONT, BOSS_R).translate((x, y, FRONT))
        hole = Manifold.cylinder(SHELL_D - FRONT - 3, SCREW_HOLE / 2).translate((x, y, FRONT + 3.01))
        shell = shell + boss - hole
    return shell


# ---------------------------------------------------------------- back lid
def lid() -> Manifold:
    z0 = SHELL_D                       # lid inner face
    plate = rounded_plate(W, H, RADIUS, LID_T).translate((0, 0, z0))

    # locating lip that drops into the shell opening
    lip_o = rounded_plate(IN_W - 0.6, IN_H - 0.6, max(0.5, RADIUS - WALL), 2.0, inset=WALL + 0.3)
    lip_i = rounded_plate(IN_W - 3.6, IN_H - 3.6, max(0.5, RADIUS - WALL - 1.5), 2.0, inset=WALL + 1.8)
    lip = (lip_o - lip_i).translate((0, 0, z0 - 2.0))
    for (x, y) in boss_positions():   # notch the lip around the bosses
        lip -= Manifold.cylinder(2.2, BOSS_R + 0.6).translate((x, y, z0 - 2.1))
    part = plate + lip

    # countersunk screw holes
    for (x, y) in boss_positions():
        part -= Manifold.cylinder(LID_T + 0.2, SCREW_CLEAR / 2).translate((x, y, z0 - 0.1))
        part -= Manifold.cylinder(1.6, SCREW_CLEAR / 2, 3.2).translate((x, y, z0 + LID_T - 1.59))

    # hold-down posts: press the board's back corners (via the standoffs) against the front.
    # Tip ends 1.5 mm short of the standoffs: stick a foam pad on each tip.
    tip_z = FRONT + BOARD_T + STANDOFF_H + 1.5
    for (x, y) in [(BX0 + 4, BY0 + BOARD_H - 4), (BX0 + BOARD_W - 4, BY0 + BOARD_H - 4), (BX0 + 4, BY0 + 4)]:
        part += Manifold.cylinder(z0 - tip_z, 3.0).translate((x, y, tip_z))

    # battery cradle (seen from the front: bottom right, below the board's flex connector)
    cl, wt, ch = 0.6, 1.6, 7.0
    bx1 = W - WALL - 22.0
    bx0 = bx1 - BAT_L - 2 * cl
    by0 = WALL + 2.0
    by1 = by0 + BAT_W + 2 * cl
    outer = box(bx0 - wt, by0 - wt, z0 - ch, bx1 - bx0 + 2 * wt, by1 - by0 + 2 * wt, ch)
    inner = box(bx0, by0, z0 - ch - 0.1, bx1 - bx0, by1 - by0, ch + 0.1)
    cradle = outer - inner
    cradle -= box(bx0 + 10, by0 - wt - 0.1, z0 - ch - 0.1, bx1 - bx0 - 20, by1 - by0 + 2 * wt + 0.2, ch - 2)  # finger gaps
    part += cradle

    # two keyholes for wall mounting (screw head <= 7 mm)
    for x in (W * 0.3, W * 0.7):
        y = H - 22
        part -= Manifold.cylinder(LID_T + 4, 3.6).translate((x, y, z0 - 2))
        part -= box(x - 1.9, y, z0 - 2, 3.8, 7.0, LID_T + 4)
    return part


# ---------------------------------------------------------------- desk stand
STAND_FLOOR = 3.0                  # stand: floor thickness under the case's back edge
STAND_LIP = 8.0                    # stand: front lip height above the case's front edge
STAND_FRONT_Y = 9.0                # stand: front lip thickness


def stand_seat():
    """Where the case's bottom-front edge sits in the stand (stand coords: y = front->back, z = up).
    Leaning back by STAND_TILT lowers the case's back edge by D*sin(tilt), so the slot floor slopes
    down towards the back and the front edge sits that much higher."""
    import math
    t = math.radians(STAND_TILT)
    return STAND_FRONT_Y, STAND_FLOOR + (D + 0.8) * math.sin(t)


def stand() -> Manifold:
    import math
    t = math.radians(STAND_TILT)
    y0, z0 = stand_seat()
    sw, sh = 80.0, z0 + 15.0                               # back support reaches 15 mm above the seat
    back_top = y0 + (D + 0.8) * math.cos(t) + (sh - STAND_FLOOR) * math.tan(t)
    sd = back_top + 7.0                                    # >= 7 mm behind the slot at the top
    body = rounded_plate(sw, sd, 3.0, sh)                  # x = width, y = front -> back, z = up
    slot = box(-1, 0, 0, sw + 2, D + 0.8, 80).rotate((-STAND_TILT, 0, 0)).translate((0, y0, z0))
    body -= slot
    # lower the front lip so it never covers the display window
    body -= box(-1, -1, z0 + STAND_LIP, sw + 2, y0 + 1 + (sh - z0 + 2) * math.tan(t), 80)
    return body.translate((-sw / 2, 0, 0))


# ---------------------------------------------------------------- preview dummies (not printed)
def dummies() -> dict[str, Manifold]:
    zb = FRONT
    board = box(BX0, BY0, zb + 1.2, BOARD_W, BOARD_H, 1.6)                               # PCB
    glass = box(W / 2 - 45.05, BY0 + 0.75, zb, 90.1, 77.0, 1.2)                          # e-paper glass
    active = box(W / 2 + WIN_DX - AA_W / 2, H / 2 + WIN_DY - AA_H / 2, zb - 0.05, AA_W, AA_H, 0.1)
    # Pico headers (seen from the front they are on the left, top) + plugs on pins 38/39
    hdr = box(BX0 + 2, BY0 + BOARD_H - 22, zb + BOARD_T, 62, 5, 8.5) + box(BX0 + 2, BY0 + BOARD_H - 42, zb + BOARD_T, 62, 5, 8.5)
    plugs = box(BX0 + 3, BY0 + BOARD_H - 42, zb + BOARD_T + 8.5, 5.5, 2.6, 14)
    bat = box(W - WALL - 22.0 - BAT_L - 0.6, WALL + 2.6, SHELL_D - BAT_T, BAT_L, BAT_W, BAT_T)
    xiao = box(BX0 + BOARD_W - 27.5, BY0 + BOARD_H - 26, SHELL_D - 6, 17.5, 21, 4.5)
    pololu = box(BX0 + 31, BY0 + 57, SHELL_D - 4, 18, 13, 3)
    return {"board": board + hdr, "glass": glass, "active": active, "plugs": plugs,
            "battery": bat, "xiao": xiao, "pololu": pololu}


# ---------------------------------------------------------------- STL export
def write_stl(m: Manifold, path: Path):
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3].astype(np.float32)
    t = np.asarray(mesh.tri_verts)
    tri = v[t]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, tri
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"e-ink weather dashboard case".ljust(80, b" "))
        f.write(struct.pack("<I", len(t)))
        f.write(rec.tobytes())


def main():
    parts = {"case_front": front_shell(), "case_lid": lid(), "case_stand": stand()}
    for name, m in parts.items():
        assert m.status().name == "NoError", (name, m.status())
        write_stl(m, HERE / "stl" / f"{name}.stl")
        (x0, y0, z0), (x1, y1, z1) = m.bounding_box()[:3], m.bounding_box()[3:]
        print(f"{name:11} {x1 - x0:6.1f} x {y1 - y0:6.1f} x {z1 - z0:5.1f} mm   {m.volume() / 1000:5.1f} cm3")
    for name, m in dummies().items():
        write_stl(m, HERE / "preview" / f"{name}.stl")
    print(f"case outside: {W:.1f} x {H:.1f} x {D:.1f} mm")


if __name__ == "__main__":
    main()
