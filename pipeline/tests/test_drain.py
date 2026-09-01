"""Task A5: anti-guilt drain (Pass A, B5) — triage items sitting in 00-Inbox
for 14+ days are resolved automatically: filed at the classifier's best guess
when it was confident, parked out of the queue otherwise. Every filing is
revertible with one `git revert`, and the whole run is ONE commit.

Follows this repo's established fixture style (no conftest.py; SimpleNamespace
fake configs; fake dates threaded via `now=`) and the classify-join seeding
idiom already established in pipeline/tests/test_classify_evidence.py
(`events.log(source_file, "classify", ..., message="type=X confidence=Y")`
followed by a matching `events.log(source_file, "route", "ok",
message="wrote <name>.md")`)."""
from __future__ import annotations

import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from api import notes
from pipeline import todos, watcher
from pipeline.events import EventLog

NOW = date(2026, 8, 31)
OLD_CREATED = (NOW - timedelta(days=15)).isoformat()     # older than the 14-day floor
FRESH_CREATED = (NOW - timedelta(days=3)).isoformat()     # under the floor


def _note(path: Path, note_id: str, ntype: str, created: str, body: str = "body") -> None:
    path.write_text(
        f"---\nid: {note_id}\ntype: {ntype}\ncreated: {created}\nsource: voice\n"
        f"origin: human\nmeta_origin: ai\nstatus: needs-review\ncategories: []\n"
        f"subjects: []\ntags: []\n---\n\n{body}\n", encoding="utf-8")


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(vault), *args], check=True,
                          capture_output=True, text=True).stdout


def _init_git_vault(vault: Path) -> None:
    """The vault must have a PRIOR commit recording notes at their original
    path/content, or the drain's own commit would be the vault's first commit
    ever — with nothing earlier for `git revert` to restore back to."""
    _git(vault, "init", "-q")
    _git(vault, "config", "user.email", "t@example.com")
    _git(vault, "config", "user.name", "Test")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "--allow-empty", "-m", "initial vault state")


def _commit_count(vault: Path) -> int:
    return len(_git(vault, "log", "--oneline").splitlines())


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    (v / "00-Inbox").mkdir(parents=True)
    return v


@pytest.fixture
def events(tmp_path, vault):
    log = EventLog(tmp_path / "events.db", vault)
    yield log
    log.close()


def make_config(vault: Path, **raw_extra) -> SimpleNamespace:
    return SimpleNamespace(vault_path=vault, raw={**raw_extra})


# ---- 1. confident, stale → filed --------------------------------------------

def test_stale_high_confidence_item_is_filed(vault, events):
    inbox = vault / "00-Inbox"
    _note(inbox / "2026-08-16-a.md", "20260816090000", "learning", OLD_CREATED, "the body")
    events.log("/in/a.m4a", "classify", "needs_review", message="type=learning confidence=0.8 by=llm")
    events.log("/in/a.m4a", "route", "ok", message="wrote 2026-08-16-a.md")
    _init_git_vault(vault)

    result = notes.drain_review(vault, events.db_path, now=NOW)
    assert result == {"filed": 1, "parked": 0}

    dest = vault / "03-Learnings" / "2026-08-16-a.md"
    assert dest.exists() and not (inbox / "2026-08-16-a.md").exists()
    text = dest.read_text(encoding="utf-8")
    assert "type: learning" in text
    assert "status: active" in text          # route.STATUS_INITIAL["learning"]
    # origin is NEVER overwritten by drain — the note's content is still
    # 100% human speech; only the TYPE was accepted without human review,
    # which meta_origin (set at creation) already records. See CLAUDE.md §2:
    # origin can never be reconstructed later, so it must survive untouched.
    assert "origin: human" in text
    assert "meta_origin: ai" in text         # untouched — set at creation, not by drain
    assert "drained: true" in text
    assert "id: 20260816090000" in text      # untouched
    assert "the body" in text                # body preserved


# ---- 2. no classify event at all → parked, untouched ------------------------

def test_stale_item_with_no_classify_event_is_parked_untouched(vault, events):
    inbox = vault / "00-Inbox"
    _note(inbox / "2026-08-16-b.md", "20260816090001", "musing", OLD_CREATED, "no events for this one")
    before = (inbox / "2026-08-16-b.md").read_text(encoding="utf-8")
    _init_git_vault(vault)

    result = notes.drain_review(vault, events.db_path, now=NOW)
    assert result == {"filed": 0, "parked": 1}

    parked = vault / "00-Inbox" / "parked" / "2026-08-16-b.md"
    assert parked.exists() and not (inbox / "2026-08-16-b.md").exists()
    assert parked.read_text(encoding="utf-8") == before   # byte-identical, nothing stamped


# ---- 3. below-floor confidence → parked, untouched ---------------------------

