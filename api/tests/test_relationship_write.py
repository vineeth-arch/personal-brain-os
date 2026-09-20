"""Task 9 — the Relationship OS write endpoints: POST /contact, POST
/promise, POST /owner, POST /hold, DELETE /hold, GET /held, and the Today
strip threading held drafts through. These are the mutating half of the
Task 8 read surfaces — every write here goes through touchlog.record_touch
/ touchlog.close_promise / merge.owner_change, the same pure-logic modules
the read side already trusts."""
from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from api import held as held_mod
from api import main as api_main
from api import notes
from api import people as people_mod
from pipeline import relationships

# the tmp vault/config/server harness is shared with the main API suite
from api.tests.test_api import TOKEN, Server, env  # noqa: F401
from api.tests.test_relationship_os import _person_v2  # noqa: F401


# ---- timezone-threading helpers (final-review fix wave) --------------------
#
# `api/main.py` computes `today`/`now` from `datetime.now(config.tzinfo)` at
# the HTTP boundary and threads it into api/people.py and api/notes.py. To
# prove that threading actually happens (not just that the functions accept
# a `today` argument), each test below:
#   1. fixes "now" to a single UTC instant whose LOCAL date, in a chosen
#      config timezone, is a day ahead of its UTC date;
#   2. sabotages the naive `date.today()` fallback inside the target module
#      so it returns an obviously-wrong sentinel date;
# and then asserts the vault write carries the LOCAL date — proving the
# value came from config.tzinfo via main.py, not from the sabotaged fallback
# (which is what would land if main.py silently left the argument unset).

class _FixedInstant(datetime):
    """`datetime.now()` that always answers one fixed UTC instant."""
    _instant = datetime(2026, 1, 1, 23, 30, tzinfo=ZoneInfo("UTC"))

    @classmethod
    def now(cls, tz=None):
        return cls._instant.astimezone(tz) if tz is not None else cls._instant


class _StaleDate(date):
    """`date.today()` that always answers an obviously-wrong sentinel."""
    @classmethod
    def today(cls):
        return cls(2000, 1, 1)


def _set_timezone(root: Path, tz: str) -> None:
    cfg_path = root / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["timezone"] = tz
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")


@pytest.fixture
def vault_env(env):
    root, vault, inbox, failed = env
    folder = vault / "07-People"
    folder.mkdir(exist_ok=True)
    return root, vault, folder


# ---- POST /contact -----------------------------------------------------------

def test_contact_blank_touch_type_three_part_422(vault_env):
    root, _, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000")
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/contact",
                           {"note": "hi", "channel": "whatsapp"})
        assert code == 422
        err = body["error"]
        assert set(err) == {"what", "cause", "todo"}
        assert err["what"] == "That touch wasn't logged."
        assert err["cause"] == "The touch type is missing or isn't one the log knows."
        assert err["todo"] == "Pick a touch type above the draft, then log it again."


def test_contact_writes_v2_line_and_last_give(vault_env):
    root, vault, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000")
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/contact",
                           {"note": "shared an article", "channel": "whatsapp",
                            "touch_type": "give_know"})
        assert code == 200
    text = next((vault / "07-People").glob("*.md")).read_text(encoding="utf-8")
    today = datetime.now(ZoneInfo("UTC")).date().isoformat()  # test config tz is UTC
    assert f"- {today} · out · whatsapp · give_know · shared an article" in text
    assert f"last_give: {today}" in text
    assert f"last_contact: {today}" in text


def test_contact_keeps_suggest_stage_and_commit_message(vault_env):
    root, vault, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000", tier="")
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/contact",
                           {"note": "caught up", "channel": "whatsapp",
                            "touch_type": "remember"})
        assert code == 200
        assert body["suggest_stage"] is None  # blank warmth_stage has no ladder
    log = subprocess.run(["git", "-C", str(vault), "log", "-1", "--format=%s"],
                         capture_output=True, text=True).stdout
    assert "logged contact" in log


