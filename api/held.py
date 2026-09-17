"""Held drafts — a paused touch, parked as a vault file at
`_System/held/{person_id}.md` until tomorrow morning (SCHEMA-REFERENCE.md
§7 Hold). A hold is never a send (CLAUDE.md §4): it is a note to self that
comes back into the queue once it is ready.

`person_id` is validated against `^\\d{14}$` before it ever touches a path —
anything else is refused rather than built into a filename (plan risk P22:
a crafted id must not be able to escape `_System/held/`)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from .notes import git_commit_vault

log = logging.getLogger("api")

HELD_FOLDER = "_System/held"
_PERSON_ID = re.compile(r"^\d{14}$")


def valid_id(person_id: str) -> bool:
    return bool(_PERSON_ID.match(person_id))


def _path(vault: Path, person_id: str) -> Path:
    return Path(vault) / HELD_FOLDER / f"{person_id}.md"


def _front_value(head: str, key: str) -> str:
    for line in head.splitlines():
        if line.startswith(f"{key}:"):
            return line.partition(":")[2].strip()
    return ""


@dataclass
class HeldItem:
    person_id: str
    text: str
    channel: str
    touch_type: str
    held_until: datetime
    ready: bool


def hold(vault: Path, person_id: str, text: str, channel: str, touch_type: str,
        now: datetime) -> datetime:
    """Write (or overwrite) the hold file for `person_id`, held until 9 am
    the day after `now`, in `now`'s own tzinfo. Returns that `held_until`.

    Raises ValueError when `person_id` doesn't look like a note id — never
    lets an unvalidated id become part of a filesystem path."""
    if not valid_id(person_id):
        raise ValueError(f"{person_id!r} is not a valid person id")
    tomorrow = (now + timedelta(days=1)).date()
    held_until = datetime.combine(tomorrow, time(9, 0), tzinfo=now.tzinfo)
    path = _path(vault, person_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "origin: human\n"
        f"person_id: {person_id}\n"
        f"held_until: {held_until.isoformat()}\n"
        f"channel: {channel}\n"
        f"touch_type: {touch_type}\n"
        "---\n\n"
        f"{text}\n",
        encoding="utf-8")
    git_commit_vault(Path(vault), f"api: held a touch for {person_id}")
    return held_until


def delete(vault: Path, person_id: str) -> bool:
    """Remove the hold file if it exists. Returns whether one was removed.

    Raises ValueError for the same bad-id reason as `hold`."""
    if not valid_id(person_id):
        raise ValueError(f"{person_id!r} is not a valid person id")
    path = _path(vault, person_id)
    existed = path.exists()
    if existed:
        path.unlink()
    git_commit_vault(Path(vault), f"api: cleared hold for {person_id}")
    return existed


def list_items(vault: Path, now: datetime) -> list[HeldItem]:
    """Every held draft in the vault. A file that can't be read or parsed is
    skipped and logged rather than failing the whole list — one bad hold
    file must never take Today down with it."""
    folder = Path(vault) / HELD_FOLDER
    if not folder.is_dir():
        return []
    items: list[HeldItem] = []
    for path in sorted(folder.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            log.warning("could not read held file %s", path)
            continue
        if not text.startswith("---\n"):
            log.warning("held file %s has no frontmatter", path)
            continue
        head, _, body = text.partition("\n---\n")
        held_until_raw = _front_value(head, "held_until")
        try:
            held_until = datetime.fromisoformat(held_until_raw)
        except ValueError:
            log.warning("held file %s has an unreadable held_until", path)
            continue
        person_id = _front_value(head, "person_id") or path.stem
        items.append(HeldItem(
            person_id=person_id,
            text=body.strip(),
            channel=_front_value(head, "channel"),
            touch_type=_front_value(head, "touch_type"),
            held_until=held_until,
            ready=now >= held_until,
        ))
    return items
