"""Stage 2 — transcribe. Transcriber interface + whisper.cpp (default) and
OpenAI whisper-1 implementations. Text files skip this stage entirely."""
from __future__ import annotations

import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from .errors import StageError


class Transcriber(ABC):
    @abstractmethod
    def transcribe(self, audio_path: Path) -> str:
        ...


def _ffmpeg_to_wav16k(audio_path: Path) -> Path:
    """Convert any audio to 16 kHz mono wav (what whisper.cpp expects)."""
    out = Path(tempfile.gettempdir()) / f"bc-{uuid.uuid4().hex}.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(audio_path), "-ar", "16000", "-ac", "1",
             "-f", "wav", str(out)],
            check=True, capture_output=True)
    except FileNotFoundError:
        raise StageError("Could not convert the audio.",
                         "ffmpeg is not installed or not on PATH.",
                         "Install ffmpeg (`brew install ffmpeg`) and re-run.")
    except subprocess.CalledProcessError as e:
        raise StageError("Could not convert the audio to a transcribable format.",
                         "ffmpeg rejected the file — it may be corrupt or truncated.",
                         "Re-record or re-export the audio, then drop it back in the inbox.") from e
    return out


class WhisperCppTranscriber(Transcriber):
    def __init__(self, binary_path: str, model_path: str):
        self.binary = binary_path
        self.model = model_path

    def transcribe(self, audio_path: Path) -> str:
        if not self.binary or not Path(self.binary).exists():
            raise StageError("Could not transcribe the recording.",
                             "The whisper.cpp binary path in config.json is missing or wrong.",
                             "Set transcription.whispercpp.binary_path to your whisper-cli binary.")
        wav = _ffmpeg_to_wav16k(audio_path)
        try:
            # ponytail: -nt = no timestamps, transcript to stdout. Works with
            # whisper-cli/main; if your build differs, this is the line to adjust.
            proc = subprocess.run(
                [self.binary, "-m", self.model, "-f", str(wav), "-nt"],
                check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            raise StageError("Could not transcribe the recording.",
                             "whisper.cpp failed — the model path may be wrong or the audio empty.",
                             "Check transcription.whispercpp.model_path, then re-run.") from e
        finally:
            wav.unlink(missing_ok=True)
        return proc.stdout.strip()


class OpenAITranscriber(Transcriber):
    """whisper-1 via a stdlib multipart POST (no openai/requests dependency)."""

    def __init__(self, api_key: str | None, language: str = ""):
        self.api_key = api_key
        self.language = language

    def transcribe(self, audio_path: Path) -> str:
        if not self.api_key:
            raise StageError("Could not transcribe the recording.",
                             "OPENAI_API_KEY is not set but transcription.engine is 'openai'.",
                             "export OPENAI_API_KEY=... or switch the engine to whispercpp.")
        boundary = uuid.uuid4().hex
        body = self._multipart(boundary, audio_path, self.language)
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions", data=body,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            import json
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read())["text"].strip()
        except urllib.error.HTTPError as e:
            # 408/429/5xx fix themselves — retry; other 4xx (bad key, bad
            # audio) never do — quarantine immediately.
            if e.code in (408, 429) or e.code >= 500:
                raise StageError("Could not transcribe the recording.",
                                 "OpenAI's transcription service is temporarily unavailable "
                                 "(it answered with a server error or a rate limit).",
                                 "Nothing to fix — it will be retried automatically.",
                                 transient=True) from e
            raise StageError("Could not transcribe the recording.",
                             "OpenAI rejected the request — the key may be wrong or the "
                             "audio in a format it doesn't accept.",
                             "Check OPENAI_API_KEY, or re-export the audio and drop it "
                             "back in the inbox.") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise StageError("Could not transcribe the recording.",
                             "The OpenAI transcription service couldn't be reached "
                             "(network down or very slow).",
                             "Check this machine's connection — it will be retried "
                             "automatically.",
                             transient=True) from e
        except Exception as e:
            raise StageError("Could not transcribe the recording.",
                             "OpenAI answered in a way the pipeline didn't understand.",
                             "Try again; if it keeps happening, check the pipeline log.") from e

    @staticmethod
    def _multipart(boundary: str, audio_path: Path, language: str = "") -> bytes:
        nl = b"\r\n"
        parts = [
            b"--" + boundary.encode(), nl,
            b'Content-Disposition: form-data; name="model"', nl, nl, b"whisper-1", nl,
        ]
        if language:
            # a language hint keeps Hindi speech in Devanagari instead of being
            # guessed at (and half-translated) language by language
            parts += [
                b"--" + boundary.encode(), nl,
                b'Content-Disposition: form-data; name="language"', nl, nl,
                language.encode(), nl,
            ]
        parts += [
            b"--" + boundary.encode(), nl,
            f'Content-Disposition: form-data; name="file"; filename="{audio_path.name}"'.encode(), nl,
            b"Content-Type: application/octet-stream", nl, nl,
            audio_path.read_bytes(), nl,
            b"--" + boundary.encode() + b"--", nl,
        ]
        return b"".join(parts)


def build_transcriber(config) -> Transcriber:
    if config.engine == "openai":
        return OpenAITranscriber(config.openai_key, getattr(config, "language", ""))
    return WhisperCppTranscriber(config.whispercpp_binary, config.whispercpp_model)


