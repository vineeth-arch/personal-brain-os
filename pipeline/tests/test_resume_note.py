"""Pass E, task E3 — resuming a note whose transcript still has one or more
`[N minutes unintelligible — audio archived]` placeholder gaps. Unlike
`transcribe_long`'s own resume-via-cache path (exercised in
test_transcribe_long.py), `resume_note` operates on an already-written note:
it re-runs transcription against the archived audio and patches only the
placeholders that are now recoverable, leaving everything else byte-identical."""
from __future__ import annotations

import shutil
import wave
from pathlib import Path

import pytest

from pipeline import transcribe as tr
from pipeline.errors import StageError
from pipeline.transcribe import Transcriber

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")

PLACEHOLDER_10 = "[00:10] [10 minutes unintelligible — audio archived]"
PLACEHOLDER_20 = "[00:20] [10 minutes unintelligible — audio archived]"


def _wav(path: Path, seconds: int, rate: int = 1000) -> Path:
    """A real (silent) wav — long enough, at a low sample rate, that ffmpeg's
    10-minute `-segment_time` default actually produces multiple chunks
    without writing a huge fixture file."""
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * rate * seconds)
    return path


class HealsAllTranscriber(Transcriber):
    """Every chunk now transcribes fine — the "problem is fixed" fake."""

    def __init__(self):
        self.calls = 0

    def transcribe(self, audio_path: Path) -> str:
        self.calls += 1
        return f"recovered text {self.calls}"


class HealsExceptCall(Transcriber):
    """Succeeds on every chunk except the given (1-indexed) call, which still
    fails permanently — mirrors FailingChunk's style in
    test_transcribe_long.py."""

    def __init__(self, fail_on: int):
        self.calls = 0
        self.fail_on = fail_on

    def transcribe(self, audio_path: Path) -> str:
        self.calls += 1
        if self.calls == self.fail_on:
            raise StageError("Could not transcribe the recording.",
                             "OpenAI rejected the segment.", "Check the key.")
        return f"recovered text {self.calls}"


class ExplodingTranscriber(Transcriber):
    """Raises if called at all — proves a short-circuit happened before any
    transcription work."""

    def transcribe(self, audio_path: Path) -> str:
        raise AssertionError("transcribe() should never be called")


class MultiParagraphTranscriber(Transcriber):
    """The second chunk's recovered transcript itself contains an internal
    blank line — a legitimate multi-paragraph transcript. Regression fixture
    for the code-review bug: resume_note must not split the stitched
    transcript on every "\\n\\n" (only chunk-marker boundaries), or this
    second paragraph is silently dropped."""

    def __init__(self):
        self.calls = 0

    def transcribe(self, audio_path: Path) -> str:
        self.calls += 1
        if self.calls == 2:
            return "first paragraph of recovered text\n\nsecond paragraph should not be lost"
        return f"recovered text {self.calls}"


# ---- the short-circuit (no placeholders at all) --------------------------------

def test_no_placeholders_short_circuits_before_any_transcription(tmp_path):
    note_path = tmp_path / "note.md"
    note_path.write_text("id: 20260101120000\n\nnothing to see here\n", encoding="utf-8")
    cache_dir = tmp_path / "cache"

    n = tr.resume_note(note_path, tmp_path / "does-not-exist.wav",
                       ExplodingTranscriber(), cache_dir)

    assert n == 0
    assert note_path.read_text(encoding="utf-8") == "id: 20260101120000\n\nnothing to see here\n"


# ---- one placeholder, fully recovered ------------------------------------------

@needs_ffmpeg
def test_recovers_the_only_placeholder_and_leaves_surrounding_text_untouched(tmp_path):
    audio = _wav(tmp_path / "meeting.wav", 601)  # > 600s → two 10-min chunks
    cache_dir = tmp_path / "cache"
    before = "id: 20260101120000\ntype: journal\n\n[00:00] first chunk\n\n"
    after = "\n\nsome trailing text\n"
    note_path = tmp_path / "note.md"
    note_path.write_text(f"{before}{PLACEHOLDER_10}{after}", encoding="utf-8")

    n = tr.resume_note(note_path, audio, HealsAllTranscriber(), cache_dir, sleep=lambda s: None)

    assert n == 1
    new_text = note_path.read_text(encoding="utf-8")
    expected = f"{before}[00:10] recovered text 2{after}"
    assert new_text == expected, "the surrounding text must be byte-identical"
    assert not cache_dir.exists(), "no placeholders remain — cache is disposable now"


@needs_ffmpeg
def test_a_recovered_chunks_internal_blank_line_is_not_truncated(tmp_path):
    """Code-review regression: splitting the stitched transcript on every
    "\\n\\n" (instead of only at chunk-marker boundaries) used to drop a
    recovered chunk's own second paragraph silently, while still counting
    it as fully recovered."""
    audio = _wav(tmp_path / "meeting.wav", 601)  # > 600s → two 10-min chunks
    cache_dir = tmp_path / "cache"
    note_path = tmp_path / "note.md"
    note_path.write_text(f"[00:00] first chunk\n\n{PLACEHOLDER_10}\n", encoding="utf-8")

    n = tr.resume_note(note_path, audio, MultiParagraphTranscriber(), cache_dir,
                       sleep=lambda s: None)

    assert n == 1
    new_text = note_path.read_text(encoding="utf-8")
    assert new_text == (
        "[00:00] first chunk\n\n"
        "[00:10] first paragraph of recovered text\n\n"
        "second paragraph should not be lost\n"
    ), "the full multi-paragraph recovered text must land in the note, not just the first paragraph"


# ---- two placeholders, only one heals ------------------------------------------

@needs_ffmpeg
def test_still_failing_placeholder_is_left_completely_untouched(tmp_path):
    audio = _wav(tmp_path / "meeting.wav", 1205)  # > 1200s → three 10-min chunks
    cache_dir = tmp_path / "cache"
    note_text = f"[00:00] first chunk\n\n{PLACEHOLDER_10}\n\n{PLACEHOLDER_20}\n"
    note_path = tmp_path / "note.md"
    note_path.write_text(note_text, encoding="utf-8")

    # chunk 0 and chunk 1 (calls 1, 2) succeed; chunk 2 (call 3) still fails.
    n = tr.resume_note(note_path, audio, HealsExceptCall(fail_on=3), cache_dir,
                       sleep=lambda s: None)

    assert n == 1
    new_text = note_path.read_text(encoding="utf-8")
    assert PLACEHOLDER_10 not in new_text
    assert PLACEHOLDER_20 in new_text, "the still-failing chunk's placeholder must be untouched"
    assert "[00:10] recovered text 2" in new_text
    assert cache_dir.exists(), "a placeholder remains — the cache is still useful"


# ---- repeat call on an already-fully-repaired note -----------------------------

@needs_ffmpeg
def test_repeat_call_after_full_recovery_returns_zero_without_retranscribing(tmp_path):
    audio = _wav(tmp_path / "meeting.wav", 601)
    cache_dir = tmp_path / "cache"
    note_path = tmp_path / "note.md"
    note_path.write_text(f"[00:00] first chunk\n\n{PLACEHOLDER_10}\n", encoding="utf-8")

    n1 = tr.resume_note(note_path, audio, HealsAllTranscriber(), cache_dir, sleep=lambda s: None)
    assert n1 == 1

    n2 = tr.resume_note(note_path, audio, ExplodingTranscriber(), cache_dir, sleep=lambda s: None)
    assert n2 == 0
