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
"""
from __future__ import annotations

import os
import shutil
import time
from datetime import datetime
from pathlib import Path

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


def _destination(inbox: Path, source: str, src: Path, stamp: datetime) -> Path:
    """The inbox path this recording gets, in the filename shape intake parses."""
    folder = inbox / "plaud" if source == "plaud" else inbox
    folder.mkdir(parents=True, exist_ok=True)
    name = src.stem.strip() or "recording"
    dest = folder / f"{stamp:%Y-%m-%d-%H%M} {name}{src.suffix.lower()}"
    i = 1
    while dest.exists():
        i += 1
        dest = folder / f"{stamp:%Y-%m-%d-%H%M} {name}-{i}{src.suffix.lower()}"
    return dest


def sweep(config, events, *, now: float | None = None) -> list[Path]:
    """Copy unseen recordings from every watched folder into the inbox.

    Returns what was copied. Never raises: a missing or unreadable folder (an
    unmounted drive, an app not installed yet) must not stop the watcher tick.
    """
    now = time.time() if now is None else now
    copied = []
    for folder, source in folders(config):
        try:
            entries = sorted(p for p in folder.iterdir() if p.is_file())
        except OSError:
            continue                      # folder gone or unreadable — try next tick
        for src in entries:
            if src.suffix.lower() not in AUDIO_EXT or src.name.startswith("."):
                continue
            try:
                stat = src.stat()
            except OSError:
                continue
            if now - stat.st_mtime < SETTLE_SECONDS:
                continue                  # still being written by the recorder
            if events.already_ingested(str(src), stat.st_mtime_ns, stat.st_size):
                continue
            dest = _destination(Path(config.inbox_path), source,
                                src, datetime.fromtimestamp(stat.st_mtime))
            tmp = dest.with_name(f".ingest-{dest.name}")
            try:
                shutil.copy2(src, tmp)
                os.replace(tmp, dest)     # the watcher never sees a half file
            except OSError:
                Path(tmp).unlink(missing_ok=True)
                continue
            events.mark_ingested(str(src), stat.st_mtime_ns, stat.st_size)
            events.log(str(src), "ingest", "ok", message=f"copied to {dest.name} source={source}")
            copied.append(dest)
    return copied