# ---- long audio (Pass P) -----------------------------------------------------
# A Plaud meeting or a 2-hour recording can't go up as one request: OpenAI caps
# an upload at 25 MB, and whisper.cpp on a laptop crawls on very long files. So
# past a threshold the audio is SEGMENTED, transcribed piece by piece, and
# stitched back into ONE transcript — one recording is still one note
# (SCHEMA-REFERENCE.md §8); only the request shape changes, never the output.
CHUNK_SECONDS = 600                       # 10-minute segments
LONG_DURATION_SECONDS = 15 * 60           # chunk past 15 minutes...
LONG_SIZE_BYTES = 20 * 1024 * 1024        # ...or past 20 MB (25 MB cap, with margin)


def _ffprobe_field(audio_path: Path, entries: str) -> float | None:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", entries,
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            check=True, capture_output=True, text=True, timeout=60)
        # ffprobe prints "N/A" for containers with no duration in that field
        # (a streamed .webm from MediaRecorder has none at format level)
        first_line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
        return float(first_line)
    except (FileNotFoundError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def probe_duration_seconds(audio_path: Path) -> float | None:
    """Recording length via ffprobe, or None if it can't be determined.

    Never raises: an unreadable duration must not stop a capture — the size
    threshold still decides, and a normal recording just takes the short path.
    Falls back from the container-level `format=duration` (absent on a
    streamed MediaRecorder .webm) to the first audio stream's own duration.
    """
    return (_ffprobe_field(audio_path, "format=duration")
            or _ffprobe_field(audio_path, "stream=duration"))


def is_long(audio_path: Path, duration: float | None) -> bool:
    if duration is not None and duration > LONG_DURATION_SECONDS:
        return True
    try:
        return audio_path.stat().st_size > LONG_SIZE_BYTES
    except OSError:
        return False


def _segment(audio_path: Path, out_dir: Path, seconds: int) -> list[Path]:
    """Split into fixed-length wav segments with ffmpeg, in order."""
    pattern = out_dir / "chunk-%04d.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(audio_path), "-f", "segment",
             "-segment_time", str(seconds), "-ar", "16000", "-ac", "1",
             "-reset_timestamps", "1", str(pattern)],
            check=True, capture_output=True)
    except FileNotFoundError:
        raise StageError("Could not split the long recording.",
                         "ffmpeg is not installed or not on PATH.",
                         "Install ffmpeg (`brew install ffmpeg`) and re-run.")
    except subprocess.CalledProcessError as e:
        raise StageError("Could not split the long recording.",
                         "ffmpeg rejected the file — it may be corrupt or truncated.",
                         "Re-export the recording, then drop it back in the inbox.") from e
    return sorted(out_dir.glob("chunk-*.wav"))


def _marker(index: int, seconds_per_chunk: int) -> str:
    total = index * seconds_per_chunk
    return f"[{total // 3600:02d}:{(total % 3600) // 60:02d}]"


def transcribe_long(audio_path: Path, transcriber: Transcriber, *, sleep=time.sleep,
                    on_event=None, chunk_seconds: int = CHUNK_SECONDS,
                    attempts: int = 3, backoff_base: int = 2) -> str:
    """Transcribe a long recording in segments and stitch them into one text.

    Each segment gets the same transient-retry policy the watcher applies to a
    whole file. A segment that fails permanently leaves a placeholder and the
    rest of the meeting still becomes a note — two hours are never lost to one
    bad ten minutes. `on_event(message, ok)` reports per-chunk outcomes.
    """
    with tempfile.TemporaryDirectory(prefix="bc-chunks-") as tmp:
        chunks = _segment(audio_path, Path(tmp), chunk_seconds)
        if not chunks:
            raise StageError("Could not split the long recording.",
                             "ffmpeg produced no segments from the file.",
                             "Check the recording plays, then drop it back in the inbox.")
        pieces = []
        failed = 0
        for i, chunk in enumerate(chunks):
            text = None
            for attempt in range(1, attempts + 1):
                try:
                    text = transcriber.transcribe(chunk)
                    break
                except StageError as e:
                    if not e.transient or attempt == attempts:
                        if on_event:
                            on_event(f"chunk {i + 1}/{len(chunks)} failed permanently", False)
                        break
                    sleep(backoff_base * 2 ** (attempt - 1))
            marker = _marker(i, chunk_seconds)
            if text is None:
                failed += 1
                minutes = max(1, round(chunk_seconds / 60))
                pieces.append(f"{marker} [{minutes} minutes unintelligible — audio archived]")
            else:
                pieces.append(f"{marker} {text.strip()}".strip())

        if failed == len(chunks):
            # nothing at all came through — a note that's pure placeholders
            # would be silent data loss (it reads as a normal, if odd, memo).
            # Quarantine like any other permanent failure instead.
            raise StageError(
                "Could not transcribe any part of the long recording.",
                f"All {len(chunks)} segments failed to transcribe.",
                "Check the recording plays and the transcription engine is reachable, "
                "then drop the file back in the inbox.")

        if on_event:
            summary = f"stitched {len(chunks)} chunk(s)"
            if failed:
                summary += f", {failed} failed"
            on_event(summary, failed == 0)
        return "\n\n".join(pieces).strip()