def test_contact_in_reply_lifts_quiet(vault_env):
    root, vault, folder = vault_env
    future = (date.today() + timedelta(days=60)).isoformat()
    _person_v2(folder, "Priya Raman", "20260701090000", quiet_until=future,
              interaction_log="- 2020-01-01 · out · whatsapp · remember · following up")
    with Server(root) as s:
        code, body = s.req("GET", "/api/people/20260701090000")
        assert body["quiet"] is not None

        code, body = s.req("POST", "/api/people/20260701090000/contact",
                           {"note": "thanks!", "channel": "whatsapp",
                            "direction": "in", "touch_type": "reply"})
        assert code == 200

        code, body = s.req("GET", "/api/people/20260701090000")
        assert body["quiet"] is None


def test_contact_uses_config_tzinfo_local_date_not_naive_fallback(vault_env, monkeypatch):
    root, vault, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000")
    _set_timezone(root, "Pacific/Apia")  # UTC+13 — local date runs a day ahead of UTC

    monkeypatch.setattr(api_main, "datetime", _FixedInstant)
    monkeypatch.setattr(people_mod, "date", _StaleDate)  # sabotage the naive fallback

    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/contact",
                           {"note": "hi", "channel": "whatsapp", "touch_type": "give_know"})
        assert code == 200
    text = next((vault / "07-People").glob("*.md")).read_text(encoding="utf-8")
    # fixed instant is 2026-01-01 23:30 UTC == 2026-01-02 12:30 in Pacific/Apia
    assert "- 2026-01-02 · out · whatsapp · give_know · hi" in text
    assert "2000-01-01" not in text
    assert "2026-01-01 ·" not in text


# ---- POST /promise -------------------------------------------------------------

def test_promise_close_theirs_updates_reliability_line(vault_env):
    root, vault, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000",
              next_action="- open · Check in — they promised: send the intro "
                          "<!-- bc:promiseA -->")
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/promise",
                           {"key": "promiseA", "result": "kept", "side": "theirs"})
        assert code == 200
        assert body["reliability"] == {"kept": 1, "late": 0, "dropped": 0}
        assert "1 kept" in body["reliability_line"]
        row = next(r for r in body["next_actions"] if r["key"] == "promiseA")
        assert row["closed"] is True


def test_promise_close_mine_logs_keep_promise(vault_env):
    root, vault, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000",
              next_action="- open · I promised: send the deck <!-- bc:promiseB -->")
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/promise",
                           {"key": "promiseB", "result": "kept", "side": "mine"})
        assert code == 200
        row = next(r for r in body["next_actions"] if r["key"] == "promiseB")
        assert row["closed"] is True
    text = next((vault / "07-People").glob("*.md")).read_text(encoding="utf-8")
    assert "keep_promise" in text and "I promised: send the deck" in text


def test_promise_close_unknown_key_is_404(vault_env):
    root, _, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000")
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/promise",
                           {"key": "nope", "result": "kept", "side": "mine"})
        assert code == 404
        assert body["error"]["what"] == "That promise isn't open anymore."


def test_promise_close_uses_config_tzinfo_local_date_not_naive_fallback(vault_env, monkeypatch):
    root, vault, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000",
              next_action="- open · Check in — they promised: send the intro "
                          "<!-- bc:promiseA -->")
    _set_timezone(root, "Pacific/Apia")

    monkeypatch.setattr(api_main, "datetime", _FixedInstant)
    monkeypatch.setattr(people_mod, "date", _StaleDate)

    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/promise",
                           {"key": "promiseA", "result": "dropped", "side": "theirs"})
        assert code == 200
    text = next((vault / "07-People").glob("*.md")).read_text(encoding="utf-8")
    assert "- 2026-01-02 · in ·  · promise_dropped ·" in text
    assert "2000-01-01" not in text


# ---- POST /owner ---------------------------------------------------------------

def test_owner_tier_edit_records_history(vault_env):
    root, vault, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000", tier="")
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/owner",
                           {"field": "tier", "value": "core"})
        assert code == 200
        assert body["tier"] == "core"
        assert body["warning"] is None
    text = next((vault / "07-People").glob("*.md")).read_text(encoding="utf-8")
    today = datetime.now(ZoneInfo("UTC")).date().isoformat()  # test config tz is UTC
    assert f"- {today} · tier:  → core (owner)" in text


