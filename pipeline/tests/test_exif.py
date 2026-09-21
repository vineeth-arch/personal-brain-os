"""pipeline/exif.py — hand-built minimal JPEGs, no fixture files."""
from __future__ import annotations

import struct
from datetime import datetime

from pipeline import exif


def jpeg(e="<", date=b"2026:03:05 14:07:09\0", tag=0x9003, count=None, sub_off=None):
    """SOI + APP1(Exif) whose IFD0 points at an ExifIFD holding one date tag."""
    p = e
    sub = sub_off if sub_off is not None else 26
    ifd0 = struct.pack(p + "H", 1) + struct.pack(p + "HHII", 0x8769, 4, 1, sub) + struct.pack(p + "I", 0)
    exif_ifd = struct.pack(p + "H", 1) + struct.pack(p + "HHII", tag, 2, len(date), 44) \
        + struct.pack(p + "I", 0)
    if count is not None:
        ifd0 = struct.pack(p + "H", count) + ifd0[2:]
    tiff = (b"II" if e == "<" else b"MM") + struct.pack(p + "HI", 42, 8) + ifd0 + exif_ifd + date
    body = b"Exif\0\0" + tiff
    return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(body) + 2) + body + b"\xff\xd9"


def cap(tmp_path, data):
    f = tmp_path / "x.jpg"
    f.write_bytes(data)
    return exif.capture_date(f)


def test_valid_little_and_big_endian(tmp_path):
    want = datetime(2026, 3, 5, 14, 7, 9)
    assert cap(tmp_path, jpeg("<")) == want
    assert cap(tmp_path, jpeg(">")) == want


def test_ifd0_datetime_fallback(tmp_path):
    # no DateTimeOriginal: DateTime (0x0132) is used instead
    assert cap(tmp_path, jpeg(tag=0x0132)) == datetime(2026, 3, 5, 14, 7, 9)


def test_no_app1_and_png_and_missing(tmp_path):
    assert cap(tmp_path, b"\xff\xd8\xff\xdb\x00\x02\xff\xda\x00\x02") is None
    assert cap(tmp_path, b"\x89PNG\r\n\x1a\n" + b"\0" * 64) is None
    assert exif.capture_date(tmp_path / "nope.jpg") is None


def test_truncated_bogus_offset_huge_count(tmp_path):
    good = jpeg()
    for cut in (4, 12, 20, 40, 60):
        assert cap(tmp_path, good[:cut]) is None
    assert cap(tmp_path, jpeg(sub_off=0xFFFFFF)) is None
    assert cap(tmp_path, jpeg(count=65535)) is None


def test_bad_or_out_of_window_dates(tmp_path):
    assert cap(tmp_path, jpeg(date=b"0000:00:00 00:00:00\0")) is None
    assert cap(tmp_path, jpeg(date=b"1999:01:01 00:00:00\0")) is None
    assert cap(tmp_path, jpeg(date=b"2999:01:01 00:00:00\0")) is None
