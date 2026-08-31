"""POST /api/review/{id}/approve confirming attendees on a conversation note —
the human half of the suggest-then-confirm flow (CLAUDE.md §3): the pipeline
only ever suggests (pipeline/watcher.py, events.db), this is the one place
`attendees:` is ever filled in and the one place a person's interaction log
gets the dated line.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from api import notes
from pipeline import relationships


def _seed_events(db: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " timestamp TEXT NOT NULL, file TEXT NOT NULL, stage TEXT NOT NULL,"
        " status TEXT NOT NULL, duration_ms INTEGER, message TEXT, plain_english_error TEXT)")
    for r in rows:
        conn.execute(
            "INSERT INTO events (timestamp, file, stage, status, duration_ms, message,"
            " plain_english_error) VALUES (?,?,?,?,?,?,?)",
            (r["timestamp"], r["file"], r["stage"], r["status"], r.get("duration_ms"),
             r.get("message", ""), r.get("plain_english_error", "")))
    conn.commit()
    conn.close()


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    (v / "00-Inbox").mkdir(parents=True)
    return v


def _conversation_note(vault: Path, note_id: str = "20260830090000") -> Path:
    path = vault / "00-Inbox" / "2026-08-30-product-sync.md"
    path.write_text(
        f"---\nid: {note_id}\ntype: conversation\ncreated: 2026-08-30\nsource: plaud\n"
        "origin: human\nmeta_origin: human\nstatus: needs-review\ncategories: []\n"
        "subjects: []\ntags: []\nattendees: []\nspeakers:\n  - Ana Silva\n  - Ben Carter\n"
        "transcript_source: plaud\n---\n\n[00:01] Ana Silva: let's begin\n"
        "[00:04] Ben Carter: sounds good\n", encoding="utf-8")
    return path


def _person(vault: Path, name: str) -> str:
    p = relationships.create_person(vault, name, "email", f"{name.lower().replace(' ', '.')}@x.com",
                                    when=datetime(2026, 1, 1))
    return p.id


def test_approve_with_no_attendees_behaves_exactly_as_before(vault):
    """The plain, existing contract — no attendees key at all."""
    _conversation_note(vault)
    dest = notes.approve(vault, "20260830090000", "conversation")
    assert dest == "12-Conversations/2026-08-30-product-sync.md"
    assert "attendees: []" in (vault / dest).read_text(encoding="utf-8")


def test_approve_confirms_attendees_and_logs_the_interaction(vault):
    ana_id = _person(vault, "Ana Silva")
    ben_id = _person(vault, "Ben Carter")
    _conversation_note(vault)

    dest = notes.approve(vault, "20260830090000", "conversation", [ana_id, ben_id])
    fm_text = (vault / dest).read_text(encoding="utf-8")
    assert f'"[[{ana_id}]]"' in fm_text
    assert f'"[[{ben_id}]]"' in fm_text

    ana_note = relationships.find_person(vault, ana_id).path.read_text(encoding="utf-8")
    # the note line names the destination's (kebab) title and links back to
    # the conversation note by its immutable id, matching the todos convention
    assert "Conversation: product-sync ([[20260830090000]])" in ana_note
    # log_contact stamps the CONFIRMATION date, not the recording's capture
    # date — a person's interaction log reflects when it was reviewed
    import datetime as _dt
    assert f"last_contact: {_dt.date.today().isoformat()}" in ana_note


def test_approve_skips_a_stale_or_unknown_attendee_id_without_failing(vault):
    real_id = _person(vault, "Ana Silva")
    _conversation_note(vault)
    dest = notes.approve(vault, "20260830090000", "conversation",
                         [real_id, "20009999999999"])   # second id doesn't exist
    fm_text = (vault / dest).read_text(encoding="utf-8")
    assert f'"[[{real_id}]]"' in fm_text
    assert "20009999999999" not in fm_text


def test_approve_ignores_attendees_for_a_non_conversation_type(vault):
    """attendees only ever means anything for type: conversation."""
    person_id = _person(vault, "Ana Silva")
    path = vault / "00-Inbox" / "2026-08-30-a-thought.md"
    path.write_text(
        "---\nid: 20260830090000\ntype: musing\ncreated: 2026-08-30\nsource: voice\n"
        "origin: human\nmeta_origin: ai\nstatus: needs-review\ncategories: []\n"
        "subjects: []\ntags: []\n---\n\nsome thought\n", encoding="utf-8")
    before = relationships.find_person(vault, person_id).path.read_text(encoding="utf-8")
    dest = notes.approve(vault, "20260830090000", "musing", [person_id])
    assert "attendees" not in (vault / dest).read_text(encoding="utf-8")
    after = relationships.find_person(vault, person_id).path.read_text(encoding="utf-8")
    assert before == after   # untouched — no confirmation happened


def test_a_failed_note_move_never_touches_a_person_note(vault, monkeypatch):
    """Ordering guard: attendees are only confirmed AFTER the note is safely
    filed. If the move fails, nothing about a person may have changed."""
    person_id = _person(vault, "Ana Silva")
    _conversation_note(vault)
    before = relationships.find_person(vault, person_id).path.read_text(encoding="utf-8")

    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(notes.os, "replace", boom)

    with pytest.raises(OSError):
        notes.approve(vault, "20260830090000", "conversation", [person_id])
    after = relationships.find_person(vault, person_id).path.read_text(encoding="utf-8")
    assert before == after


# ---- GET /api/review surfacing suggestions ----------------------------------

def test_list_review_surfaces_suggested_attendees_by_name(vault):
    person_id = _person(vault, "Ana Silva")
    _conversation_note(vault)
    db = vault.parent / "events.db"
    _seed_events(db, [
        {"timestamp": "2026-08-30T09:00:00", "file": "/inbox/x.ogg", "stage": "attendees",
         "status": "ok", "message": json.dumps({"suggested": {"Ana Silva": person_id}})},
        {"timestamp": "2026-08-30T09:00:01", "file": "/inbox/x.ogg", "stage": "route",
         "status": "ok", "message": "wrote 2026-08-30-product-sync.md"},
    ])
    items = notes.list_review(vault, db)
    assert len(items) == 1
    suggested = items[0]["suggested_attendees"]
    assert suggested == [{"id": person_id, "label": "Ana Silva", "name": "Ana Silva"}]


def test_list_review_suggested_attendees_is_empty_for_non_conversation_notes(vault):
    path = vault / "00-Inbox" / "2026-08-30-a-thought.md"
    path.write_text(
        "---\nid: 1\ntype: musing\ncreated: 2026-08-30\nsource: voice\n"
        "origin: human\nmeta_origin: ai\nstatus: needs-review\ncategories: []\n"
        "subjects: []\ntags: []\n---\n\nsome thought\n", encoding="utf-8")
    items = notes.list_review(vault, vault.parent / "events.db")
    assert items[0]["suggested_attendees"] == []


def test_list_review_with_an_unmatched_speaker_has_no_id(vault):
    _conversation_note(vault)
    db = vault.parent / "events.db"
    _seed_events(db, [
        {"timestamp": "2026-08-30T09:00:00", "file": "/inbox/x.ogg", "stage": "route",
         "status": "ok", "message": "wrote 2026-08-30-product-sync.md"},
    ])
    items = notes.list_review(vault, db)
    assert items[0]["suggested_attendees"] == []   # no attendees event logged at all
