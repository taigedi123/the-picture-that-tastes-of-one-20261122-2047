#!/usr/bin/env python3
"""Render one appetising picture: a ripe mango, hedgehog-cut cheek in front.

Pure standard library. Draws into an RGB framebuffer with a tiny hand
rolled rasteriser, then encodes a 1024x1024 PNG with zlib.
"""
import math
import os
import struct
import zlib

W = 1024
H = 1024


def clamp(v):
    if v < 0:
        return 0
    if v > 255:
        return 255
    return int(v)


def mix(a, b, t):
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


class Canvas:
    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.buf = bytearray(w * h * 3)

    def set_px(self, x, y, col):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.buf[i] = clamp(col[0])
            self.buf[i + 1] = clamp(col[1])
            self.buf[i + 2] = clamp(col[2])

    def blend_px(self, x, y, col, alpha):
        if 0 <= x < self.w and 0 <= y < self.h and alpha > 0:
            if alpha >= 1.0:
                self.set_px(x, y, col)
                return
            i = (y * self.w + x) * 3
            inv = 1.0 - alpha
            self.buf[i] = clamp(self.buf[i] * inv + col[0] * alpha)
            self.buf[i + 1] = clamp(self.buf[i + 1] * inv + col[1] * alpha)
            self.buf[i + 2] = clamp(self.buf[i + 2] * inv + col[2] * alpha)

    def fill_background(self):
        # Deep teal backdrop, warm radial glow behind the fruit, vignette.
        cx, cy = 500.0, 470.0
        deep = (9, 42, 38)
        glow = (34, 104, 86)
        warm = (64, 118, 82)
        for y in range(self.h):
            dy = (y - cy) / 620.0
            for x in range(self.w):
                dx = (x - cx) / 620.0
                d = math.sqrt(dx * dx + dy * dy)
                t = max(0.0, 1.0 - d)
                col = mix(deep, glow, t * t * 0.9)
                if y < 430:
                    col = mix(col, warm, (430 - y) / 430.0 * 0.18 * t)
                # vignette: darken far corners
                vx = abs(x - 512) / 512.0
                vy = abs(y - 512) / 512.0
                vig = 1.0 - 0.38 * max(0.0, (vx * vx + vy * vy) - 0.55)
                i = (y * self.w + x) * 3
                self.buf[i] = clamp(col[0] * vig)
                self.buf[i + 1] = clamp(col[1] * vig)
                self.buf[i + 2] = clamp(col[2] * vig)
        # table line: slightly darker lower band with soft edge
        for y in range(700, self.h):
            a = min(0.35, (y - 700) / 220.0 * 0.35)
            for x in range(self.w):
                i = (y * self.w + x) * 3
                self.buf[i] = clamp(self.buf[i] * (1 - a) + 6 * a)
                self.buf[i + 1] = clamp(self.buf[i + 1] * (1 - a) + 30 * a)
                self.buf[i + 2] = clamp(self.buf[i + 2] * (1 - a) + 26 * a)

    def ellipse(self, cx, cy, rx, ry, col_fn, alpha=1.0):
        """Filled ellipse; col_fn(nx, ny) -> colour, nx/ny in -1..1."""
        x0 = max(0, int(cx - rx - 1))
        x1 = min(self.w - 1, int(cx + rx + 1))
        y0 = max(0, int(cy - ry - 1))
        y1 = min(self.h - 1, int(cy + ry + 1))
        for y in range(y0, y1 + 1):
            ny = (y - cy) / ry
            span = math.sqrt(max(0.0, 1.0 - ny * ny))
            xa = int(cx - rx * span)
            xb = int(cx + rx * span)
            for x in range(max(0, xa), min(self.w - 1, xb) + 1):
                nx = (x - cx) / rx
                # soft edge: fade alpha in the outer 1.5% ring
                d = nx * nx + ny * ny
                a = alpha
                if d > 0.94:
                    a = alpha * max(0.0, (1.0 - d) / 0.06)
                self.blend_px(x, y, col_fn(nx, ny), a)

    def rrect(self, x0, y0, x1, y1, rad, col_fn, alpha=1.0):
        """Filled rounded rectangle; col_fn(u, v) with u, v in 0..1."""
        x0i = max(0, int(x0))
        x1i = min(self.w - 1, int(x1))
        y0i = max(0, int(y0))
        y1i = min(self.h - 1, int(y1))
        w = max(1.0, x1 - x0)
        h = max(1.0, y1 - y0)
        for y in range(y0i, y1i + 1):
            for x in range(x0i, x1i + 1):
                # distance outside the inner rectangle, for corner rounding
                qx = max(x0 + rad - x, 0, x - (x1 - rad))
                qy = max(y0 + rad - y, 0, y - (y1 - rad))
                if qx * qx + qy * qy > rad * rad:
                    continue
                edge = min(x - x0, x1 - x, y - y0, y1 - y)
                a = alpha if edge >= 1.5 else alpha * max(0.0, edge / 1.5)
                self.blend_px(x, y, col_fn((x - x0) / w, (y - y0) / h), a)

    def poly(self, pts, col, alpha=1.0):
        """Filled convex-ish polygon via scanlines."""
        ys = [p[1] for p in pts]
        y0 = max(0, int(min(ys)))
        y1 = min(self.h - 1, int(max(ys)) + 1)
        n = len(pts)
        for y in range(y0, y1 + 1):
            nodes = []
            for i in range(n):
                ax, ay = pts[i]
                bx, by = pts[(i + 1) % n]
                if (ay <= y < by) or (by <= y < ay):
                    nodes.append(ax + (y - ay) / (by - ay) * (bx - ax))
            nodes.sort()
            for k in range(0, len(nodes) - 1, 2):
                xa = max(0, int(nodes[k]))
                xb = min(self.w - 1, int(nodes[k + 1]))
                for x in range(xa, xb + 1):
                    self.blend_px(x, y, col, alpha)