def test_owner_edit_uses_config_tzinfo_local_date_not_naive_fallback(vault_env, monkeypatch):
    root, vault, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000", tier="")
    _set_timezone(root, "Pacific/Apia")

    monkeypatch.setattr(api_main, "datetime", _FixedInstant)
    monkeypatch.setattr(people_mod, "date", _StaleDate)

    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/owner",
                           {"field": "tier", "value": "core"})
        assert code == 200
    text = next((vault / "07-People").glob("*.md")).read_text(encoding="utf-8")
    assert "- 2026-01-02 · tier:  → core (owner)" in text
    assert "2000-01-01" not in text


def test_owner_rejects_non_owner_field(vault_env):
    root, _, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000")
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/owner",
                           {"field": "company", "value": "Acme"})
        assert code == 422
        assert set(body["error"]) == {"what", "cause", "todo"}
        assert body["error"]["cause"] == '"company" is not an owner-editable field.'


def test_owner_rejects_invalid_value_for_known_field(vault_env):
    root, _, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000")
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/owner",
                           {"field": "tier", "value": "not-a-real-tier"})
        assert code == 422
        assert set(body["error"]) == {"what", "cause", "todo"}
        assert body["error"]["cause"] == (
            '"not-a-real-tier" isn\'t one of the allowed values for tier.')


def test_owner_dates_written_as_inline_map(vault_env):
    root, vault, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000")
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/owner",
                           {"field": "dates", "value": json.dumps({"birthday": "03-14",
                                                                   "anniversary": ""})})
        assert code == 200
    text = next((vault / "07-People").glob("*.md")).read_text(encoding="utf-8")
    assert "dates: {birthday: 03-14, anniversary: }" in text


def test_tier_cap_warning(vault_env):
    root, vault, folder = vault_env
    for i in range(15):
        _person_v2(folder, f"Inner {i}", f"2026070108{i:04d}", tier="inner")
    _person_v2(folder, "Priya Raman", "20260701090000", tier="")
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/owner",
                           {"field": "tier", "value": "inner"})
        assert code == 200
        assert body["warning"] == "inner is 16 of 15. Who moves down a tier?"


# ---- POST /draft, POST /reply (quiet-window timezone threading) ----------------

def test_draft_quiet_uses_config_tzinfo_local_date_not_naive_fallback(vault_env, monkeypatch):
    """draft() only consumes `today` to compute `ledger.is_quiet` — threading
    the wrong date flips the `pushy` seducer chip, so a `quiet_until` set to
    exactly the correct LOCAL date (the window closing today, not later)
    proves which `today` the server actually used."""
    root, vault, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000",
              quiet_until="2026-01-02",
              interaction_log="- 2026-01-01 · out · whatsapp · give_know · shared an article")
    _set_timezone(root, "Pacific/Apia")

    monkeypatch.setattr(api_main, "datetime", _FixedInstant)
    monkeypatch.setattr(people_mod, "date", _StaleDate)  # sabotage the naive fallback

    from pipeline import llm
    monkeypatch.setattr(llm, "complete_text",
                        lambda prompt, config: ("just checking in, no rush", "claude-haiku", []))

    with Server(root) as s:
        s.req("POST", "/api/people/voice", {"samples": ["hey! long time"]})
        code, body = s.req("POST", "/api/people/20260701090000/draft",
                           {"touch_type": "presence"})
        assert code == 200
        # local date (2026-01-02) == quiet_until -> the window has just
        # closed, so "pushy" must NOT appear. If the server had fallen back
        # to the sabotaged date (2000-01-01 < quiet_until), quiet would
        # still read True and "pushy" would appear.
        assert "pushy" not in body["lints"]["seducer"]


