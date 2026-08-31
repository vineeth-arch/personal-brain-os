"""Stage 1 — intake. Poll the inbox for audio + text, parse optional filename
metadata "YYYY-MM-DD-HHmm <name> #tag.ext". The inbox is source-agnostic."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import enrich

# .webm/.mp4 are what the cockpit's mic button records (MediaRecorder)
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".opus", ".ogg", ".oga", ".flac",
             ".webm", ".mp4"}
TEXT_EXT = {".txt", ".md"}

# "2026-07-03-0900 morning walk #idea"  (time, name, tag all after the date are optional)
_NAME_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<time>\d{4})\s+(?P<name>.*?)"
                      r"(?:\s+#(?P<tag>[\w-]+))?$")


# recordings dropped here (by Plaud Desktop's export, or the ingest sweep) are
# stamped with their provenance instead of the generic "voice"
PLAUD_DIR = "plaud"


@dataclass
class Item:
    path: Path
    kind: str                 # "audio" | "text" | "link"
    captured: datetime
    name: str                 # human title hint from filename
    tag: str | None           # capture/routing tag from filename, if any
    source: str               # "voice" | "manual" | "plaud"


def _parse(path: Path, source_hint: str | None = None) -> Item | None:
    ext = path.suffix.lower()
    if ext in AUDIO_EXT:
        kind, source = "audio", source_hint or "voice"
    elif ext in TEXT_EXT:
        # a text capture whose body is (or contains) a URL becomes a link —
        # enriched into a resource note instead of classified (Pass L)
        try:
            kind = "link" if enrich.is_link_text(path.read_text(encoding="utf-8")) else "text"
        except (OSError, UnicodeDecodeError):
            kind = "text"
        # The hint wins here too. This was hard-coded "manual", which silently
        # discarded the caller's provenance: a transcript exported beside a
        # Plaud recording landed as source: manual, so the note claimed a human
        # typed what the device heard (SCHEMA-REFERENCE.md §2 provenance).
        source = source_hint or "manual"
    else:
        return None

    stem = path.stem
    m = _NAME_RE.match(stem)
    if m:
        captured = datetime.strptime(f"{m['date']}-{m['time']}", "%Y-%m-%d-%H%M")
        name = m["name"].strip() or stem
        tag = m["tag"]
    else:
        captured = datetime.fromtimestamp(path.stat().st_mtime)
        name, tag = stem, None
    return Item(path=path, kind=kind, captured=captured, name=name, tag=tag, source=source)


def poll(inbox_path: Path) -> list[Item]:
    """Return inbox items oldest-first (by captured time), ignoring unknown types + dotfiles.

    The inbox root is source-agnostic; the one subfolder that carries meaning is
    `plaud/`, whose recordings are stamped `source: plaud`."""
    inbox = Path(inbox_path)
    items = []
    for p in inbox.iterdir():
        if p.is_file() and not p.name.startswith("."):
            item = _parse(p, "plaud" if p.stem.lower().startswith("plaud") else None)
            if item:
                items.append(item)
    plaud_dir = inbox / PLAUD_DIR
    if plaud_dir.is_dir():
        for p in plaud_dir.iterdir():
            if p.is_file() and not p.name.startswith("."):
                item = _parse(p, "plaud")
                if item:
                    items.append(item)
    items.sort(key=lambda i: i.captured)
    return items
