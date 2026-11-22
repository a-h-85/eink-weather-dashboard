#!/usr/bin/env python3
"""Render README pictures of the case from the CAD model (no OpenSCAD/Blender needed).

    python3 enclosure/render_images.py [screen.png]

Writes docs/case-front.png (assembled, in the stand, with a dashboard frame on the display)
and docs/case-exploded.png (from the back: lid, battery, electronics). A tiny
z-buffer rasteriser with flat shading - good enough for documentation pictures.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

import case

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent / "docs"
SS = 2  # supersampling


def tris(m, color, xform):
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3].astype(float)
    v = xform(v)
    t = np.asarray(mesh.tri_verts)
    return [(v[t], np.array(color, float))]


def rot_x(deg):
    a = math.radians(deg)
    return np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])


def rot_y(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])


def scene(explode: float):
    y0, z0 = case.stand_seat()
    tilt = rot_x(case.STAND_TILT)

    def case_x(dz=0.0):
        def f(v):
            v = v + np.array([-case.W / 2, 0, dz])
            return v @ tilt.T + np.array([0, z0 + 10 * explode, y0])
        return f

    stand_rot = rot_y(180) @ rot_x(-90)

    def stand_x(v):
        return v @ stand_rot.T + np.array([0, -30 * explode, 0])

    d = case.dummies()
    items = []
    items += tris(case.front_shell(), (0.86, 0.84, 0.79), case_x())
    items += tris(case.lid(), (0.62, 0.66, 0.64), case_x(70 * explode))
    items += tris(case.stand(), (0.50, 0.57, 0.54), stand_x)
    for name, col, dz in [("board", (0.19, 0.37, 0.62), 0), ("glass", (0.85, 0.86, 0.84), 0),
                          ("plugs", (0.12, 0.12, 0.12), 0), ("battery", (0.16, 0.16, 0.16), 40),
                          ("xiao", (0.22, 0.22, 0.22), 40), ("pololu", (0.18, 0.49, 0.31), 40)]:
        items += tris(d[name], col, case_x(dz * explode))
    # the display picture: quad in case coords, slightly in front of the glass
    cx, cy, w, h = case.W / 2 + case.WIN_DX, case.H / 2 + case.WIN_DY, case.AA_W, case.AA_H
    z = case.FRONT - 0.05
    quad = np.array([[cx + w / 2, cy + h / 2, z], [cx - w / 2, cy + h / 2, z],
                     [cx - w / 2, cy - h / 2, z], [cx + w / 2, cy - h / 2, z]])  # TL, TR, BR, BL seen from the front
    return items, case_x()(quad)


def look_at(eye, target, up=(0, 1, 0)):
    eye, target, up = map(lambda a: np.array(a, float), (eye, target, up))
    f = target - eye; f /= np.linalg.norm(f)
    r = np.cross(f, up); r /= np.linalg.norm(r)
    u = np.cross(r, f)
    return eye, np.stack([r, u, f])


def render(explode, eye, target, size, screen_img=None, fov=28):
    """Z-buffer rasteriser with flat shading; the display picture is texture-mapped."""
    W, H = size[0] * SS, size[1] * SS
    items, quad = scene(explode)
    eye, R = look_at(eye, target)
    focal = (H / 2) / math.tan(math.radians(fov / 2))
    light = np.array([-0.45, 0.75, -0.5]); light /= np.linalg.norm(light)
    img = np.full((H, W, 3), 255.0)
    zbuf = np.full((H, W), np.inf)

    def project(p):
        c = (p - eye) @ R.T
        return np.stack([W / 2 + focal * c[..., 0] / c[..., 2], H / 2 - focal * c[..., 1] / c[..., 2]], -1), c[..., 2]

    def raster(xy, z, shade_fn):
        x0, y0 = np.floor(xy.min(0)).astype(int)
        x1, y1 = np.ceil(xy.max(0)).astype(int)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, W - 1), min(y1, H - 1)
        if x0 > x1 or y0 > y1:
            return
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        (ax, ay), (bx, by), (cx, cy) = xy
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-9:
            return
        w0 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den
        w1 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            return
        iz = w0 / z[0] + w1 / z[1] + w2 / z[2]          # perspective-correct depth
        depth = 1 / iz
        sub = zbuf[y0:y1 + 1, x0:x1 + 1]
        win = inside & (depth < sub)
        if not win.any():
            return
        sub[win] = depth[win]
        img[y0:y1 + 1, x0:x1 + 1][win] = shade_fn(w0, w1, w2, iz)[win]

    for tri, col in items:
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
        facing = np.einsum("ij,ij->i", n, eye - tri.mean(axis=1)) > 0
        shade = 0.55 + 0.45 * np.clip(n @ light, 0, 1)
        xy, depth = project(tri)
        for k in np.nonzero(facing & (depth.min(axis=1) > 1))[0]:
            c = np.array(col * 255 * shade[k])
            raster(xy[k], depth[k], lambda w0, w1, w2, iz, c=c: np.broadcast_to(c, w0.shape + (3,)))

    if screen_img is not None:
        nq = np.cross(quad[3] - quad[0], quad[1] - quad[0])   # outward normal of the display
        if np.dot(nq, eye - quad.mean(0)) > 0:             # only when the display faces the camera
            tex = np.asarray(screen_img.convert("RGB"), float)
            th, tw = tex.shape[:2]
            uv = np.array([[0, 0], [tw - 1, 0], [tw - 1, th - 1], [0, th - 1]], float)
            qxy, qz = project(quad)
            for i, j, k in ((0, 1, 2), (0, 2, 3)):
                zz = qz[[i, j, k]]
                uvs = uv[[i, j, k]]

                def tex_fn(w0, w1, w2, iz, zz=zz, uvs=uvs):
                    u = (w0 * uvs[0, 0] / zz[0] + w1 * uvs[1, 0] / zz[1] + w2 * uvs[2, 0] / zz[2]) / iz
                    v = (w0 * uvs[0, 1] / zz[0] + w1 * uvs[1, 1] / zz[1] + w2 * uvs[2, 1] / zz[2]) / iz
                    return tex[np.clip(v, 0, th - 1).astype(int), np.clip(u, 0, tw - 1).astype(int)]
                raster(qxy[[i, j, k]], zz - 0.02, tex_fn)

    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).resize(size, Image.LANCZOS)
    bbox = Image.eval(out.convert("L"), lambda v: 255 - v).point(lambda v: 255 if v > 8 else 0).getbbox()
    if bbox:
        m = 24
        out = out.crop((max(0, bbox[0] - m), max(0, bbox[1] - m), min(size[0], bbox[2] + m), min(size[1], bbox[3] + m)))
    return out


def perspective_coeffs(dst, src):
    """Coefficients for Image.PERSPECTIVE mapping output (dst) points to input (src) points."""
    a = []
    for (x, y), (u, v) in zip(dst, src):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    b = np.array([c for p in src for c in p], float)
    return np.linalg.solve(np.array(a, float), b).tolist()


def main():
    screen = Image.open(sys.argv[1]) if len(sys.argv) > 1 else Image.open(DOCS / "screenshot.png")
    target = (0, 48, 28)
    render(0.0, (-170, 135, -290), target, (1400, 1100), screen).save(DOCS / "case-front.png")
    render(1.0, (360, 230, 280), (0, 45, 55), (1400, 1100), screen).save(DOCS / "case-exploded.png")
    print("wrote", DOCS / "case-front.png", DOCS / "case-exploded.png")


if __name__ == "__main__":
    main()
