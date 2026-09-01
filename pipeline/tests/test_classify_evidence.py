"""Task A1: evidence rides along in the classifier's JSON response, through the
classify event message, into api.notes.list_review — no second LLM call.
Hermetic: a stub classifier stands in for Haiku, no network needed. Per-file
fixture only (no conftest.py in this repo)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from api import notes
from pipeline import classify, watcher
from pipeline.config import Config

FIXTURES = Path(__file__).parent / "fixtures"


def make_config():
    return SimpleNamespace(raw={}, confidence_threshold=0.7)


# ---- classify.classify() / validate_classification() ------------------------

def test_evidence_flows_from_llm_fn_into_classification():
    item = SimpleNamespace(tag=None, name="memo")
    cls = classify.classify(
        item, "no tag here", make_config(),
        llm_fn=lambda transcript, cfg: {
            "type": "learning", "confidence": 0.9, "title": "spaced-repetition",
            "categories": [], "subjects": [], "tags": [],
            "evidence": "mentions 'remind me' and a date",
        })
    assert cls.evidence == "mentions 'remind me' and a date"


def test_absent_evidence_defaults_to_empty_string():
    item = SimpleNamespace(tag=None, name="memo")
    cls = classify.classify(
        item, "no tag here", make_config(),
        llm_fn=lambda transcript, cfg: {
            "type": "learning", "confidence": 0.9, "title": "spaced-repetition",
            "categories": [], "subjects": [], "tags": [],
        })
    assert cls.evidence == ""


def test_validate_classification_trims_overlong_evidence():
    data = {"type": "learning", "confidence": 0.9, "title": "x", "evidence": "e" * 200}
    assert classify.validate_classification(data) is None
    assert data["evidence"] == "e" * classify.EVIDENCE_MAX_CHARS
    assert len(data["evidence"]) == 120


def test_validate_classification_leaves_short_evidence_untouched():
    data = {"type": "learning", "confidence": 0.9, "title": "x", "evidence": "short reason"}
    assert classify.validate_classification(data) is None
    assert data["evidence"] == "short reason"


def test_validate_classification_accepts_absent_evidence():
    data = {"type": "learning", "confidence": 0.9, "title": "x"}
    assert classify.validate_classification(data) is None
    assert "evidence" not in data


# ---- watcher: the classify event message -------------------------------------

def stub_classifier_with_evidence(transcript: str, config) -> dict:
    return {
        "type": "learning",
        "categories": ["Memory"],
        "subjects": ["Spaced repetition"],
        "tags": ["learning"],
        "confidence": 0.95,
        "title": "spaced repetition retrieval",
        "evidence": "mentions 'remind me' and a date",
    }


@pytest.fixture
def vault_env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    failed = tmp_path / "failed"
    for d in (vault, inbox, archive, failed):
        d.mkdir()
    (inbox / "plain.md").write_text((FIXTURES / "plain.md").read_text(encoding="utf-8"),
                                    encoding="utf-8")

    monkeypatch.setattr(watcher, "DB_PATH", tmp_path / "events.db")
    monkeypatch.setattr(watcher, "HEARTBEAT_PATH", tmp_path / ".watcher-heartbeat")

    config = Config(vault_path=vault, inbox_path=inbox, archive_path=archive, failed_path=failed)
    return config, tmp_path


def _classify_message(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT message FROM events WHERE stage = 'classify' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return row[0]


def test_watcher_event_message_carries_evidence(vault_env):
    config, tmp_path = vault_env
    from pipeline.events import EventLog
    events = EventLog(tmp_path / "events.db", config.vault_path)
    deps = watcher.Deps(transcriber=None, classifier_fn=stub_classifier_with_evidence)

    results = watcher.run_once(config, events, deps)
    assert all(r.status != "failed" for r in results), [r.error for r in results]

    message = _classify_message(tmp_path / "events.db")
    assert 'evidence="mentions \'remind me\' and a date"' in message
    assert "type=learning confidence=0.95 by=llm" in message


def test_watcher_event_message_omits_evidence_when_absent(vault_env):
    config, tmp_path = vault_env

    def stub_no_evidence(transcript: str, config) -> dict:
        return {"type": "learning", "categories": [], "subjects": [], "tags": [],
                "confidence": 0.95, "title": "spaced repetition retrieval"}

    from pipeline.events import EventLog
    events = EventLog(tmp_path / "events.db", config.vault_path)
    deps = watcher.Deps(transcriber=None, classifier_fn=stub_no_evidence)

    results = watcher.run_once(config, events, deps)
    assert all(r.status != "failed" for r in results), [r.error for r in results]

    message = _classify_message(tmp_path / "events.db")
    assert "evidence=" not in message


# ---- api.notes.list_review — evidence surfaced or null ----------------------

def _note(path: Path, note_id: str, ntype: str, body: str = "body") -> None:
    path.write_text(
        f"---\nid: {note_id}\ntype: {ntype}\ncreated: 2026-07-01\n"
        f"status: needs-review\n---\n\n{body}\n", encoding="utf-8")


def test_list_review_surfaces_evidence_or_null(tmp_path):
    vault = tmp_path / "vault"
    inbox_dir = vault / "00-Inbox"
    inbox_dir.mkdir(parents=True)
    db_path = tmp_path / "events.db"

    from pipeline.events import EventLog
    log = EventLog(db_path, vault)
    log.log("/in/walk.m4a", "classify", "needs_review",
           message="type=learning confidence=0.62 by=llm "
                   "evidence=\"mentions 'remind me' and a date\"")
    log.log("/in/walk.m4a", "route", "ok", message="wrote 2026-07-01-walk.md")
    log.log("/in/quiet.m4a", "classify", "needs_review",
           message="type=musing confidence=0.55 by=llm")
    log.log("/in/quiet.m4a", "route", "ok", message="wrote 2026-07-01-quiet.md")
    log.close()

    _note(inbox_dir / "2026-07-01-walk.md", "20260701090000", "learning")
    _note(inbox_dir / "2026-07-01-quiet.md", "20260701090100", "musing")

    items = notes.list_review(vault, db_path)
    by_id = {i["id"]: i for i in items}
    assert by_id["20260701090000"]["evidence"] == "mentions 'remind me' and a date"
    assert by_id["20260701090000"]["confidence"] == 0.62
    assert by_id["20260701090100"]["evidence"] is None
    assert by_id["20260701090100"]["confidence"] == 0.55
