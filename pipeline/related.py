""""Past-you thought this too" — a cheap related-note signal at classification
time (Pass R, B7). Self-contained substring scan, same reasoning as
pipeline/resurface.py: this package never imports from api/, so this
duplicates a minimal frontmatter reader rather than reusing api/notes.py's.

Pass I: `embeddings_db`, when given, tries a semantic match first via
pipeline/embeddings.py — embed title+body, cosine-query embeddings.db, take
the best hit that isn't `exclude_id` — and only falls through to the
substring scan below when that comes up empty (no key, no embeddings.db rows
yet, or every semantic hit was excluded). On a cockpit with no
OPENAI_API_KEY set, embed_text degrades to None silently, so this is
identical to the substring-only behavior from Pass R."""
from __future__ import annotations

import re
from pathlib import Path

from . import embeddings

_EXCLUDED_FOLDERS = {"raw", "_System"}
_MIN_WORD_LEN = 4
_WORD_RE = re.compile(r"[A-Za-z]+")
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")

# Below this cosine score, two notes are more likely coincidentally nearby
# in embedding space than genuinely related — matches this cockpit's other
# confidence-floor precedent (api/notes.py::drain_review's classify floor,
# also 0.5). Without a floor, the semantic path always returns SOME hit
# (embeddings.query never comes back empty once there are >=2 notes
# indexed), so every classified note would get a "Past-you thought this
# too" stamp even when the nearest neighbor isn't meaningfully related.
_MIN_SEMANTIC_SIMILARITY = 0.5


def _read_note(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    fm: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            fm[k.strip()] = v
    return fm, parts[2]


def find(vault: Path, title: str, body: str, exclude_id: str, *,
        embeddings_db: Path | None = None) -> dict | None:
    """One prior note whose title, frontmatter, or body shares a significant
    (>=4 char) word with `title` — ranked title-match > frontmatter-match >
    body-match, same philosophy as api/notes.py::search_vault. Deterministic:
    ties broken by file path order (the vault walk is always sorted).

    When `embeddings_db` is given, tries a semantic match first (embeds
    title+body, cosine-queries embeddings.db, takes the best hit that isn't
    `exclude_id` AND meets the `_MIN_SEMANTIC_SIMILARITY` floor) and only
    falls through to the substring scan below when that comes up empty — no
    key, no embeddings.db rows yet, every semantic hit was excluded, or the
    best hit's cosine score was below the floor. This keeps behavior
    identical to before this task on a cockpit with no OPENAI_API_KEY set:
    embed_text and query both degrade to "nothing found" silently, so the
    substring path underneath is untouched and still runs exactly as it did
    in Pass R."""
    if embeddings_db is not None:
        vector = embeddings.embed_text(f"{title}\n\n{body[:1500]}")
        if vector is not None:
            for hit_id, hit_title, _path, score in embeddings.query(embeddings_db, vector, k=5):
                if hit_id != exclude_id and score >= _MIN_SEMANTIC_SIMILARITY:
                    return {"id": hit_id, "title": hit_title}

    words = {w.lower() for w in _WORD_RE.findall(title) if len(w) >= _MIN_WORD_LEN}
    if not words:
        return None

    best: tuple[int, str, dict] | None = None
    for path in sorted(vault.rglob("*.md")):
        rel_parts = path.relative_to(vault).parts
        if not rel_parts or rel_parts[0] in _EXCLUDED_FOLDERS:
            continue
        if any(part.startswith(".") for part in rel_parts):
            continue
        text = _read_note(path)
        if text is None:
            continue
        fm, note_body = _parse_frontmatter(text)
        note_id = fm.get("id", "")
        if not note_id or note_id == exclude_id:
            continue

        title_lower = _DATE_PREFIX_RE.sub("", path.stem).lower()
        rank = None
        if any(w in title_lower for w in words):
            rank = 0
        elif any(w in str(v).lower() for k, v in fm.items() if k != "id" for w in words):
            rank = 1
        elif any(w in note_body.lower() for w in words):
            rank = 2
        if rank is None:
            continue

        candidate = (rank, str(path))
        if best is None or candidate < (best[0], best[1]):
            best = (rank, str(path), {
                "id": note_id,
                "title": _DATE_PREFIX_RE.sub("", path.stem),
            })
    return best[2] if best else None
