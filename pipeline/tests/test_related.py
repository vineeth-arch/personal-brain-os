"""Pass R (B7): the related-note substring scan. Real tmp vault, no
conftest.py — matching this repo's per-file fixture style (see
pipeline/tests/test_resurface.py)."""
from __future__ import annotations

from pathlib import Path

from pipeline import related


def _note(vault: Path, folder: str, note_id: str, slug: str,
         body: str = "Some content.\n\nMore.", extra_fm: str = "") -> Path:
    d = vault / folder
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"2026-01-01-{slug}.md"
    path.write_text(
        f"---\nid: {note_id}\ntype: musing\ncreated: 2026-01-01\nstatus: active\n"
        f"{extra_fm}---\n\n{body}\n", encoding="utf-8")
    return path


def test_shared_title_word_finds_the_other_note(tmp_path):
    vault = tmp_path / "vault"
    _note(vault, "02-Musings", "n1", "warehouse-visit-notes")
    result = related.find(vault, "Warehouse logistics plan", "no shared word here", "n2")
    assert result == {"id": "n1", "title": "warehouse-visit-notes"}


def test_no_shared_words_returns_none(tmp_path):
    vault = tmp_path / "vault"
    _note(vault, "02-Musings", "n1", "totally-unrelated-thing")
    assert related.find(vault, "completely different topic", "nothing overlaps", "n2") is None


def test_note_never_matches_itself(tmp_path):
    vault = tmp_path / "vault"
    # The only candidate carries the SAME id passed as exclude_id — even
    # though its title/body would otherwise match, it must never come back.
    _note(vault, "02-Musings", "n1", "warehouse-visit-notes")
    assert related.find(vault, "Warehouse logistics plan", "warehouse again", "n1") is None


def test_title_match_beats_body_only_match(tmp_path):
    vault = tmp_path / "vault"
    # body-only match: the word appears in the body, not the title
    _note(vault, "02-Musings", "body-only", "some-other-slug",
         body="This note mentions warehouse somewhere in the body.")
    # title match: the word appears in the title itself
    _note(vault, "03-Learnings", "title-match", "warehouse-inspection")
    result = related.find(vault, "Warehouse logistics plan", "no match text here", "exclude-me")
    assert result == {"id": "title-match", "title": "warehouse-inspection"}


def test_raw_and_system_and_dotted_paths_excluded(tmp_path):
    vault = tmp_path / "vault"
    _note(vault, "raw", "n1", "warehouse-recording")
    _note(vault, "_System", "n2", "warehouse-log")
    dotted = vault / ".obsidian" / "workspace"
    dotted.parent.mkdir(parents=True, exist_ok=True)
    dotted_note = vault / ".obsidian" / "2026-01-01-warehouse.md"
    dotted_note.write_text(
        "---\nid: n3\ntype: musing\ncreated: 2026-01-01\nstatus: active\n---\n\nbody\n",
        encoding="utf-8")
    assert related.find(vault, "Warehouse logistics plan", "no shared body text", "exclude") is None