def mango_skin(nx, ny, dark=0.0):
    """Skin gradient: green at top-left, gold middle, red blush bottom-right."""
    t = (nx * 0.55 + ny * 0.75 + 1.3) / 2.6
    t = min(1.0, max(0.0, t))
    if t < 0.45:
        col = mix((74, 128, 52), (196, 168, 44), t / 0.45)
    elif t < 0.75:
        col = mix((196, 168, 44), (232, 138, 40), (t - 0.45) / 0.30)
    else:
        col = mix((232, 138, 40), (196, 62, 34), (t - 0.75) / 0.25)
    shade = 1.0 - 0.28 * max(0.0, ny) - dark
    light = 1.0 + 0.16 * max(0.0, -nx - ny * 0.4)
    f = shade * light
    return (col[0] * f, col[1] * f, col[2] * f)


def draw_leaf(cv, cx, cy, length, angle, col_dark, col_light):
    ca, sa = math.cos(angle), math.sin(angle)
    pts_top = []
    pts_bot = []
    for i in range(25):
        t = i / 24.0
        wdt = math.sin(t * math.pi) * length * 0.20
        px = cx + ca * length * t - sa * wdt
        py = cy + sa * length * t + ca * wdt
        pts_top.append((px, py))
        px2 = cx + ca * length * t + sa * wdt
        py2 = cy + sa * length * t - ca * wdt
        pts_bot.append((px2, py2))
    cv.poly(pts_top + pts_bot[::-1], col_dark, 0.96)
    # central vein highlight
    for i in range(40):
        t = i / 39.0
        px = int(cx + ca * length * t)
        py = int(cy + sa * length * t)
        cv.blend_px(px, py, col_light, 0.55)
        cv.blend_px(px + 1, py, col_light, 0.3)


def draw_cube(cv, cx, cy, s, dome):
    """One hedgehog cube: bright top face, warmer left side, deep right side."""
    lift = dome * 30.0
    cy -= lift
    half = s * 0.5
    depth = s * 0.34
    fh = half * 0.78  # front face half-height (taller = cubes stand proud)
    # drop shadow in the cut beneath the cube
    cv.ellipse(cx + 3, cy + fh + 5, half * 1.02, 7, lambda nx, ny: (146, 62, 4), alpha=0.55)
    # right side face
    cv.poly([(cx + half, cy - fh), (cx + half + depth, cy - fh + depth * 0.6),
             (cx + half + depth, cy + fh + depth * 0.6), (cx + half, cy + fh)],
            (178, 82, 10), 1.0)
    # front face: juicy orange gradient, lighter toward top-left
    def front(u, v):
        base = mix((255, 182, 36), (238, 118, 14), v)
        base = mix(base, (255, 210, 92), (1 - u) * 0.35)
        return base
    cv.rrect(cx - half, cy - fh, cx + half, cy + fh, s * 0.15, front)
    # top face: pale glossy gold, brightest near the light
    cv.poly([(cx - half, cy - fh), (cx - half + depth, cy - fh - depth * 0.6),
             (cx + half + depth, cy - fh - depth * 0.6), (cx + half, cy - fh)],
            (255, 204, 74), 1.0)
    # repaint top with slight gradient via a translucent overlay on its left
    cv.poly([(cx - half, cy - fh), (cx - half + depth, cy - fh - depth * 0.6),
             (cx - half + depth + s * 0.3, cy - fh - depth * 0.6), (cx - half + s * 0.3, cy - fh)],
            (255, 236, 158), 0.5)
    # specular streak on the front face
    cv.ellipse(cx - half * 0.35, cy - fh * 0.4, s * 0.20, s * 0.10,
               lambda nx, ny: (255, 242, 196), alpha=0.4)


