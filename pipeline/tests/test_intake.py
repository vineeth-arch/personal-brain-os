"""Pass 11 — intake recognises what phones actually record.

The inbox is source-agnostic, but only extensions in the allow-list are picked
up; anything else is silently ignored, which for a voice memo looks exactly
like the pipeline being broken. iPhone Voice Memos produce .m4a and WhatsApp
voice notes arrive as .opus, so both must route to kind=audio."""
from __future__ import annotations

import pytest

from pipeline import intake


@pytest.mark.parametrize("name", [
    "memo.m4a",        # iPhone Voice Memos / most Android recorders
    "voice.opus",      # WhatsApp / Telegram voice notes
    "note.ogg",        # Android recorders, Signal
    "clip.oga",
    "take.flac",
    "old.mp3",
    "old.wav",
    "old.aac",
    "2026-07-03-0900 voice-note.webm",   # the cockpit's mic button (MediaRecorder)
    "2026-07-03-0900 voice-note.mp4",    # the same button on iOS Safari
])
def test_phone_audio_routes_to_audio(tmp_path, name):
    (tmp_path / name).write_bytes(b"not really audio, intake only reads the name")
    items = intake.poll(tmp_path)
    assert len(items) == 1, f"{name} was ignored by intake"
    assert items[0].kind == "audio"
    assert items[0].source == "voice"


def test_extension_matching_is_case_insensitive(tmp_path):
    """Phones and cloud syncs hand back .M4A often enough to matter."""
    (tmp_path / "MEMO.M4A").write_bytes(b"x")
    items = intake.poll(tmp_path)
    assert len(items) == 1 and items[0].kind == "audio"


def test_unknown_types_are_still_ignored(tmp_path):
    """The allow-list is still a list — sync scratch files must not be
    swallowed into the pipeline as captures."""
    (tmp_path / "notes.xlsx").write_bytes(b"x")
    (tmp_path / "archive.zip").write_bytes(b"x")
    (tmp_path / ".syncthing.tmp").write_bytes(b"x")
    assert intake.poll(tmp_path) == []


@pytest.mark.parametrize("name", ["photo.jpg", "photo.jpeg", "screenshot.png", "photo.webp"])
def test_client_converted_images_route_to_image(tmp_path, name):
    """Pass V2: the client always resizes+converts before the file reaches
    the inbox, so a plain jpg/png/webp here is a first-class capture."""
    (tmp_path / name).write_bytes(b"not really an image, intake only reads the name")
    items = intake.poll(tmp_path)
    assert len(items) == 1, f"{name} was ignored by intake"
    assert items[0].kind == "image"
    assert items[0].source == "manual"


def test_image_extension_matching_is_case_insensitive(tmp_path):
    (tmp_path / "IMG_1234.JPG").write_bytes(b"x")
    items = intake.poll(tmp_path)
    assert len(items) == 1 and items[0].kind == "image"


@pytest.mark.parametrize("name", ["photo.heic", "photo.HEIC", "photo.heif"])
def test_heic_is_recognized_but_unsupported_never_silently_dropped(tmp_path, name):
    """The client (Shortcut/PWA) always converts to JPEG first (CLAUDE.md §7:
    no Pillow, no server HEIC decode) — but a raw HEIC arriving some other
    way (e.g. Syncthing straight from an iPhone camera roll) must be
    QUARANTINED with a clear "convert on your phone" message, not silently
    ignored forever. process_file() (pipeline/watcher.py) is what actually
    quarantines an "image-unsupported" item; this only proves intake sees it."""
    (tmp_path / name).write_bytes(b"x")
    items = intake.poll(tmp_path)
    assert len(items) == 1, f"{name} vanished silently instead of being recognized as unsupported"
    assert items[0].kind == "image-unsupported"


def test_image_filename_metadata_parses_like_audio(tmp_path):
    """Same 'YYYY-MM-DD-HHmm name #tag.ext' convention as every other
    capture — the API's image_capture_path writes filenames this shape."""
    (tmp_path / "2026-07-03-0900 branding-teardown #resource.jpg").write_bytes(b"x")
    items = intake.poll(tmp_path)
    assert len(items) == 1
    item = items[0]
    assert item.kind == "image"
    assert item.name == "branding-teardown"
    assert item.tag == "resource"


# ---- sweep_orphaned_sidecars (Pass V2) ---------------------------------------

def test_sweep_leaves_a_sidecar_whose_image_is_still_present(tmp_path):
    (tmp_path / "2026-07-03-0900 photo.jpg").write_bytes(b"x")
    sidecar = tmp_path / ".2026-07-03-0900 photo.insight"
    sidecar.write_text("a thought", encoding="utf-8")
    intake.sweep_orphaned_sidecars(tmp_path, now=intake.time.time() + 999999)
    assert sidecar.exists()  # the image is still here — not the sweep's job


def test_sweep_leaves_a_fresh_orphaned_sidecar(tmp_path):
    sidecar = tmp_path / ".2026-07-03-0900 photo.insight"
    sidecar.write_text("a thought", encoding="utf-8")
    intake.sweep_orphaned_sidecars(tmp_path)  # now=now, age=0
    assert sidecar.exists()


def test_sweep_removes_a_day_old_orphaned_sidecar(tmp_path):
    sidecar = tmp_path / ".2026-07-03-0900 photo.insight"
    sidecar.write_text("a thought", encoding="utf-8")
    intake.sweep_orphaned_sidecars(tmp_path, now=intake.time.time() + intake.ORPHAN_SIDECAR_AGE_SECONDS + 1)
    assert not sidecar.exists()


def test_sweep_on_a_missing_inbox_does_nothing(tmp_path):
    intake.sweep_orphaned_sidecars(tmp_path / "does-not-exist")  # must not raise