def test_reply_quiet_uses_config_tzinfo_local_date_not_naive_fallback(vault_env, monkeypatch):
    """Same proof as above, through POST /reply — which threads `today`
    into the same draft() call."""
    root, vault, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000",
              quiet_until="2026-01-02",
              interaction_log="- 2026-01-01 · out · whatsapp · give_know · shared an article")
    _set_timezone(root, "Pacific/Apia")

    monkeypatch.setattr(api_main, "datetime", _FixedInstant)
    monkeypatch.setattr(people_mod, "date", _StaleDate)

    from pipeline import llm
    monkeypatch.setattr(llm, "complete_json",
                        lambda prompt, config, validate: ({"code": None}, "claude-haiku", []))
    monkeypatch.setattr(llm, "complete_text",
                        lambda prompt, config: ("just checking in, no rush", "claude-haiku", []))

    with Server(root) as s:
        s.req("POST", "/api/people/voice", {"samples": ["hey! long time"]})
        code, body = s.req("POST", "/api/people/20260701090000/reply",
                           {"message": "hey, checking in"})
        assert code == 200
        assert "pushy" not in body["seducer"]


# ---- POST /hold, DELETE /hold, GET /held ---------------------------------------

def test_hold_writes_vault_file_and_commits(vault_env):
    root, vault, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000")
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/hold",
                           {"text": "still thinking about this", "channel": "whatsapp",
                            "touch_type": "remember"})
        assert code == 200
        assert "until" in body

    held_path = vault / "_System" / "held" / "20260701090000.md"
    assert held_path.is_file()
    text = held_path.read_text(encoding="utf-8")
    assert "person_id: 20260701090000" in text
    assert "channel: whatsapp" in text
    assert "touch_type: remember" in text
    assert "still thinking about this" in text

    log = subprocess.run(["git", "-C", str(vault), "log", "-1", "--format=%s"],
                         capture_output=True, text=True).stdout
    assert "held" in log


def test_hold_rejects_bad_id(vault_env):
    root, vault, folder = vault_env
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/not-an-id/hold",
                           {"text": "x", "channel": "whatsapp", "touch_type": "remember"})
        assert code == 404
        assert set(body["error"]) == {"what", "cause", "todo"}
    assert not (vault / "_System" / "held").exists()


def test_hold_delete_clears_the_file(vault_env):
    root, vault, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000")
    with Server(root) as s:
        s.req("POST", "/api/people/20260701090000/hold",
              {"text": "x", "channel": "whatsapp", "touch_type": "remember"})
        assert (vault / "_System" / "held" / "20260701090000.md").is_file()
        code, body = s.req("DELETE", "/api/people/20260701090000/hold")
        assert code == 200 and body == {"ok": True}
    assert not (vault / "_System" / "held" / "20260701090000.md").exists()


