#!/usr/bin/env python3
"""Generate the PWA icons with the Python standard library only (no Pillow —
the frontend dependency lock extends in spirit to build tooling).

Draws the brand mark: a deep-indigo (#1F006E) square with a centered electric
teal (#00FFCF) filled circle — the "one lit element" reduced to an icon. The
maskable variant shrinks the circle into the 80% safe zone.

Usage:
    python3 web/scripts/make_icons.py           # write the three PNGs
    python3 web/scripts/make_icons.py --check   # verify committed PNGs match
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

INDIGO = (0x1F, 0x00, 0x6E)
TEAL = (0x00, 0xFF, 0xCF)

OUT_DIR = Path(__file__).resolve().parent.parent / "public" / "icons"

SPECS = [
    ("icon-192.png", 192, 0.32),
    ("icon-512.png", 512, 0.32),
    ("icon-maskable-512.png", 512, 0.26),  # inside the maskable safe zone
]


def png_bytes(size: int, radius_frac: float) -> bytes:
    cx = cy = (size - 1) / 2
    r = size * radius_frac
    r2 = r * r
    rows = bytearray()
    for y in range(size):
        rows.append(0)  # no filter
        for x in range(size):
            dx, dy = x - cx, y - cy
            d2 = dx * dx + dy * dy
            if d2 <= r2:
                px = TEAL
            else:
                # 1px anti-alias ring: blend by distance to the circle edge
                dist = d2**0.5 - r
                if dist < 1.0:
                    t = dist  # 0 at edge → teal, 1 → indigo
                    px = tuple(round(TEAL[i] + (INDIGO[i] - TEAL[i]) * t) for i in range(3))
                else:
                    px = INDIGO
            rows.extend(px)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    idat = zlib.compress(bytes(rows), 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def main() -> int:
    check = "--check" in sys.argv
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ok = True
    for name, size, radius in SPECS:
        data = png_bytes(size, radius)
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
