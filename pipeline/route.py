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
}
INBOX_FOLDER = "00-Inbox"

# Initial status per type (SCHEMA-REFERENCE.md §6 lifecycles).
STATUS_INITIAL = {
    "resource": "inbox", "decision": "open", "todo": "open", "project": "active",
    "person": "active", "musing": "active", "learning": "active", "insight": "active",
    "journal": "active", "reflection": "active", "principle": "active",
}

# Only these types may split a genuinely multi-topic recording (SCHEMA §8).
SPLITTABLE = {"journal", "musing"}


def _kebab(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug[:60] or "note"


def _yaml_links(values: list[str]) -> str:
    if not values:
        return "[]"
    return "\n" + "\n".join(f'  - "[[{v}]]"' for v in values)


def _yaml_list(values: list[str]) -> str:
    if not values:
        return "[]"
    return "\n" + "\n".join(f"  - {v}" for v in values)


def build_frontmatter(item, cls) -> str:
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
        "---",
    ]
    return "\n".join(lines)


def route(item, cls, transcript: str, vault_path: Path) -> list[Path]:
    """Write the note(s) and return the paths written."""
    folder = INBOX_FOLDER if cls.needs_review else TYPE_FOLDER.get(cls.type, INBOX_FOLDER)
    dest_dir = Path(vault_path) / folder
    dest_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = build_frontmatter(item, cls)
    created = item.captured.strftime("%Y-%m-%d")
    base = f"{created}-{_kebab(cls.title)}"
    path = dest_dir / f"{base}.md"
    i = 1
    while path.exists():
        i += 1
        path = dest_dir / f"{base}-{i}.md"

    # ponytail: single-note write. Multi-topic splitting of journal/musing needs an
    # LLM topic-segmenter — deferred (see DEFERRED.md); SPLITTABLE guards the seam.
    path.write_text(f"{frontmatter}\n\n{transcript.rstrip()}\n")
    return [path]
