#!/usr/bin/env python3
"""Generate the PWA icons with the Python standard library only (no Pillow —
the frontend dependency lock extends in spirit to build tooling).

Draws the brand mark: a minimal cockpit dial on deep indigo. A 270-degree
gauge track and hairline ring in tonal indigo steps, with ONE lit element in
electric teal — the active arc, its needle and the hub ring (DESIGNSYSTEM §6).
Every size is rendered natively (never resampled) as opaque RGB with no alpha
channel, which is what iOS wants for a home-screen icon. The maskable variant
scales the dial into the 80% safe circle.

Usage:
    python3 web/scripts/make_icons.py            # write the PNGs
    python3 web/scripts/make_icons.py --check    # verify committed PNGs match
    python3 web/scripts/make_icons.py --ss 1     # fast, unsmoothed preview

--check is an exact byte compare. It relies on libm (cos/atan2/exp), so treat
it as a same-machine guard, not a cross-platform CI gate.
"""
from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

# dark-mode tokens from web/src/index.css (.dark)
BG_IN = (0x28, 0x0A, 0x86)      # --cal-stamp        centre of the radial step
BG_OUT = (0x19, 0x05, 0x50)     # --cal-bg-muted     rim
TRACK = (0x3A, 0x14, 0xBE)      # --cal-bg-emphasis
HAIRLINE = (0x52, 0x33, 0x99)   # --cal-border-emphasis
TEAL = (0x00, 0xFF, 0xCF)       # --cal-brand — the one accent

OUT_DIR = Path(__file__).resolve().parent.parent / "public" / "icons"

# (filename, size, content scale k) — k < 1 pulls the dial inside a safe zone
SPECS = [
    ("icon-192.png", 192, 1.0),
    ("icon-512.png", 512, 1.0),
    ("apple-touch-icon-180.png", 180, 1.0),
    ("icon-maskable-512.png", 512, 0.855),  # 0.445 * 0.855 = 76% dia, inside the 80% circle
]

# Gauge runs clockwise from 225deg (lower-left) over the top to -45deg
# (lower-right). Angles are standard maths: CCW from east, y up.
SWEEP_LO, SWEEP_HI = math.radians(-45.0), math.radians(225.0)
VALUE = 0.68
NEEDLE_ANGLE = math.radians(225.0 - 270.0 * VALUE)
TAU = math.tau


def sd_arc(px, py, r, w, lo, hi):
    """Signed distance to a round-capped arc: radius r, half-width w, covering
    CCW angles lo..hi. Negative inside."""
    th = math.atan2(py, px)
    if (th - lo) % TAU <= (hi - lo):
        return abs(math.hypot(px, py) - r) - w
    return min(
        math.hypot(px - r * math.cos(lo), py - r * math.sin(lo)),
        math.hypot(px - r * math.cos(hi), py - r * math.sin(hi)),
    ) - w


def sd_seg(px, py, ax, ay, bx, by, ra, rb):
    """Signed distance to a tapered capsule from a (radius ra) to b (rb)."""
    dx, dy = bx - ax, by - ay
    h = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - h * dx, py - ay - h * dy) - (ra + (rb - ra) * h)


def over(dst, color, d):
    """Composite `color` over `dst` with analytic edge coverage from distance d."""
    cov = 0.5 - d
    if cov <= 0.0:
        return dst
    cov = min(cov, 1.0)
    return tuple(dst[i] + (color[i] - dst[i]) * cov for i in range(3))


def shade(x, y, s, k):
    """Colour of one sub-sample at pixel-space (x, y) for an s-pixel icon."""
    px, py = x - s / 2, s / 2 - y
    u = s * k  # every dimension below is a fraction of u

    t = min(math.hypot(px, py) / (0.72 * s), 1.0)
    rgb = tuple(BG_IN[i] + (BG_OUT[i] - BG_IN[i]) * t for i in range(3))

    r_track, w_track = 0.415 * u, 0.030 * u
    rgb = over(rgb, TRACK, sd_arc(px, py, r_track, w_track, SWEEP_LO, SWEEP_HI))
    rgb = over(rgb, HAIRLINE, abs(math.hypot(px, py) - 0.285 * u) - 0.008 * u)
    for i in range(5):  # ticks sit inside the track, at 0/.25/.5/.75/1 of the sweep
        a = math.radians(225.0 - 270.0 * i / 4)
        c, sn = math.cos(a), math.sin(a)
        rgb = over(rgb, HAIRLINE,
                   sd_seg(px, py, 0.320 * u * c, 0.320 * u * sn, 0.360 * u * c, 0.360 * u * sn,
                          0.011 * u, 0.011 * u))

    active = sd_arc(px, py, r_track, w_track, NEEDLE_ANGLE, SWEEP_HI)
    glow = 0.32 * math.exp(-max(active, 0.0) / (0.038 * u))
    rgb = tuple(rgb[i] + TEAL[i] * glow for i in range(3))
    rgb = over(rgb, TEAL, active)

    tip = 0.345 * u
    rgb = over(rgb, TEAL, sd_seg(px, py, 0.0, 0.0, tip * math.cos(NEEDLE_ANGLE),
                                 tip * math.sin(NEEDLE_ANGLE), 0.026 * u, 0.007 * u))
    hub = math.hypot(px, py)
    rgb = over(rgb, BG_OUT, hub - 0.055 * u)
    return over(rgb, TEAL, abs(hub - 0.045 * u) - 0.010 * u)


def chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def png_bytes(size: int, k: float, ss: int = 2) -> bytes:
    offs = [(i + 0.5) / ss for i in range(ss)]
    rows = bytearray()
    for y in range(size):
        row = bytearray()
        for x in range(size):
            acc = [0.0, 0.0, 0.0]
            for oy in offs:
                for ox in offs:
                    c = shade(x + ox, y + oy, size, k)
                    acc[0] += c[0]; acc[1] += c[1]; acc[2] += c[2]
            row.extend(min(255, max(0, round(a / (ss * ss)))) for a in acc)
        rows.append(1)  # filter 1 (Sub): the gradient makes left-deltas tiny
        rows.extend((row[i] - (row[i - 3] if i >= 3 else 0)) & 0xFF for i in range(len(row)))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB, no alpha
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def main() -> int:
    check = "--check" in sys.argv
    ss = int(sys.argv[sys.argv.index("--ss") + 1]) if "--ss" in sys.argv else 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ok = True
    for name, size, k in SPECS:
        data = png_bytes(size, k, ss)
        path = OUT_DIR / name
        if check:
            if not path.exists() or path.read_bytes() != data:
                print(f"MISMATCH: {path}")
                ok = False
            else:
                print(f"ok: {path}")
        else:
            path.write_bytes(data)
            print(f"wrote {path} ({len(data)} bytes)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
