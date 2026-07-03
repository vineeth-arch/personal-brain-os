"""Stage 2 — transcribe. Transcriber interface + whisper.cpp (default) and
OpenAI whisper-1 implementations. Text files skip this stage entirely."""
from __future__ import annotations

import subprocess
import tempfile
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

    def __init__(self, api_key: str | None):
        self.api_key = api_key

    def transcribe(self, audio_path: Path) -> str:
        if not self.api_key:
            raise StageError("Could not transcribe the recording.",
                             "OPENAI_API_KEY is not set but transcription.engine is 'openai'.",
                             "export OPENAI_API_KEY=... or switch the engine to whispercpp.")
        boundary = uuid.uuid4().hex
        body = self._multipart(boundary, audio_path)
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions", data=body,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            import json
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read())["text"].strip()
        except Exception as e:
            raise StageError("Could not transcribe the recording.",
                             "The OpenAI transcription request failed (network, quota, or bad key).",
                             "Check OPENAI_API_KEY and your connection, then re-run.") from e

    @staticmethod
    def _multipart(boundary: str, audio_path: Path) -> bytes:
        nl = b"\r\n"
        parts = [
            b"--" + boundary.encode(), nl,
            b'Content-Disposition: form-data; name="model"', nl, nl, b"whisper-1", nl,
            b"--" + boundary.encode(), nl,
            f'Content-Disposition: form-data; name="file"; filename="{audio_path.name}"'.encode(), nl,
            b"Content-Type: application/octet-stream", nl, nl,
            audio_path.read_bytes(), nl,
            b"--" + boundary.encode() + b"--", nl,
        ]
        return b"".join(parts)


def build_transcriber(config) -> Transcriber:
    if config.engine == "openai":
        return OpenAITranscriber(config.openai_key)
    return WhisperCppTranscriber(config.whispercpp_binary, config.whispercpp_model)