def test_held_ready_after_nine_tomorrow_in_config_tz(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    tz = ZoneInfo("Asia/Dubai")
    now = datetime(2026, 8, 20, 10, 0, tzinfo=tz)

    until = held_mod.hold(vault, "20260701090000", "draft text", "whatsapp", "presence", now)
    assert until == datetime(2026, 8, 21, 9, 0, tzinfo=tz)

    before = held_mod.list_items(vault, until - timedelta(minutes=1))
    assert before[0].ready is False

    after = held_mod.list_items(vault, until)
    assert after[0].ready is True


def test_ready_hold_listed_first_in_today(vault_env):
    root, vault, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000")
    held_dir = vault / "_System" / "held"
    held_dir.mkdir(parents=True)
    (held_dir / "20260701090000.md").write_text(
        "---\norigin: human\nperson_id: 20260701090000\n"
        "held_until: 2020-01-01T09:00:00+00:00\nchannel: whatsapp\n"
        "touch_type: remember\n---\n\nstill want to send this?\n", encoding="utf-8")
    with Server(root) as s:
        code, body = s.req("GET", "/api/people/today")
        assert code == 200
        assert len(body["strip"]) >= 1
        top = body["strip"][0]
        assert top["held"] is True
        assert top["person_id"] == "20260701090000"


# ---- attendee approval (R25) ----------------------------------------------------

def test_attendee_approval_writes_in_touch(tmp_path):
    vault = tmp_path / "vault"
    (vault / "00-Inbox").mkdir(parents=True)
    person = relationships.create_person(vault, "Ana Silva", "email", "ana@x.com",
                                         when=datetime(2026, 1, 1))
    path = vault / "00-Inbox" / "2026-08-30-product-sync.md"
    path.write_text(
        "---\nid: 20260830090000\ntype: conversation\ncreated: 2026-08-30\nsource: plaud\n"
        "origin: human\nmeta_origin: human\nstatus: needs-review\ncategories: []\n"
        "subjects: []\ntags: []\nattendees: []\nspeakers:\n  - Ana Silva\n"
        "transcript_source: plaud\n---\n\n[00:01] Ana Silva: let's begin\n", encoding="utf-8")

    dest = notes.approve(vault, "20260830090000", "conversation", [person.id])
    ana_note = relationships.find_person(vault, person.id).path.read_text(encoding="utf-8")
    assert " · in ·  · other · Conversation: product-sync ([[20260830090000]])" in ana_note


def test_attendee_approval_uses_given_today_not_naive_fallback(tmp_path, monkeypatch):
    """`notes.approve`'s own `today` parameter, called directly (mirroring
    how api/main.py's route now computes and passes it) — proves the
    attendee touch is stamped with whatever `today` the caller supplies
    rather than the module's naive `date.today()` fallback."""
    vault = tmp_path / "vault"
    (vault / "00-Inbox").mkdir(parents=True)
    person = relationships.create_person(vault, "Ana Silva", "email", "ana@x.com",
                                         when=datetime(2026, 1, 1))
    path = vault / "00-Inbox" / "2026-08-30-product-sync.md"
    path.write_text(
        "---\nid: 20260830090000\ntype: conversation\ncreated: 2026-08-30\nsource: plaud\n"
        "origin: human\nmeta_origin: human\nstatus: needs-review\ncategories: []\n"
        "subjects: []\ntags: []\nattendees: []\nspeakers:\n  - Ana Silva\n"
        "transcript_source: plaud\n---\n\n[00:01] Ana Silva: let's begin\n", encoding="utf-8")

    monkeypatch.setattr(notes, "date", _StaleDate)  # sabotage the naive fallback

    notes.approve(vault, "20260830090000", "conversation", [person.id],
                  today=date(2026, 1, 2))
    ana_note = relationships.find_person(vault, person.id).path.read_text(encoding="utf-8")
    assert "- 2026-01-02 · in ·  · other · Conversation: product-sync ([[20260830090000]])" \
        in ana_note
    assert "2000-01-01" not in ana_note


def test_attendee_approval_route_threads_config_tzinfo(vault_env, monkeypatch):
    """The route-level half of the above: POST /api/review/{id}/approve must
    itself compute `datetime.now(config.tzinfo).date()` and pass it through,
    the same pattern GET /api/people/today already uses."""
    root, vault, folder = vault_env
    (vault / "00-Inbox").mkdir(exist_ok=True)
    person = relationships.create_person(vault, "Ana Silva", "email", "ana@x.com",
                                         when=datetime(2026, 1, 1))
    path = vault / "00-Inbox" / "2026-08-30-product-sync.md"
    path.write_text(
        "---\nid: 20260830090000\ntype: conversation\ncreated: 2026-08-30\nsource: plaud\n"
        "origin: human\nmeta_origin: human\nstatus: needs-review\ncategories: []\n"
        "subjects: []\ntags: []\nattendees: []\nspeakers:\n  - Ana Silva\n"
        "transcript_source: plaud\n---\n\n[00:01] Ana Silva: let's begin\n", encoding="utf-8")
    _set_timezone(root, "Pacific/Apia")

    monkeypatch.setattr(api_main, "datetime", _FixedInstant)
    monkeypatch.setattr(notes, "date", _StaleDate)

    with Server(root) as s:
        code, body = s.req("POST", "/api/review/20260830090000/approve",
                           {"type": "conversation", "attendees": [person.id]})
        assert code == 200
    ana_note = relationships.find_person(vault, person.id).path.read_text(encoding="utf-8")
    assert "- 2026-01-02 · in ·  · other ·" in ana_note
    assert "2000-01-01" not in ana_note