def droplet(cv, x, y, r):
    cv.ellipse(x, y, r, r * 0.78, lambda nx, ny: mix((255, 226, 140), (250, 180, 60), ny * 0.5 + 0.5), alpha=0.85)
    cv.ellipse(x - r * 0.3, y - r * 0.3, r * 0.32, r * 0.22, lambda nx, ny: (255, 255, 244), alpha=0.95)


def build():
    cv = Canvas(W, H)
    cv.fill_background()

    # soft shadow under everything
    cv.ellipse(505, 800, 330, 62, lambda nx, ny: (4, 22, 19), alpha=0.55)
    cv.ellipse(505, 792, 250, 44, lambda nx, ny: (3, 16, 14), alpha=0.45)

    # ---- second whole mango, behind and to the right, lower contrast ----
    cv.ellipse(672, 408, 188, 168, lambda nx, ny: mango_skin(nx, ny, dark=0.13))
    # its soft sheen
    cv.ellipse(606, 344, 66, 32, lambda nx, ny: (255, 244, 200), alpha=0.22)
    # stem nub
    cv.ellipse(672, 246, 13, 9, lambda nx, ny: (74, 56, 30), alpha=0.9)

    # ---- hero cheek: skin rim ----
    cv.ellipse(446, 570, 292, 246, lambda nx, ny: mango_skin(nx, ny))
    # pale flesh ring just inside the skin
    cv.ellipse(446, 562, 272, 226,
               lambda nx, ny: mix((255, 200, 80), (250, 166, 42), ny * 0.5 + 0.5))
    # inner flesh bed the cubes sit on
    cv.ellipse(446, 556, 246, 200,
               lambda nx, ny: mix((252, 170, 40), (236, 126, 18), (nx * nx + ny * ny) * 0.4 + ny * 0.25))

    # ---- hedgehog cubes on a grid clipped by the flesh ellipse ----
    step = 70
    rows = range(-3, 4)
    cols = range(-4, 5)
    cubes = []
    for r in rows:
        for c in cols:
            ox = c * step + (step * 0.5 if r % 2 else 0)
            oy = r * step * 0.86
            nx = ox / 246.0
            ny = oy / 200.0
            d2 = nx * nx + ny * ny
            if d2 > 0.80:
                continue
            dome = max(0.0, 1.0 - d2)
            size = 58 - d2 * 10
            cubes.append((446 + ox, 556 + oy, size, dome))
    cubes.sort(key=lambda item: item[1])  # back rows first
    for (x, y, s, dome) in cubes:
        draw_cube(cv, x, y, s, dome)

    # juice droplets scattered over the cubes
    drops = [(398, 500, 9), (486, 462, 7), (520, 556, 10), (356, 580, 7),
             (452, 640, 8), (566, 500, 6), (430, 420, 6), (300, 520, 6),
             (596, 596, 7), (486, 606, 6)]
    for (x, y, r) in drops:
        droplet(cv, x, y, r)

    # a couple of loose cubes resting on the shadow beside the cheek
    draw_cube(cv, 776, 756, 56, 0.0)
    draw_cube(cv, 846, 740, 44, 0.0)
    droplet(cv, 770, 738, 7)

    # ---- leaf tucked at the base, front left ----
    draw_leaf(cv, 236, 742, 190, -0.5, (26, 92, 40), (120, 190, 110))
    draw_leaf(cv, 268, 766, 150, -0.16, (34, 110, 48), (140, 205, 120))

    return cv


def encode_png(cv):
    raw = bytearray()
    stride = cv.w * 3
    for y in range(cv.h):
        raw.append(0)
        raw += cv.buf[y * stride:(y + 1) * stride]
    comp = zlib.compress(bytes(raw), 6)

    def chunk(tag, data):
        out = struct.pack(">I", len(data)) + tag + data
        out += struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return out

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", cv.w, cv.h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", comp)
    png += chunk(b"IEND", b"")
    return png


def main():
    cv = build()
    data = encode_png(cv)
    out = os.environ.get("ENTRY_OUT", "/output/entry.png")
    try:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "wb") as fh:
            fh.write(data)
    except OSError:
        fallback = os.path.join(".", "output", "entry.png")
        os.makedirs(os.path.dirname(fallback), exist_ok=True)
        with open(fallback, "wb") as fh:
            fh.write(data)
    print("wrote", len(data), "bytes")


if __name__ == "__main__":
    main()