def test_stale_below_floor_item_is_parked_untouched(vault, events):
    inbox = vault / "00-Inbox"
    _note(inbox / "2026-08-16-c.md", "20260816090002", "learning", OLD_CREATED, "shaky guess")
    events.log("/in/c.m4a", "classify", "needs_review", message="type=learning confidence=0.3 by=llm")
    events.log("/in/c.m4a", "route", "ok", message="wrote 2026-08-16-c.md")
    before = (inbox / "2026-08-16-c.md").read_text(encoding="utf-8")
    _init_git_vault(vault)

    result = notes.drain_review(vault, events.db_path, now=NOW)
    assert result == {"filed": 0, "parked": 1}
    parked = vault / "00-Inbox" / "parked" / "2026-08-16-c.md"
    assert parked.exists()
    assert parked.read_text(encoding="utf-8") == before


# ---- 4. fresh item, high confidence → untouched ------------------------------

def test_fresh_item_is_untouched_even_at_high_confidence(vault, events):
    inbox = vault / "00-Inbox"
    _note(inbox / "2026-08-28-d.md", "20260828090000", "learning", FRESH_CREATED, "brand new")
    events.log("/in/d.m4a", "classify", "needs_review", message="type=learning confidence=0.9 by=llm")
    events.log("/in/d.m4a", "route", "ok", message="wrote 2026-08-28-d.md")
    _init_git_vault(vault)

    result = notes.drain_review(vault, events.db_path, now=NOW)
    assert result == {"filed": 0, "parked": 0}
    text = (inbox / "2026-08-28-d.md").read_text(encoding="utf-8")
    assert "status: needs-review" in text
    assert not (inbox / "parked").exists() or not any((inbox / "parked").iterdir())


# ---- 5. conversation, stale → always parked ----------------------------------

def test_stale_conversation_is_always_parked_never_filed(vault, events):
    """A conversation's TYPE is never in doubt — only its attendees are — so
    it must always park here, never auto-file, whatever confidence it
    carries (CLAUDE.md §3: no AI bulk-write reaches a person note
    unreviewed).

    IMPORTANT — this is NOT simply the "missing classify event" case: reading
    the real `is_conversation` branch in pipeline/watcher.py (not assuming
    it) shows it logs `stage='attendees'` for the suggestion AND STILL logs
    an unconditional `stage='classify'` event afterwards, with confidence
    1.0 and `by=plaud`, purely so GET /api/review has a number to show. So a
    conversation DOES have a `_classify_map` entry, at the highest possible
    confidence — the floor check alone would wrongly call it "confident
    enough" and file it. This test seeds BOTH events, exactly as the real
    pipeline would, to prove drain_review's explicit
    `note_type != "conversation"` guard is what actually parks it, not an
    absent classify event."""
    inbox = vault / "00-Inbox"
    _note(inbox / "2026-08-16-e.md", "20260816090003", "conversation", OLD_CREATED, "[00:01] A: hi")
    events.log("/in/e.ogg", "attendees", "ok", message='{"suggested": {}}')
    events.log("/in/e.ogg", "classify", "needs_review",
              message="type=conversation confidence=1.00 by=plaud")
    events.log("/in/e.ogg", "route", "ok", message="wrote 2026-08-16-e.md")
    _init_git_vault(vault)

    result = notes.drain_review(vault, events.db_path, now=NOW)
    assert result == {"filed": 0, "parked": 1}
    assert (vault / "00-Inbox" / "parked" / "2026-08-16-e.md").exists()


# ---- 6. one commit per run; git revert restores everything -------------------

def test_one_commit_per_run_and_revert_restores_everything(vault, events):
    inbox = vault / "00-Inbox"
    _note(inbox / "2026-08-16-a.md", "20260816090000", "learning", OLD_CREATED, "filed body")
    events.log("/in/a.m4a", "classify", "needs_review", message="type=learning confidence=0.8 by=llm")
    events.log("/in/a.m4a", "route", "ok", message="wrote 2026-08-16-a.md")
    _note(inbox / "2026-08-16-b.md", "20260816090001", "musing", OLD_CREATED, "parked body")
    _init_git_vault(vault)

    before = _commit_count(vault)
    result = notes.drain_review(vault, events.db_path, now=NOW)
    assert result == {"filed": 1, "parked": 1}
    after = _commit_count(vault)
    assert after - before == 1                                    # exactly one commit

    log_subject = _git(vault, "log", "-1", "--format=%s").strip()
    assert log_subject == "triage drain: 1 filed at best guess, 1 parked — revert with: git revert HEAD"

    _git(vault, "revert", "--no-edit", "HEAD")
    assert (inbox / "2026-08-16-a.md").read_text(encoding="utf-8") == (
        "---\nid: 20260816090000\ntype: learning\ncreated: 2026-08-16\nsource: voice\n"
        "origin: human\nmeta_origin: ai\nstatus: needs-review\ncategories: []\n"
        "subjects: []\ntags: []\n---\n\nfiled body\n")
    assert not (vault / "03-Learnings" / "2026-08-16-a.md").exists()
    assert (inbox / "2026-08-16-b.md").exists()
    assert not (vault / "00-Inbox" / "parked" / "2026-08-16-b.md").exists()


