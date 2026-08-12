"""Person notes — reading 07-People and writing the parts the app owns.

The record is the markdown file, not this module: everything here either reads
a note or edits a named field, and nothing rewrites a note wholesale. That
matters because a person note is mostly the owner's prose (`## Context`,
`## Needs`, `## Next action`) and an append-only history. The app's job is to
maintain four frontmatter fields and add dated lines to the log — never to
touch the sentences a human wrote.

Frontmatter and the four body sections are SCHEMA-REFERENCE.md §7 verbatim.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Iterator

from . import notefm, relationships, route

PEOPLE_FOLDER = route.TYPE_FOLDER["person"]     # "07-People"

INTERACTION_HEADING = "## Interaction log"
BODY_SECTIONS = ["## Context", "## Needs", INTERACTION_HEADING, "## Next action"]

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")


def person_notes(vault: Path) -> Iterator[tuple[Path, dict[str, str], str]]:
    """Every person note in the vault. A missing folder yields nothing — an
    empty People screen, not an error."""
    folder = Path(vault) / PEOPLE_FOLDER
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*.md")):
        try:
            fm, body = notefm.parse_frontmatter(path.read_text())
        except OSError:
            continue
        if fm.get("type") == "person":
            yield path, fm, body


def find_person(vault: Path, note_id: str) -> Path | None:
    for path, fm, _ in person_notes(vault):
        if fm.get("id") == note_id:
            return path
    return None


def person_name(path: Path, fm: dict[str, str]) -> str:
    """Frontmatter name wins, then title, then the human filename."""
    return fm.get("name") or fm.get("title") or _DATE_PREFIX_RE.sub("", path.stem)


def append_interaction(body: str, line: str) -> str:
    """Add one dated line to '## Interaction log', creating the section if the
    note predates it. Append-only by construction: existing lines are copied
    through untouched, so history can never be rewritten by an app write."""
    if INTERACTION_HEADING.lower() not in body.lower():
        return f"{body.rstrip()}\n\n{INTERACTION_HEADING}\n{line}".strip()

    out: list[str] = []
    inserted = False
    in_section = False
    for text in body.splitlines():
        stripped = text.strip()
        if stripped.lower() == INTERACTION_HEADING.lower():
            in_section = True
            out.append(text)
            continue
        # the next H2 closes the section — the new line goes just before it,
        # after any existing entries
        if in_section and stripped.startswith("## "):
            while out and not out[-1].strip():
                out.pop()
            out.extend(["", line, ""])
            inserted = True
            in_section = False
        out.append(text)
    if not inserted:
        while out and not out[-1].strip():
            out.pop()
        out.append(line)
    return "\n".join(out).strip()


def interaction_log(body: str) -> list[str]:
    """The dated lines under '## Interaction log', newest last (file order)."""
    entries: list[str] = []
    capturing = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.lower() == INTERACTION_HEADING.lower():
            capturing = True
            continue
        if capturing and stripped.startswith("## "):
            break
        if capturing and stripped:
            entries.append(stripped.lstrip("- ").strip())
    return entries


def sections(body: str) -> dict[str, str]:
    """H2 heading (without '## ') → text beneath it."""
    out: dict[str, str] = {}
    heading = ""
    buf: list[str] = []

    def flush() -> None:
        if heading:
            out[heading] = "\n".join(buf).strip()

    for line in body.splitlines():
        if line.strip().startswith("## "):
            flush()
            heading = line.strip()[3:].strip()
            buf = []
        else:
            buf.append(line)
    flush()
    return out


def build_person_note(*, note_id: str, name: str, created: str, source: str,
                      origin: str, fields: dict[str, str]) -> str:
    """A person note with the §7 frontmatter and the four empty body sections.

    `fields` carries whatever the caller actually knows (dex_id, company,
    channels…). Unknown values stay present-but-empty rather than absent: the
    schema names them, and an empty key is an invitation to fill it in, while a
    missing one looks like the note is from a different era."""
    channels = fields.get("channels", "{whatsapp: , email: , linkedin: }")
    lines = [
        "---",
        f"id: {note_id}",
        "type: person",
        f"name: {name}",
        f"created: {created}",
        f"source: {source}",
        f"origin: {origin}",
        f"status: {fields.get('status', relationships.ACTIVE)}",
        f"relationship: {fields.get('relationship', '')}",
        f"company: {fields.get('company', '')}",
        f"channels: {channels}",
        f"dex_id: {fields.get('dex_id', '')}",
        f"dex_deeplink: {fields.get('dex_deeplink', '')}",
        f"cadence_days: {fields.get('cadence_days', '')}",
        f"last_contact: {fields.get('last_contact', '')}",
        f"warmth_stage: {fields.get('warmth_stage', relationships.WARMTH_STAGES[0])}",
        "categories: []",
        "subjects: []",
        "tags: []",
        "---",
    ]
    body = "\n\n".join(BODY_SECTIONS)
    return "\n".join(lines) + "\n\n" + body + "\n"


def note_filename(name: str, created: str) -> str:
    return f"{created}-{route._kebab(name)}.md"


def stamp_status(fm_block: str, fm: dict[str, str], today: date) -> str:
    """Keep the stored `status:` in step with the computed lifecycle, so a
    reader outside this app (Obsidian Bases, a query) sees the same answer the
    People screen shows. `unset` is never written — there's nothing to say."""
    computed = relationships.classify_person(fm, today)
    if computed == relationships.UNSET:
        return fm_block
    return notefm.stamp_field(fm_block, "status", computed)
