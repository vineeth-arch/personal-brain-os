"""Pass P — long recordings. A Plaud meeting is too big for one transcription
request, so it is segmented, transcribed piece by piece and stitched back into
ONE transcript. These tests hold the line that matters: whatever the chunking
does, a recording still becomes exactly one note, and one bad segment never
costs the rest of the meeting."""
from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from pipeline import transcribe as tr
from pipeline.errors import StageError
from pipeline.transcribe import Transcriber

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")


def _wav(path: Path, seconds: int, rate: int = 8000) -> Path:
    """A real (silent) wav — ffmpeg/ffprobe read it like any recording."""
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * rate * seconds)
    return path


class CountingTranscriber(Transcriber):
    def __init__(self):
        self.calls = 0

    def transcribe(self, audio_path: Path) -> str:
        self.calls += 1
        return f"chunk text {self.calls}"


class FailingChunk(Transcriber):
    """Permanently fails one segment; the rest transcribe fine."""

    def __init__(self, fail_on: int):
        self.calls = 0
        self.fail_on = fail_on

    def transcribe(self, audio_path: Path) -> str:
        self.calls += 1
        if self.calls == self.fail_on:
            raise StageError("Could not transcribe the recording.",
                             "OpenAI rejected the segment.", "Check the key.")
        return f"chunk text {self.calls}"


# ---- thresholds ---------------------------------------------------------------

def test_short_recording_is_not_chunked(tmp_path):
    short = _wav(tmp_path / "memo.wav", 1)
    assert tr.is_long(short, duration=90) is False


def test_long_duration_triggers_chunking(tmp_path):
    f = _wav(tmp_path / "meeting.wav", 1)
    assert tr.is_long(f, duration=tr.LONG_DURATION_SECONDS + 1) is True


def test_large_file_triggers_chunking_even_without_a_duration(tmp_path):
    """ffprobe can fail on an odd container; size alone must still catch a
    file too big for one request."""
    big = tmp_path / "big.m4a"
    big.write_bytes(b"0" * (tr.LONG_SIZE_BYTES + 1))
    assert tr.is_long(big, duration=None) is True


@needs_ffmpeg
def test_probe_duration_reads_real_audio(tmp_path):
    f = _wav(tmp_path / "twelve.wav", 12)
    assert tr.probe_duration_seconds(f) == pytest.approx(12, abs=1)


def test_probe_duration_is_none_for_a_non_audio_file(tmp_path):
    junk = tmp_path / "notes.txt"
    junk.write_text("not audio", encoding="utf-8")
    assert tr.probe_duration_seconds(junk) is None


# ---- chunk + stitch -----------------------------------------------------------

@needs_ffmpeg
def test_chunks_are_stitched_into_one_transcript_with_markers(tmp_path):
    audio = _wav(tmp_path / "long.wav", 12)
    t = CountingTranscriber()
    out = tr.transcribe_long(audio, t, sleep=lambda s: None, chunk_seconds=4)

    assert t.calls == 3, "expected three 4-second segments"
    # ONE transcript, in order, with an elapsed-time marker on every segment
    assert out.count("chunk text") == 3
    assert out.index("chunk text 1") < out.index("chunk text 2") < out.index("chunk text 3")
    assert out.count("[00:00]") == 3, "markers are hh:mm — 4s segments all sit in minute zero"


def test_markers_step_by_the_chunk_length():
    assert tr._marker(0, tr.CHUNK_SECONDS) == "[00:00]"
    assert tr._marker(1, tr.CHUNK_SECONDS) == "[00:10]"
    assert tr._marker(6, tr.CHUNK_SECONDS) == "[01:00]"
    assert tr._marker(13, tr.CHUNK_SECONDS) == "[02:10]"


@needs_ffmpeg
def test_a_twenty_minute_recording_becomes_one_marked_transcript(tmp_path):
    """The Plaud case, at real settings: a 20-minute file, real ffmpeg
    segmenting at the real 10-minute chunk length."""
    audio = _wav(tmp_path / "meeting.wav", 20 * 60)
    assert tr.is_long(audio, tr.probe_duration_seconds(audio)) is True

    t = CountingTranscriber()
    out = tr.transcribe_long(audio, t, sleep=lambda s: None)

    assert t.calls == 2, "20 minutes at 10-minute chunks is two segments"
    assert "[00:00]" in out and "[00:10]" in out
    assert out.index("[00:00]") < out.index("[00:10]")


@needs_ffmpeg
def test_one_permanently_failed_chunk_still_yields_the_meeting(tmp_path):
    audio = _wav(tmp_path / "long.wav", 12)
    events = []
    out = tr.transcribe_long(audio, FailingChunk(fail_on=2), sleep=lambda s: None,
                             on_event=lambda m, ok: events.append((m, ok)), chunk_seconds=4)

    assert "chunk text 1" in out and "chunk text 3" in out
    assert "unintelligible — audio archived" in out
    assert any("chunk 2/3 failed permanently" in m for m, ok in events if not ok)


@needs_ffmpeg
def test_transient_chunk_failure_is_retried_with_backoff(tmp_path):
    class Flaky(Transcriber):
        def __init__(self):
            self.calls = 0

        def transcribe(self, audio_path: Path):
            self.calls += 1
            if self.calls == 1:
                raise StageError("x", "y", "z", transient=True)
            return "ok text"

    audio = _wav(tmp_path / "long.wav", 5)
    slept = []
    out = tr.transcribe_long(audio, Flaky(), sleep=slept.append, chunk_seconds=10,
                             attempts=3, backoff_base=2)
    assert slept == [2], "a transient chunk failure must back off, not quarantine"
    assert "ok text" in out


# ---- the language hint ---------------------------------------------------------

def test_openai_multipart_carries_the_language_hint(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"RIFFfake")
    body = tr.OpenAITranscriber._multipart("BOUND", f, "hi")
    assert b'name="language"' in body and b"hi" in body
    # no hint configured → no field at all (whisper auto-detects)
    assert b'name="language"' not in tr.OpenAITranscriber._multipart("BOUND", f, "")
