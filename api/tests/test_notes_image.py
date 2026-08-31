"""Pass 13 — unit tests for api/notes.py's image-capture helpers, run
directly (no HTTP) so the size cap and format detection are deterministic
and don't depend on socket/streaming behavior."""
from __future__ import annotations

import json

import pytest

from api import notes

JPEG_MAGIC = b"\xff\xd8\xff\xe0\x00\x10JFIF" + bytes(range(256)) * 4
PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP_MAGIC = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32


@pytest.mark.parametrize("data,expected", [
    (JPEG_MAGIC, (".jpg", "image/jpeg")),
    (PNG_MAGIC, (".png", "image/png")),
    (WEBP_MAGIC, (".webp", "image/webp")),
    (b"not an image at all", None),
    (b"", None),
    (b"RIFF\x00\x00\x00\x00AVI ", None),  # a RIFF container that ISN'T webp
])
def test_detect_image_type(data, expected):
    assert notes.detect_image_type(data) == expected


def test_capture_image_rejects_oversized_bytes(tmp_path):
    huge = JPEG_MAGIC + b"\x00" * notes.MAX_IMAGE_BYTES
    with pytest.raises(ValueError, match="over the"):
        notes.capture_image(tmp_path, huge, text="", tag=None, ocr="")
    assert list(tmp_path.iterdir()) == []


def test_capture_image_rejects_unrecognized_format(tmp_path):
    with pytest.raises(ValueError, match="unrecognized"):
        notes.capture_image(tmp_path, b"plain text, not a photo", text="", tag=None, ocr="")
    assert list(tmp_path.iterdir()) == []


def test_capture_image_no_text_uses_photo_fallback_name(tmp_path):
    notes.capture_image(tmp_path, JPEG_MAGIC, text="   ", tag=None, ocr="")
    images = list(tmp_path.glob("*.jpg"))
    assert len(images) == 1
    assert "photo" in images[0].name


def test_capture_image_tag_appears_in_filename_for_free_routing(tmp_path):
    notes.capture_image(tmp_path, JPEG_MAGIC, text="my thought", tag="todo", ocr="")
    images = list(tmp_path.glob("*.jpg"))
    assert "#todo" in images[0].name


def test_capture_image_sidecar_written_before_image(tmp_path, monkeypatch):
    """The watcher must never observe an image without its sidecar — verify
    the write ORDER by recording calls to the shared atomic-write helper."""
    order = []
    original = notes._atomic_write_bytes

    def spy(path, data, tmp_dir):
        order.append(path.suffix if path.suffix != ".json" else ".meta.json")
        return original(path, data, tmp_dir)

    monkeypatch.setattr(notes, "_atomic_write_bytes", spy)
    notes.capture_image(tmp_path, JPEG_MAGIC, text="order test", tag=None, ocr="")
    assert order == [".meta.json", ".jpg"]


def test_capture_image_downscale_skipped_when_ffmpeg_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(notes.shutil, "which", lambda name: None)
    big = JPEG_MAGIC + b"\x00" * (notes.DOWNSCALE_THRESHOLD_BYTES + 1000)
    notes.capture_image(tmp_path, big, text="", tag=None, ocr="")
    images = list(tmp_path.glob("*.jpg"))
    assert images[0].stat().st_size == len(big)  # kept the original — no ffmpeg to resize with


def test_capture_image_downscale_failure_keeps_original(tmp_path, monkeypatch):
    """ffmpeg present but failing (bad codec, timeout, whatever) must never
    fail the capture — the original bytes are kept."""
    monkeypatch.setattr(notes.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def boom(*a, **k):
        raise notes.subprocess.CalledProcessError(1, "ffmpeg")

    monkeypatch.setattr(notes.subprocess, "run", boom)
    big = JPEG_MAGIC + b"\x00" * (notes.DOWNSCALE_THRESHOLD_BYTES + 1000)
    notes.capture_image(tmp_path, big, text="", tag=None, ocr="")
    images = list(tmp_path.glob("*.jpg"))
    assert images[0].stat().st_size == len(big)


def test_capture_image_sidecar_ocr_field_persisted(tmp_path):
    notes.capture_image(tmp_path, JPEG_MAGIC, text="a thought", tag=None, ocr="extracted text here")
    sidecar = json.loads(next(tmp_path.glob("*.meta.json")).read_text())
    assert sidecar["ocr"] == "extracted text here"
    assert sidecar["source"] == "photo"
