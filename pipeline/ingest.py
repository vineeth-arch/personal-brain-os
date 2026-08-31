"""Auto-ingest — pull recordings out of the folders other apps own.

Plaud Desktop (Google Meet / Zoom on the laptop), the Note Pro's synced export
folder, and the Mac's Voice Memos folder each keep their files in their own
place. This stage COPIES new recordings from those folders into the inbox so
the normal pipeline picks them up: the source folder is never modified, because
it belongs to another app and the user may still want the file there.

Configured in config.json:

    "watch_folders": [
      {"path": "~/Library/.../Plaud", "source": "plaud"},
      {"path": "~/Library/.../Recordings", "source": "voice"}
    ]

`source: plaud` lands in inbox/plaud/ so intake stamps its provenance. What has
been copied is remembered in events.db (path + mtime + size) — pipeline state,
not knowledge.

A recording is treated as a BUNDLE (pipeline/plaud.py): the audio, plus
whatever the device already produced alongside it — a speaker-labelled
transcript, an AI summary. Two shapes are recognised: applaud's directory-per-
recording sync, and Plaud Desktop's flat sidecar export (meeting.m4a +
meeting.txt). Either way, a transcript found here travels into the inbox as a
hidden sidecar next to the copied audio (pipeline/plaud.sidecar_paths) so the
watcher can read it instead of re-transcribing with whisper — see
watcher._transcribe. An ordinary recording with no sidecars at all (Voice
Memos, most `source: voice` folders) is completely unaffected: no sidecars are
written, and the audio is copied exactly as it always was.

A recording still being processed on Plaud's side — audio synced, transcript
not written yet — lands as an ordinary audio file with no sidecar, same as any
other lone recording; the transcript catches up whenever the folder is swept
again after Plaud finishes (constitution: never lose a capture waiting on it).
The mirror case — a transcript that arrived before its audio — is imported as
a visible text capture so the words are never lost; if the audio then appears
on a later tick it is imported too, producing a second note for the same
recording. That double-import is a known, accepted edge case (rare: Plaud
normally uploads audio before it finishes transcribing, not after) rather than
one this pass solves — a real fix needs bundle-level dedupe keyed on the
device's own recording id, not per-file.
"""
from __future__ import annotations

import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from . import plaud
from .intake import AUDIO_EXT

# A file the recorder is still writing has a very recent mtime; leaving it for
# the next tick is cheaper than importing half a meeting.
SETTLE_SECONDS = 30

# what a watched folder may claim its recordings are (SCHEMA-REFERENCE.md §2)
VALID_SOURCES = {"voice", "plaud", "share", "manual"}


def folders(config) -> list[tuple[Path, str]]:
    """The configured (path, source) pairs, skipping malformed entries."""
    out = []
    for entry in (getattr(config, "raw", {}) or {}).get("watch_folders") or []:
        path = (entry or {}).get("path")
        source = ((entry or {}).get("source") or "voice").strip().lower()
        if not path or source not in VALID_SOURCES:
            continue
        out.append((Path(path).expanduser(), source))
    return out


def _destination(inbox: Path, source: str, name: str, ext: str, stamp: datetime) -> Path:
    """The inbox path this recording gets, in the filename shape intake parses.

    `name` is a title, not a raw filename stem: applaud's own files are always
    named audio.ogg / transcript.txt, so deriving the note title from the
    SOURCE file (as this used to) would give every Plaud note the same useless
    title, "audio". The caller passes the bundle's parsed title instead —
    which for the flat sidecar shape (Plaud Desktop) IS the source stem, so
    that path's naming is unchanged.
    """
    folder = inbox / "plaud" if source == "plaud" else inbox
    folder.mkdir(parents=True, exist_ok=True)
    name = (name or "recording").strip() or "recording"
    dest = folder / f"{stamp:%Y-%m-%d-%H%M} {name}{ext}"
    i = 1
    while dest.exists():
        i += 1
        dest = folder / f"{stamp:%Y-%m-%d-%H%M} {name}-{i}{ext}"
    return dest