# ---- 7. empty run → no commit -------------------------------------------------

def test_empty_run_makes_no_commit(vault, events):
    _init_git_vault(vault)   # empty inbox
    before = _commit_count(vault)
    result = notes.drain_review(vault, events.db_path, now=NOW)
    assert result == {"filed": 0, "parked": 0}
    assert _commit_count(vault) == before


def test_second_run_same_day_with_nothing_newly_stale_is_a_noop(vault, events):
    inbox = vault / "00-Inbox"
    _note(inbox / "2026-08-16-a.md", "20260816090000", "learning", OLD_CREATED, "body")
    events.log("/in/a.m4a", "classify", "needs_review", message="type=learning confidence=0.8 by=llm")
    events.log("/in/a.m4a", "route", "ok", message="wrote 2026-08-16-a.md")
    _init_git_vault(vault)

    notes.drain_review(vault, events.db_path, now=NOW)
    before = _commit_count(vault)
    result = notes.drain_review(vault, events.db_path, now=NOW)
    assert result == {"filed": 0, "parked": 0}
    assert _commit_count(vault) == before


# ---- 8. drain_tick: once-a-day gate, no reminder mark on failure ------------

def test_drain_tick_runs_once_per_day(vault, events, monkeypatch):
    config = make_config(vault, triage={"drain": True})
    calls = []

    def fake_drain_review(vault_arg, db_path, **kw):
        calls.append((vault_arg, db_path))
        return {"filed": 0, "parked": 0}
    monkeypatch.setattr(notes, "drain_review", fake_drain_review)

    fixed_today = date(2026, 8, 31)
    monkeypatch.setattr(watcher, "date", SimpleNamespace(today=lambda: fixed_today))

    watcher.drain_tick(config, events)
    watcher.drain_tick(config, events)
    assert len(calls) == 1                                    # second call is a no-op
    assert events.reminder_fired(f"drain-{fixed_today.isoformat()}")


def test_drain_tick_does_not_mark_reminder_on_failure(vault, events, monkeypatch):
    config = make_config(vault, triage={"drain": True})

    def boom(vault_arg, db_path, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(notes, "drain_review", boom)

    fixed_today = date(2026, 8, 31)
    monkeypatch.setattr(watcher, "date", SimpleNamespace(today=lambda: fixed_today))

    watcher.drain_tick(config, events)   # raises internally, caught, never propagates
    assert not events.reminder_fired(f"drain-{fixed_today.isoformat()}")

    calls = []

    def fake_ok(vault_arg, db_path, **kw):
        calls.append(1)
        return {"filed": 0, "parked": 0}
    monkeypatch.setattr(notes, "drain_review", fake_ok)
    watcher.drain_tick(config, events)   # same "day" — retries since reminder wasn't marked
    assert len(calls) == 1
    assert events.reminder_fired(f"drain-{fixed_today.isoformat()}")


def test_drain_tick_disabled_via_config_is_a_quiet_noop(vault, events, monkeypatch):
    config = make_config(vault, triage={"drain": False})
    calls = []
    monkeypatch.setattr(notes, "drain_review", lambda *a, **kw: calls.append(1))
    watcher.drain_tick(config, events)
    assert calls == []


# ---- 9. digest line ----------------------------------------------------------

def test_digest_includes_drain_filed_line_when_seeded(vault, events, monkeypatch):
    events.conn.execute(
        "INSERT INTO events (timestamp, file, stage, status, message) VALUES (?,?,?,?,?)",
        ("2026-08-30T03:00:00", str(vault), "drain", "ok", "filed=7 parked=2"))
    events.conn.commit()
    config = SimpleNamespace(vault_path=vault, ntfy_url="https://ntfy.example", ntfy_topic="t",
                             raw={"todos": {"digest": True}})
    pushes = []
    monkeypatch.setattr(todos.errors, "ntfy",
                        lambda url, topic, msg, title="", click="": pushes.append(msg))
    todos.tick(config, events, now=datetime(2026, 8, 31, 8, 5, tzinfo=todos.TZ))
    assert len(pushes) == 1
    assert "7 old items filed at best guess — one command undoes it." in pushes[0]


def test_digest_omits_drain_line_when_no_drain_event_yesterday(vault, events, monkeypatch):
    events.conn.execute(
        "INSERT INTO events (timestamp, file, stage, status) VALUES (?,?,?,?)",
        ("2026-08-30T09:00:00", "/in/a.m4a", "archive", "ok"))
    events.conn.commit()
    config = SimpleNamespace(vault_path=vault, ntfy_url="https://ntfy.example", ntfy_topic="t",
                             raw={"todos": {"digest": True}})
    pushes = []
    monkeypatch.setattr(todos.errors, "ntfy",
                        lambda url, topic, msg, title="", click="": pushes.append(msg))
    todos.tick(config, events, now=datetime(2026, 8, 31, 8, 5, tzinfo=todos.TZ))
    assert len(pushes) == 1
    assert "filed at best guess" not in pushes[0]
