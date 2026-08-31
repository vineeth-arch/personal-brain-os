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
    (tmp_path / "photo.jpg").write_bytes(b"x")
    (tmp_path / "sheet.xlsx").write_bytes(b"x")
    (tmp_path / ".syncthing.tmp").write_bytes(b"x")
    assert intake.poll(tmp_path) == []
