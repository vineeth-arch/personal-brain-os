"""Stage 4 — route. Build the note with universal frontmatter (SCHEMA-REFERENCE.md
§2) and write it to the correct vault folder, transcript kept at FULL LENGTH.
Splitting rule: only a multi-topic journal/musing may become >1 note; every
other type is always one note."""
from __future__ import annotations

import re
from pathlib import Path

# Note type → vault folder. musing/learning each get their own folder;
# insight goes to wiki/ — a user-managed top-level folder (beside the numbered
# folders, not nested under raw/) that IS a real pipeline write destination.
# raw/ stays entirely user-managed; the pipeline never writes there.
# Keep this dict in sync with the type→folder table in SCHEMA-REFERENCE.md §9.
TYPE_FOLDER = {
    "journal": "01-Journal",
    "musing": "02-Musings",
    "learning": "03-Learnings",
    "insight": "wiki",
    "resource": "04-Resources",
    "project": "05-Projects",
    "todo": "06-Todos",
    "person": "07-People",
    "reflection": "08-Reflections",
    "decision": "09-Decisions",
    "principle": "10-Principles",
    "company": "11-Companies",
}
INBOX_FOLDER = "00-Inbox"

# Initial status per type (SCHEMA-REFERENCE.md §6 lifecycles).
STATUS_INITIAL = {
    "resource": "inbox", "decision": "open", "todo": "open", "project": "active",
    "person": "active", "musing": "active", "learning": "active", "insight": "active",
    "journal": "active", "reflection": "active", "principle": "active",
    "company": "active",
}

# Only these types may split a genuinely multi-topic recording (SCHEMA §8).
SPLITTABLE = {"journal", "musing"}


def _kebab(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug[:60] or "note"


# Frontmatter values come from a language model and from scraped page metadata,
# neither of which owes us well-formed YAML. A title carrying a newline, or a
# tag carrying a colon, rewrites the block: the remainder of the value becomes a
# bogus top-level key and every field after it is lost. The parser is
# line-based, so the damage is silent — the note just quietly loses its type or
# its id. Hence: collapse to one line, and quote anything YAML would read as
# structure. Values that are already plain (ordinary titles, bare URLs) are left
# byte-identical, so this changes no existing note.
_NEEDS_QUOTE = re.compile(r": | #|:$")
_INDICATORS = tuple("-?:,[]{}#&*!|>'\"%@`")


def _scalar(value: object) -> str:
    """One frontmatter scalar, safe to interpolate after `key: `."""
    text = re.sub(r"\s+", " ", str("" if value is None else value)).strip()
    if not text:
        return ""
    if text.startswith(_INDICATORS) or _NEEDS_QUOTE.search(text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _wikilink(value: object) -> str:
    """A [[wikilink]] target: one line, and no bracket or quote that would
    escape the link and take the rest of the block with it."""
    text = re.sub(r"\s+", " ", str("" if value is None else value)).strip()
    return re.sub(r'[\[\]"]', "", text)


def stamp_field(fm_block: str, key: str, value: str) -> str:
    """Set a column-0 scalar frontmatter field in `fm_block`, appending it if
    the note doesn't carry it yet. The block is the raw text between the ---
    fences. Every other line, including list values, is left untouched."""
    out, found = [], False
    for line in fm_block.splitlines():
        if line.startswith(f"{key}:"):
            out.append(f"{key}: {value}".rstrip())
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}: {value}".rstrip())
    return "\n".join(out)


def _yaml_links(values: list[str]) -> str:
    links = [_wikilink(v) for v in (values or [])]
    links = [v for v in links if v]
    if not links:
        return "[]"
    return "\n" + "\n".join(f'  - "[[{v}]]"' for v in links)


def _yaml_list(values: list[str]) -> str:
    items = [_scalar(v) for v in (values or [])]
    items = [v for v in items if v]
    if not items:
        return "[]"
    return "\n" + "\n".join(f"  - {v}" for v in items)


def build_frontmatter(item, cls, duration_min: int | None = None) -> str:
    """cls is a classify.Classification. Body transcript is human-origin; AI-added
    metadata is flagged via meta_origin (SCHEMA §1 provenance firewall)."""
    note_id = item.captured.strftime("%Y%m%d%H%M%S")
    created = item.captured.strftime("%Y-%m-%d")
    if cls.needs_review:
        status = "needs-review"
    else:
        status = STATUS_INITIAL.get(cls.type, "active")
    meta_origin = "ai" if cls.routed_by == "llm" else "human"
    lines = [
        "---",
        f"id: {note_id}",
        f"type: {cls.type}",
        f"created: {created}",
        f"source: {item.source}",
        "origin: human",
        f"meta_origin: {meta_origin}",
        f"status: {status}",
        f"categories: {_yaml_links(cls.categories)}",
        f"subjects: {_yaml_links(cls.subjects)}",
        f"tags: {_yaml_list(cls.tags)}",
    ]
    if duration_min is not None:
        # how long the recording ran — the one audio fact worth keeping in
        # frontmatter, so a 2-hour meeting reads differently from a 40-second memo
        lines.append(f"duration_min: {duration_min}")
    lines.append("---")
    return "\n".join(lines)


def route(item, cls, transcript: str, vault_path: Path,
          duration_min: int | None = None) -> list[Path]:
    """Write the note(s) and return the paths written."""
    folder = INBOX_FOLDER if cls.needs_review else TYPE_FOLDER.get(cls.type, INBOX_FOLDER)
    dest_dir = Path(vault_path) / folder
    dest_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = build_frontmatter(item, cls, duration_min)
    created = item.captured.strftime("%Y-%m-%d")
    base = f"{created}-{_kebab(cls.title)}"
    path = dest_dir / f"{base}.md"
    i = 1
    while path.exists():
        i += 1
        path = dest_dir / f"{base}-{i}.md"

    # ponytail: single-note write. Multi-topic splitting of journal/musing needs an
    # LLM topic-segmenter — deferred (see DEFERRED.md); SPLITTABLE guards the seam.
    path.write_text(f"{frontmatter}\n\n{transcript.rstrip()}\n", encoding="utf-8")
    return [path]
