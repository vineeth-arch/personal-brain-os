"""Stage 1 — intake. Poll the inbox for audio + text, parse optional filename
metadata "YYYY-MM-DD-HHmm <name> #tag.ext". The inbox is source-agnostic."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import enrich

AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac"}
TEXT_EXT = {".txt", ".md"}

# "2026-07-03-0900 morning walk #idea"  (time, name, tag all after the date are optional)
_NAME_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<time>\d{4})\s+(?P<name>.*?)"
                      r"(?:\s+#(?P<tag>[\w-]+))?$")


@dataclass
class Item:
    path: Path
    kind: str                 # "audio" | "text" | "link"
    captured: datetime
    name: str                 # human title hint from filename
    tag: str | None           # capture/routing tag from filename, if any
    source: str               # "voice" | "manual"


def _parse(path: Path) -> Item | None:
    ext = path.suffix.lower()
    if ext in AUDIO_EXT:
        kind, source = "audio", "voice"
    elif ext in TEXT_EXT:
        # a text capture whose body is (or contains) a URL becomes a link —
        # enriched into a resource note instead of classified (Pass L)
        try:
            kind = "link" if enrich.is_link_text(path.read_text()) else "text"
        except OSError:
            kind = "text"
        source = "manual"
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
    """Return inbox items oldest-first (by captured time), ignoring unknown types + dotfiles."""
    items = []
    for p in Path(inbox_path).iterdir():
        if p.is_file() and not p.name.startswith("."):
            item = _parse(p)
            if item:
                items.append(item)
    items.sort(key=lambda i: i.captured)
    return items
