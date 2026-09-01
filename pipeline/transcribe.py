"""Stage 2 — transcribe. Transcriber interface + whisper.cpp (default) and
OpenAI whisper-1 implementations. Text files skip this stage entirely."""
from __future__ import annotations

import re
import shutil
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
                    attempts: int = 3, backoff_base: int = 2,
                    cache_dir: Path | None = None) -> str:
    """Transcribe a long recording in segments and stitch them into one text.

    Each segment gets the same transient-retry policy the watcher applies to a
    whole file. A segment that fails permanently leaves a placeholder and the
    rest of the meeting still becomes a note — two hours are never lost to one
    bad ten minutes. `on_event(message, ok)` reports per-chunk outcomes.

    `cache_dir`, when given, makes a re-run cheap: each chunk's SUCCESSFUL
    transcript is cached to `cache_dir/chunk-%04d.txt` (atomic write —
    written to a temp file in the same directory, then renamed into place)
    before this function uses it; on the NEXT call with the same
    `cache_dir`, a chunk whose cache file already exists is read from disk
    instead of re-transcribed. Only chunks that failed or were never
    attempted hit the transcription engine again. `cache_dir` is created if
    missing. If every chunk succeeds this run, `cache_dir` is removed before
    returning — a fully-successful transcription has no further use for the
    cached pieces. If it isn't fully successful, this function never deletes
    it itself — the caller decides when the cache is no longer needed
    (this repo's disposable-cache convention: losing it just means the next
    resume re-transcribes everything, no data lost, matching how every other
    cache in this codebase is documented). Segmentation must be
    deterministic for the cache to align correctly across calls — same
    `audio_path` + same `chunk_seconds` always produces the same chunk
    boundaries via ffmpeg's `-segment_time`, so callers must always pass the
    SAME `chunk_seconds` for a given cache_dir (the default is stable; don't
    vary it across calls against the same cache).
    """
    with tempfile.TemporaryDirectory(prefix="bc-chunks-") as tmp:
        chunks = _segment(audio_path, Path(tmp), chunk_seconds)
        if not chunks:
            raise StageError("Could not split the long recording.",
                             "ffmpeg produced no segments from the file.",
                             "Check the recording plays, then drop it back in the inbox.")
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
        pieces = []
        failed = 0
        for i, chunk in enumerate(chunks):
            cache_file = cache_dir / f"chunk-{i:04d}.txt" if cache_dir is not None else None
            text = cache_file.read_text(encoding="utf-8") if cache_file and cache_file.exists() else None
            if text is None:
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
                if text is not None and cache_file is not None:
                    tmp_cache = cache_file.with_suffix(".tmp")
                    tmp_cache.write_text(text, encoding="utf-8")
                    tmp_cache.replace(cache_file)
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
            # Quarantine like any other permanent failure instead. The audio
            # never gets archived on this path (it's quarantined to failed/
            # instead), so an empty cache dir here would just be an orphan —
            # clean it up rather than leaving an empty .chunks/<stem>/ behind.
            if cache_dir is not None:
                shutil.rmtree(cache_dir, ignore_errors=True)
            raise StageError(
                "Could not transcribe any part of the long recording.",
                f"All {len(chunks)} segments failed to transcribe.",
                "Check the recording plays and the transcription engine is reachable, "
                "then drop the file back in the inbox.")

        if cache_dir is not None and failed == 0 and cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)

        if on_event:
            summary = f"stitched {len(chunks)} chunk(s)"
            if failed:
                summary += f", {failed} failed"
            on_event(summary, failed == 0)
        return "\n\n".join(pieces).strip()


# ---- resuming a partially-failed transcription (Pass E) -----------------------
# A partial failure still writes a note (placeholders and all) and archives the
# audio — it never reaches `failed/`, so it's never seen by the retry route.
# `resume_note` repairs one of these after the fact, given the note and its
# archived audio explicitly: there is no index in this codebase from a note's
# id back to its archived audio's filename today (adding one is a bigger
# schema decision, out of scope here), so the caller supplies both paths.
_UNINTELLIGIBLE_RE = re.compile(
    r"\[\d{2}:\d{2}\] \[\d+ minutes? unintelligible — audio archived\]")


def resume_note(note_path: Path, audio_path: Path, transcriber: Transcriber,
                cache_dir: Path, *, sleep=time.sleep) -> int:
    """Repair an already-written note's placeholder gaps by re-running
    transcription against the SAME audio, reusing (and extending) whatever's
    already cached — only chunks that are still missing/failed actually hit
    the transcription engine. `cache_dir` is caller-supplied (this function
    doesn't invent one — there's no index today mapping a note back to its
    archived audio's cache location; the caller already knows both paths).
    Replaces each newly-recovered chunk's EXACT placeholder text in the
    note's body with the fresh transcript (marker-delimited — only a
    placeholder whose [hh:mm] marker matches a chunk that's no longer
    unintelligible is touched; a chunk still failing is left completely
    alone, matching the note's own placeholder text exactly so nothing
    changes for it). Returns the count of chunks recovered — 0 is a valid,
    harmless outcome (nothing was fixable yet). Deletes `cache_dir` once no
    placeholders remain in the note (nothing left to resume).

    This function is pipeline-level and does NOT call `git_commit_vault` —
    that's an api/-layer concern (this module has no api/ imports, matching
    every other pipeline module's boundary). Committing the note change is
    the caller's job.
    """
    text = note_path.read_text(encoding="utf-8")
    if not _UNINTELLIGIBLE_RE.search(text):
        return 0  # nothing to resume — short-circuit before any transcription work

    new_transcript = transcribe_long(audio_path, transcriber, sleep=sleep, cache_dir=cache_dir)

    recovered = 0
    # Split only at chunk-marker boundaries, not every blank line — a
    # recovered chunk's own text can legitimately contain a blank line
    # (e.g. a multi-paragraph transcript), and splitting on "\n\n" would
    # silently truncate it at the first paragraph break.
    for piece in re.split(r"\n\n(?=\[\d{2}:\d{2}\])", new_transcript):
        piece = piece.strip()
        if not piece.startswith("["):
            continue
        marker, _, rest = piece.partition("] ")
        marker += "]"
        rest = rest.strip()
        if _UNINTELLIGIBLE_RE.fullmatch(f"{marker} {rest}"):
            continue  # this chunk is STILL unintelligible — nothing to replace
        placeholder_pattern = re.compile(
            re.escape(marker) + r" \[\d+ minutes? unintelligible — audio archived\]")
        new_text, n = placeholder_pattern.subn(f"{marker} {rest}", text, count=1)
        if n:
            text = new_text
            recovered += 1

    if recovered:
        note_path.write_text(text, encoding="utf-8")

    if not _UNINTELLIGIBLE_RE.search(text) and cache_dir.exists():
        shutil.rmtree(cache_dir, ignore_errors=True)

    return recovered
