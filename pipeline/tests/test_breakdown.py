"""Task R5 tests: micro-step breakdown (B10) — child-line format, tree-nested
scan(), rollup toggle() in both directions, add_breakdown(). Hermetic — real
tmp vault, no conftest.py (matches this package's existing idiom)."""
from __future__ import annotations

import pytest

from pipeline import todos


def _write(vault, name: str, text: str):
    todos_dir = vault / todos.TODOS_FOLDER
    todos_dir.mkdir(parents=True, exist_ok=True)
    (todos_dir / name).write_text(text, encoding="utf-8")


@pytest.fixture
def vault(tmp_path):
    return tmp_path / "vault"


# ---- 1. child-line format round-trip ---------------------------------------

def test_child_line_round_trip():
    line = todos.format_child_line("book the room", "20260901100000-1a")
    parsed = todos.parse_line(line)
    assert parsed is not None
    task, done, due, time, block, indent, feel = parsed
    assert task == "book the room"
    assert done is False
    assert block == "20260901100000-1a"
    assert indent is True          # a child line marks itself as indented
    assert feel is None


def test_child_line_does_not_match_as_top_level():
    """A child line only ever appears at 4-space indent; parse_line still
    matches it (scan() is what treats indent=True specially), but the indent
    flag it returns must be True, never mistaken for a parent line."""
    line = todos.format_child_line("draft the agenda", "20260901100000-1b")
    assert not line.startswith("- [")   # genuinely indented, not top-level
    parsed = todos.parse_line(line)
    assert parsed[5] is True


# ---- 2. scan() nests children under their immediately-preceding parent -----

def test_scan_nests_children_under_parent(vault):
    _write(vault, "2026-09-01.md",
           "- [ ] plan the offsite (from [[20260901100000]]) ^20260901100000-1 🎚4\n"
           "    - [ ] book the room ^20260901100000-1a\n"
           "    - [x] draft the agenda ^20260901100000-1b\n")
    items = todos.scan(vault)
    assert len(items) == 1
    parent = items[0]
    assert parent.block_id == "20260901100000-1"
    assert parent.feel == 4
    assert len(parent.children) == 2
    assert parent.children[0].task == "book the room"
    assert parent.children[0].block_id == "20260901100000-1a"
    assert parent.children[0].done is False
    assert parent.children[1].task == "draft the agenda"
    assert parent.children[1].block_id == "20260901100000-1b"
    assert parent.children[1].done is True


def test_scan_skips_orphaned_indented_line_without_crashing(vault):
    """An indented checkbox with no preceding top-level line — shouldn't
    happen in practice, but scan() must not crash on it."""
    _write(vault, "2026-09-01.md",
           "    - [ ] a stray indented line ^orphan-a\n"
           "- [ ] a normal todo ^normal-1\n")
    items = todos.scan(vault)
    assert [i.block_id for i in items] == ["normal-1"]
    assert items[0].children == []


# ---- 3 & 9. add_breakdown() + feel-marker round-trip ------------------------

def test_add_breakdown_inserts_children_and_feel_marker(vault):
    _write(vault, "2026-09-01.md",
           "- [ ] plan the offsite (from [[20260901100000]]) ^20260901100000-1\n")
    updated = todos.add_breakdown(
        vault, "20260901100000-1", 3,
        ["book the room", "draft the agenda", "invite the team"])

    assert updated.feel == 3
    assert [c.block_id for c in updated.children] == [
        "20260901100000-1a", "20260901100000-1b", "20260901100000-1c"]
    assert [c.task for c in updated.children] == [
        "book the room", "draft the agenda", "invite the team"]

    text = (vault / todos.TODOS_FOLDER / "2026-09-01.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0].endswith("^20260901100000-1 🎚3")
    assert lines[1] == "    - [ ] book the room ^20260901100000-1a"
    assert lines[2] == "    - [ ] draft the agenda ^20260901100000-1b"
    assert lines[3] == "    - [ ] invite the team ^20260901100000-1c"

    # re-scan confirms the tree
    rescanned = next(t for t in todos.scan(vault) if t.block_id == "20260901100000-1")
    assert rescanned.feel == 3 and len(rescanned.children) == 3


def test_feel_marker_round_trip():
    with_feel = todos.parse_line("- [ ] plan the offsite ^20260901100000-1 🎚4")
    assert with_feel[6] == 4
    without_feel = todos.parse_line("- [ ] plan the offsite ^20260901100000-1")
    assert without_feel[6] is None


# ---- 4 & 5. add_breakdown() error paths -------------------------------------

def test_add_breakdown_already_has_children_raises_value_error(vault):
    _write(vault, "2026-09-01.md",
           "- [ ] plan the offsite ^20260901100000-1\n"
           "    - [ ] book the room ^20260901100000-1a\n")
    with pytest.raises(ValueError):
        todos.add_breakdown(vault, "20260901100000-1", 3, ["a", "b"])


def test_add_breakdown_unknown_id_raises_lookup_error(vault):
    _write(vault, "2026-09-01.md", "- [ ] plan the offsite ^20260901100000-1\n")
    with pytest.raises(LookupError):
        todos.add_breakdown(vault, "nope", 3, ["a", "b"])


# ---- 6 & 7. rollup toggle() both directions ---------------------------------

def test_rollup_forward_all_children_done_marks_parent(vault):
    _write(vault, "2026-09-01.md",
           "- [ ] plan the offsite ^20260901100000-1\n"
           "    - [ ] book the room ^20260901100000-1a\n"
           "    - [ ] draft the agenda ^20260901100000-1b\n")

    todos.toggle(vault, "20260901100000-1a")
    parent = next(t for t in todos.scan(vault) if t.block_id == "20260901100000-1")
    assert parent.done is False   # one sibling still open

    todos.toggle(vault, "20260901100000-1b")
    parent = next(t for t in todos.scan(vault) if t.block_id == "20260901100000-1")
    assert parent.done is True    # both siblings done → parent auto-flips


def test_rollup_reverse_reopen_child_unmarks_parent(vault):
    _write(vault, "2026-09-01.md",
           "- [x] plan the offsite ^20260901100000-1\n"
           "    - [x] book the room ^20260901100000-1a\n"
           "    - [x] draft the agenda ^20260901100000-1b\n")

    todos.toggle(vault, "20260901100000-1a")   # reopen one sibling
    parent = next(t for t in todos.scan(vault) if t.block_id == "20260901100000-1")
    assert parent.done is False               # parent auto-flips back open

    child = next(c for c in parent.children if c.block_id == "20260901100000-1a")
    assert child.done is False
    other = next(c for c in parent.children if c.block_id == "20260901100000-1b")
    assert other.done is True                 # the untouched sibling is unaffected


# ---- 8. toggling a parent directly never cascades to children --------------

def test_toggling_parent_directly_does_not_cascade_to_children(vault):
    _write(vault, "2026-09-01.md",
           "- [ ] plan the offsite ^20260901100000-1\n"
           "    - [ ] book the room ^20260901100000-1a\n"
           "    - [ ] draft the agenda ^20260901100000-1b\n")

    todos.toggle(vault, "20260901100000-1")
    parent = next(t for t in todos.scan(vault) if t.block_id == "20260901100000-1")
    assert parent.done is True
    assert all(not c.done for c in parent.children)   # untouched
