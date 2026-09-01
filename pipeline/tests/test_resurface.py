"""Pass R (B6): the hybrid resurfacing picker. Real tmp vault + real sqlite,
fake `now=` and seeded `rng=random.Random(N)` for determinism — no conftest.py,
matching this repo's per-file fixture style."""
from __future__ import annotations

import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from pipeline import resurface, todos
from pipeline.events import EventLog

TODAY = date(2026, 8, 31)


def _candidate(vault: Path, folder: str, note_id: str, slug: str, created: str,
              body: str = "Some content.\n\nMore.", related_id: str | None = None) -> Path:
    d = vault / folder
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{created}-{slug}.md"
    related_line = f'related: "[[{related_id}]]"\n' if related_id else ""
    path.write_text(
        f"---\nid: {note_id}\ntype: musing\ncreated: {created}\nstatus: active\n"
        f"{related_line}---\n\n{body}\n",
        encoding="utf-8")
    return path


def _seed_row(db_path: Path, note_id: str, last_shown: str | None, shows: int,
             response: str | None) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS resurface (note_id TEXT PRIMARY KEY, last_shown TEXT, "
        "shows INTEGER NOT NULL DEFAULT 0, response TEXT)")
    conn.execute(
        "INSERT INTO resurface (note_id, last_shown, shows, response) VALUES (?, ?, ?, ?)",
        (note_id, last_shown, shows, response))
    conn.commit()
    conn.close()


def _row(db_path: Path, note_id: str):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS resurface (note_id TEXT PRIMARY KEY, last_shown TEXT, "
        "shows INTEGER NOT NULL DEFAULT 0, response TEXT)")
    row = conn.execute(
        "SELECT note_id, last_shown, shows, response FROM resurface WHERE note_id = ?",
        (note_id,)).fetchone()
    conn.close()
    return row


# ---- cooldown -----------------------------------------------------------------

def test_cooldown_blocks_then_releases(tmp_path):
    vault = tmp_path / "vault"
    db = tmp_path / "events.db"
    _candidate(vault, "02-Musings", "n1", "hunch", "2026-01-01")
    # shown yesterday, shows=1 -> next cooldown is 7*(1+1)=14 days; 1 day
    # elapsed is nowhere near enough
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    _seed_row(db, "n1", yesterday, 1, None)
    assert resurface.pick(vault, db, k=1, now=TODAY) == []

    # same note, but its cooldown fully elapsed (14 days since last_shown)
    db2 = tmp_path / "events2.db"
    elapsed = (TODAY - timedelta(days=14)).isoformat()
    _seed_row(db2, "n1", elapsed, 1, None)
    picked = resurface.pick(vault, db2, k=1, now=TODAY)
    assert len(picked) == 1 and picked[0]["id"] == "n1"


def test_gap_widens_with_shows_7_then_14(tmp_path):
    vault = tmp_path / "vault"
    _candidate(vault, "02-Musings", "n1", "hunch", "2026-01-01")

    # shows=0 -> first cooldown is 7*(0+1)=7 days. 6 days elapsed: still blocked.
    db_a = tmp_path / "a.db"
    _seed_row(db_a, "n1", (TODAY - timedelta(days=6)).isoformat(), 0, None)
    assert resurface.pick(vault, db_a, k=1, now=TODAY) == []
    db_b = tmp_path / "b.db"
    _seed_row(db_b, "n1", (TODAY - timedelta(days=7)).isoformat(), 0, None)
    assert len(resurface.pick(vault, db_b, k=1, now=TODAY)) == 1

    # shows=1 -> next cooldown is 7*(1+1)=14 days. 13 days: blocked, 14: eligible.
    db_c = tmp_path / "c.db"
    _seed_row(db_c, "n1", (TODAY - timedelta(days=13)).isoformat(), 1, None)
    assert resurface.pick(vault, db_c, k=1, now=TODAY) == []
    db_d = tmp_path / "d.db"
    _seed_row(db_d, "n1", (TODAY - timedelta(days=14)).isoformat(), 1, None)
    assert len(resurface.pick(vault, db_d, k=1, now=TODAY)) == 1


def test_archived_never_picked_regardless_of_age_or_cooldown(tmp_path):
    vault = tmp_path / "vault"
    db = tmp_path / "events.db"
    # old creation date (huge weight), never shown (no cooldown to wait out) —
    # would dominate any weighted draw if the archived check didn't come first
    _candidate(vault, "wiki", "n1", "old-idea", "2000-01-01")
    _seed_row(db, "n1", None, 0, "archived")
    assert resurface.pick(vault, db, k=1, now=TODAY) == []


# ---- weighting ------------------------------------------------------------------

def test_age_weighting_favors_older_notes(tmp_path):
    vault = tmp_path / "vault"
    _candidate(vault, "02-Musings", "old", "old-note", "2020-01-01")
    _candidate(vault, "02-Musings", "new", "new-note", "2026-08-01")
    rng = random.Random(7)
    older_picks = newer_picks = 0
    db = tmp_path / "events.db"
    for _ in range(200):
        db.unlink(missing_ok=True)  # fresh state each draw — no cross-draw cooldown
        picked = resurface.pick(vault, db, k=1, now=TODAY, rng=rng)
        assert len(picked) == 1
        if picked[0]["id"] == "old":
            older_picks += 1
        else:
            newer_picks += 1
    assert older_picks > newer_picks


# ---- k>1 --------------------------------------------------------------------

