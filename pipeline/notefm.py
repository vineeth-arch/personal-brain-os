"""Flat-frontmatter read/write surgery, shared by the pipeline and the API.

These started life in api/notes.py, but the pipeline can't import from api/
(the dependency runs api → pipeline, never back), and dex.py and search.py both
need to read and stamp frontmatter. They live here now; api/notes.py imports
and re-exports them, so its call sites are unchanged.

The format is the one pipeline/route.py writes: column-0 scalars between two
'---' fences. List values are indented and deliberately skipped by the parser —
nothing in the app treats categories/subjects/tags as data, and hand-rolling a
YAML parser to read them would be a dependency in disguise (CLAUDE.md §7).
Every mutation here is line surgery precisely so that a field the app doesn't
understand survives a write untouched.
"""
from __future__ import annotations


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Flat top-level keys only (list values are indented and skipped), matching
    the format route.build_frontmatter writes."""
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    fm: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, parts[2]


def split_note(text: str) -> tuple[str, str] | None:
    """(frontmatter_block, body) or None when there's no frontmatter. The block
    is the raw text between the '---' fences (no fences, keeps inner newlines)."""
    if not text.startswith("---\n"):
        return None
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return None
    return parts[1], parts[2]


def compose_note(fm_block: str, body: str) -> str:
    return "---\n" + fm_block.rstrip("\n") + "\n---\n\n" + body.strip() + "\n"


def stamp_field(fm_block: str, key: str, value: str) -> str:
    """Set a column-0 scalar frontmatter field, appending it if absent."""
    lines = fm_block.splitlines()
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{key}:"):
            out.append(f"{key}: {value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}: {value}")
    return "\n".join(out)