def _atomic_copy(src: Path, dest: Path) -> bool:
    tmp = dest.with_name(f".ingest-{dest.name}")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)     # the watcher never sees a half file
        return True
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        return False


def _atomic_write(dest: Path, text: str) -> bool:
    tmp = dest.with_name(f".ingest-{dest.name}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, dest)
        return True
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        return False


def _write_sidecars(audio_dest: Path, transcript, summary: str) -> None:
    """Best-effort. A sidecar write failing must not undo the audio import
    that already succeeded — worst case, that one note falls back to whisper
    transcription instead of using the device's transcript."""
    t_path, s_path = plaud.sidecar_paths(audio_dest)
    if transcript is not None and transcript.body:
        _atomic_write(t_path, transcript.body)
    if summary:
        _atomic_write(s_path, summary)


def _bundle_for(entry: Path) -> plaud.Bundle | None:
    """The bundle at `entry`, or None if it isn't one we recognise.

    A directory is checked against applaud's layout; a lone audio file is
    checked for Plaud Desktop's flat sidecars (present or not — sidecars_for
    always returns something for real audio, so a recording with nothing
    alongside it is still imported exactly as before). Anything else — a
    stray text file, a thumbnail, a dotfile — is left alone: watched folders
    like Voice Memos hold plenty of files that are not captures, and importing
    every one of them would be worse than missing a rare bundle shape.
    """
    try:
        is_dir = entry.is_dir()
    except OSError:
        return None
    if is_dir:
        return plaud.from_directory(entry, AUDIO_EXT)
    if entry.suffix.lower() in AUDIO_EXT:
        return plaud.sidecars_for(entry)
    return None


def sweep(config, events, *, now: float | None = None) -> list[Path]:
    """Copy unseen recordings from every watched folder into the inbox.

    Returns what was copied. Never raises: a missing or unreadable folder (an
    unmounted drive, an app not installed yet) must not stop the watcher tick.
    """
    now = time.time() if now is None else now
    copied = []
    for folder, source in folders(config):
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue                      # folder gone or unreadable — try next tick
        for entry in entries:
            if entry.name.startswith("."):
                continue
            bundle = _bundle_for(entry)
            if bundle is None:
                continue

            primary = bundle.audio or bundle.transcript
            if primary is None:
                continue
            try:
                stat = primary.stat()
            except OSError:
                continue
            if now - stat.st_mtime < SETTLE_SECONDS:
                continue                  # still being written/synced
            key = str(primary)
            if events.already_ingested(key, stat.st_mtime_ns, stat.st_size):
                continue

            # read from the WATCHED folder's own files — cheap, done once here,
            # so nothing downstream re-parses SRT/JSON ever again
            transcript = plaud.read_transcript(bundle)
            summary = plaud.read_summary(bundle)
            stamp = datetime.fromtimestamp(stat.st_mtime)
            title = bundle.title or primary.stem

            if bundle.audio is not None:
                dest = _destination(Path(config.inbox_path), source, title,
                                    bundle.audio.suffix.lower(), stamp)
                if not _atomic_copy(bundle.audio, dest):
                    continue
                if transcript is not None or summary:
                    _write_sidecars(dest, transcript, summary)
            else:
                # no audio yet (still processing) — the transcript IS the
                # capture: a visible .txt note, like any other text capture
                if transcript is None or not transcript.body:
                    continue              # nothing importable yet
                dest = _destination(Path(config.inbox_path), source, title, ".txt", stamp)
                if not _atomic_write(dest, transcript.body):
                    continue

            events.mark_ingested(key, stat.st_mtime_ns, stat.st_size)
            detail = " transcript=plaud" if transcript is not None else ""
            events.log(key, "ingest", "ok",
                      message=f"copied to {dest.name} source={source}{detail}")
            copied.append(dest)
    return copied