def test_k2_picks_two_different_notes(tmp_path):
    vault = tmp_path / "vault"
    _candidate(vault, "02-Musings", "n1", "one", "2026-01-01")
    _candidate(vault, "03-Learnings", "n2", "two", "2026-01-05")
    _candidate(vault, "wiki", "n3", "three", "2026-01-10")
    db = tmp_path / "events.db"
    picked = resurface.pick(vault, db, k=2, now=TODAY, rng=random.Random(3))
    assert len(picked) == 2
    assert picked[0]["id"] != picked[1]["id"]


# ---- same-day idempotency (review fix) -----------------------------------------
# GET /api/resurfaced is polled on window focus by usePolling — including the
# focus round-trip the "Open in Obsidian" button itself triggers — so pick()
# must not re-roll new notes on every same-day call: the SET a repeated call
# returns must stay stable, or the primary CTA destroys the very card it's on.

def test_repeated_same_day_pick_returns_a_stable_set(tmp_path):
    vault = tmp_path / "vault"
    _candidate(vault, "02-Musings", "n1", "one", "2026-01-01")
    _candidate(vault, "03-Learnings", "n2", "two", "2026-01-05")
    _candidate(vault, "wiki", "n3", "three", "2026-01-10")
    db = tmp_path / "events.db"
    rng = random.Random(3)
    first = resurface.pick(vault, db, k=2, now=TODAY, rng=rng)
    second = resurface.pick(vault, db, k=2, now=TODAY, rng=rng)
    assert {n["id"] for n in first} == {n["id"] for n in second}


def test_archive_breaks_same_day_stability_immediately(tmp_path):
    """The one case where a same-day repeat call must NOT stay stable: an
    archive response on a just-shown card has to stick right away, not wait
    for tomorrow's cooldown reset."""
    vault = tmp_path / "vault"
    _candidate(vault, "02-Musings", "n1", "only", "2026-01-01")
    db = tmp_path / "events.db"
    first = resurface.pick(vault, db, k=1, now=TODAY, rng=random.Random(1))
    assert [n["id"] for n in first] == ["n1"]
    resurface.record_response(db, "n1", "archive")
    second = resurface.pick(vault, db, k=1, now=TODAY, rng=random.Random(1))
    assert second == []


# ---- record_response ---------------------------------------------------------

def test_record_response_archive_inserts_when_no_prior_row(tmp_path):
    db = tmp_path / "events.db"
    assert _row(db, "n1") is None
    resurface.record_response(db, "n1", "archive")
    row = _row(db, "n1")
    assert row == ("n1", None, 0, "archived")


def test_record_response_updates_existing_row(tmp_path):
    db = tmp_path / "events.db"
    _seed_row(db, "n1", "2026-01-01", 2, None)
    resurface.record_response(db, "n1", "connect")
    row = _row(db, "n1")
    assert row == ("n1", "2026-01-01", 2, "connected")  # last_shown/shows untouched


# ---- digest integration --------------------------------------------------------

def _todos_config(tmp_path, vault):
    return SimpleNamespace(
        vault_path=vault,
        ntfy_url="https://ntfy.example", ntfy_topic="t",
        anthropic_key=None,
        raw={"todos": {"digest": True}},
    )


def test_digest_includes_resurfaced_line(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    _candidate(vault, "02-Musings", "n1", "an-old-hunch", "2020-01-01",
              body="A hunch worth revisiting.")
    # a due-today todo so the digest isn't skipped as a quiet day (the
    # quiet-day check runs before the resurfaced pick — same digest ordering
    # rule the brief specifies)
    todos_dir = vault / "06-Todos"
    todos_dir.mkdir(parents=True)
    (todos_dir / "2026-08-31.md").write_text(
        "# Todos — 2026-08-31\n\n- [ ] water the plants 📅 2026-08-31 ^t-1\n", encoding="utf-8")

    config = _todos_config(tmp_path, vault)
    events = EventLog(tmp_path / "events.db", vault)
    pushes = []
    now = datetime(2026, 8, 31, 8, 5, tzinfo=todos.TZ)

    monkeypatch.setattr(todos.errors, "ntfy",
                        lambda url, topic, msg, title="", click="": pushes.append(msg))
    todos.tick(config, events, now=now)
    events.close()

    assert len(pushes) == 1
    assert "Resurfaced: an-old-hunch" in pushes[0]


# ---- related_title resolution (Pass R, B7) -----------------------------------
# A resurface candidate resolves its OWN `related: [[id]]` frontmatter (stamped
# by watcher.py at classification time) to the linked note's title via a
# bounded live vault lookup — a different mechanism from api/notes.py's
# events.db join, since a resurface candidate has no events.db trail from
# THIS run (see pipeline/resurface.py::_resolve_title).

def test_candidate_resolves_related_title_from_frontmatter(tmp_path):
    vault = tmp_path / "vault"
    _candidate(vault, "03-Learnings", "other-id", "an-earlier-note", "2026-01-01",
              body="The earlier thought.")
    _candidate(vault, "02-Musings", "n1", "a-later-echo", "2026-02-01",
              body="Related to that earlier thought.", related_id="other-id")
    candidates = {c["id"]: c for c in resurface._candidates(vault)}
    assert candidates["n1"]["related_title"] == "an-earlier-note"


def test_candidate_related_link_to_missing_note_is_none(tmp_path):
    vault = tmp_path / "vault"
    _candidate(vault, "02-Musings", "n1", "a-dangling-link", "2026-02-01",
              related_id="deleted-or-moved-id")
    candidates = {c["id"]: c for c in resurface._candidates(vault)}
    assert candidates["n1"]["related_title"] is None


def test_candidate_with_no_related_field_is_none(tmp_path):
    vault = tmp_path / "vault"
    _candidate(vault, "02-Musings", "n1", "just-a-note", "2026-02-01")
    candidates = {c["id"]: c for c in resurface._candidates(vault)}
    assert candidates["n1"]["related_title"] is None
