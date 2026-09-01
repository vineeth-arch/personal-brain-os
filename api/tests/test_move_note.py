"""Task A5: focused coverage of `_move_note`'s collision-suffix and
rollback-on-unlink-failure paths — both extracted out of `approve()` verbatim,
so they're exercised here through `approve()` itself (same public surface the
rest of the suite already drives), matching test_approve_attendees.py's
direct-import idiom rather than the full HTTP Server harness."""
from __future__ import annotations

from pathlib import Path

import pytest

from api import notes


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    (v / "00-Inbox").mkdir(parents=True)
    return v


def _note(path: Path, note_id: str, ntype: str, body: str = "body") -> None:
    path.write_text(
        f"---\nid: {note_id}\ntype: {ntype}\ncreated: 2026-08-30\nsource: voice\n"
        f"origin: human\nmeta_origin: ai\nstatus: needs-review\ncategories: []\n"
        f"subjects: []\ntags: []\n---\n\n{body}\n", encoding="utf-8")


def test_approve_moves_around_a_filename_collision(vault):
    """A note already sitting at the destination filename (e.g. a same-named
    note filed earlier) must not be clobbered — the mover appends a
    collision-suffix instead, same behaviour before and after the extraction."""
    dest_dir = vault / "03-Learnings"
    dest_dir.mkdir(parents=True)
    (dest_dir / "2026-08-30-walk.md").write_text("pre-existing note\n", encoding="utf-8")

    _note(vault / "00-Inbox" / "2026-08-30-walk.md", "20260830090000", "musing", "the new note")
    dest = notes.approve(vault, "20260830090000", "learning")

    assert dest == "03-Learnings/2026-08-30-walk-2.md"
    assert (dest_dir / "2026-08-30-walk.md").read_text(encoding="utf-8") == "pre-existing note\n"
    assert "the new note" in (dest_dir / "2026-08-30-walk-2.md").read_text(encoding="utf-8")


def test_approve_rolls_back_the_destination_when_the_source_unlink_fails(vault, monkeypatch):
    """If the inbox copy can't be removed after a successful write, the note
    must never exist twice under one immutable id — the destination copy is
    deleted and the OSError propagates rather than being swallowed."""
    _note(vault / "00-Inbox" / "2026-08-30-walk.md", "20260830090000", "musing", "body")

    original_unlink = Path.unlink

    def selective_boom(self, missing_ok=False):
        # only the INBOX copy fails to unlink — the rollback's own
        # dest.unlink(missing_ok=True) must still succeed normally, or the
        # rollback itself would be what's under test here, not the failure
        if "00-Inbox" in str(self):
            raise OSError("permission denied")
        return original_unlink(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", selective_boom)

    with pytest.raises(OSError):
        notes.approve(vault, "20260830090000", "learning")

    assert not (vault / "03-Learnings" / "2026-08-30-walk.md").exists()   # rolled back
    assert (vault / "00-Inbox" / "2026-08-30-walk.md").exists()           # source untouched
