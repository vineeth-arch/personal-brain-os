"""Pass Q — whole-vault search. A filesystem scan against a seeded tmp vault;
no SQLite note-content index (CLAUDE.md §1), so these tests write real .md
files and read them back through the real route."""
from __future__ import annotations

from pathlib import Path

import pytest

from api import notes


def _write(vault: Path, folder: str, name: str, fm: dict, body: str) -> Path:
    d = vault / folder
    d.mkdir(parents=True, exist_ok=True)
    fm_lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
    path = d / name
    path.write_text(f"---\n{fm_lines}\n---\n\n{body}\n", encoding="utf-8")
    return path


@pytest.fixture
def vault(tmp_path):
    return tmp_path / "vault"


def test_title_match_ranks_above_body_match(vault):
    _write(vault, "02-Musings", "a.md",
          {"id": "1", "type": "musing", "title": "branding hook for the launch"},
          "some unrelated body text")
    _write(vault, "03-Learnings", "b.md",
          {"id": "2", "type": "learning", "title": "unrelated"},
          "we need a good branding hook here too")
    results = notes.search_vault(vault, "branding hook")
    assert [r["id"] for r in results] == ["1", "2"]
    assert results[0]["matched_in"] == "title"
    assert results[1]["matched_in"] == "body"


def test_matches_in_the_insight_body(vault):
    _write(vault, "04-Resources", "r.md",
          {"id": "3", "type": "resource", "title": "some article"},
          "## Insight\n\nthe garden trellis design was clever")
    results = notes.search_vault(vault, "trellis")
    assert len(results) == 1 and results[0]["id"] == "3"
    assert "trellis" in results[0]["excerpt"].lower()


def test_matches_a_persons_interaction_log(vault):
    _write(vault, "07-People", "p.md",
          {"id": "4", "type": "person", "title": "Priya Raman"},
          "## Interaction log\n\n- 2026-07-20 — talked about the studio residency")
    results = notes.search_vault(vault, "residency")
    assert len(results) == 1 and results[0]["id"] == "4"


def test_frontmatter_value_match(vault):
    _write(vault, "04-Resources", "r.md",
          {"id": "5", "type": "resource", "title": "weeknight dal", "author": "Priya Krishnan"},
          "a recipe")
    results = notes.search_vault(vault, "krishnan")
    assert len(results) == 1 and results[0]["matched_in"] == "frontmatter"


def test_raw_and_system_folders_are_excluded(vault):
    _write(vault, "raw", "transcript.md", {"id": "6", "type": "musing", "title": "x"},
          "the word findme is in here")
    _write(vault, "_System", "log.md", {"id": "7", "type": "musing", "title": "x"},
          "the word findme is in here too")
    assert notes.search_vault(vault, "findme") == []


def test_hidden_folders_are_excluded(vault):
    _write(vault, ".trash", "deleted.md", {"id": "8", "type": "musing", "title": "findme"}, "body")
    assert notes.search_vault(vault, "findme") == []


def test_case_insensitive(vault):
    _write(vault, "02-Musings", "a.md", {"id": "9", "type": "musing", "title": "Spaced Repetition"}, "b")
    results = notes.search_vault(vault, "SPACED repetition")
    assert len(results) == 1


def test_a_file_without_frontmatter_is_skipped_not_crashed_on(vault):
    (vault / "02-Musings").mkdir(parents=True)
    (vault / "02-Musings" / "not-a-note.md").write_text("no frontmatter here, findme", encoding="utf-8")
    assert notes.search_vault(vault, "findme") == []


def test_no_matches_is_an_empty_list(vault):
    _write(vault, "02-Musings", "a.md", {"id": "1", "type": "musing", "title": "x"}, "y")
    assert notes.search_vault(vault, "nothing-matches-this") == []


def test_limit_caps_results(vault):
    for i in range(5):
        _write(vault, "02-Musings", f"a{i}.md",
              {"id": str(i), "type": "musing", "title": f"findme note {i}"}, "body")
    assert len(notes.search_vault(vault, "findme", limit=3)) == 3
