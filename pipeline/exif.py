"""Photo capture date from JPEG EXIF — struct only, no Pillow (CLAUDE.md §7).
Never raises: any malformed input returns None."""
from __future__ import annotations

import struct
from datetime import datetime
from pathlib import Path

_MAX_READ = 128 * 1024
_MAX_ENTRIES = 512


def _ifd_tags(buf: bytes, off: int, e: str) -> dict[int, tuple[int, int, int]]:
    """{tag: (type, count, value-field offset)} for one IFD, bounds-checked."""
    (n,) = struct.unpack_from(e + "H", buf, off)
    if n > _MAX_ENTRIES:
        return {}
    out = {}
    for i in range(n):
        pos = off + 2 + 12 * i
        tag, typ, cnt = struct.unpack_from(e + "HHI", buf, pos)
        out[tag] = (typ, cnt, pos + 8)
    return out


def _ascii(buf: bytes, e: str, entry) -> str:
    _, cnt, vpos = entry
    start = vpos if cnt <= 4 else struct.unpack_from(e + "I", buf, vpos)[0]
    if start + cnt > len(buf):
        raise ValueError
    return buf[start:start + cnt].split(b"\0")[0].decode("ascii")


def _tiff_date(tiff: bytes) -> datetime | None:
    e = "<" if tiff[:2] == b"II" else ">" if tiff[:2] == b"MM" else None
    if e is None:
        return None
    (ifd0,) = struct.unpack_from(e + "I", tiff, 4)
    tags = _ifd_tags(tiff, ifd0, e)
    if 0x8769 in tags:
        (sub,) = struct.unpack_from(e + "I", tiff, tags[0x8769][2])
        tags = {**tags, **_ifd_tags(tiff, sub, e)}
    entry = tags.get(0x9003) or tags.get(0x0132)
    if not entry:
        return None
    d = datetime.strptime(_ascii(tiff, e, entry), "%Y:%m:%d %H:%M:%S")
    return d if 2000 <= d.year and d <= datetime.now() else None


def capture_date(path: Path) -> datetime | None:
    try:
        with open(path, "rb") as f:
            buf = f.read(_MAX_READ)
        if buf[:2] != b"\xff\xd8":
            return None
        pos = 2
        while pos + 4 <= len(buf) and buf[pos] == 0xFF:
            marker, size = struct.unpack_from(">BH", buf, pos + 1)
            if marker == 0xE1 and buf[pos + 4:pos + 10] == b"Exif\0\0":
                return _tiff_date(buf[pos + 10:pos + 2 + size])
            if marker == 0xDA or size < 2:
                return None
            pos += 2 + size
    except Exception:  # noqa: BLE001 — contract: never raises
        pass
    return None
