"""pipeline/tests/test_queue.py — the seven-view queue engine and the
daily-cap-5 strip (SCHEMA-REFERENCE.md §7 / A4, A11.2). Pure functions over
`Person`, `Touch` and `Held`; a tmp vault only where a real `Person` (tier,
frontmatter, raw sections) is needed."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from pipeline import queue as q
from pipeline import relationships as rel

TODAY = date(2026, 9, 17)


def make_person(vault, name="Priya Raman", *, tier="core", cadence="", status="active",
                created="2026-01-01", list_of_20=False, relationship="client",
                last_contact="2026-08-01", quiet_until="", dates="",
                interaction_log="", next_action=""):
    folder = vault / rel.PEOPLE_FOLDER
    path = folder / f"2026-07-01-{name.lower().replace(' ', '-')}.md"
    path.write_text(
        "---\n"
        "id: 20260701090000\n"
        "type: person\n"
        f"created: {created}\n"
        "source: manual\n"
        "origin: human\n"
        f"relationship: {relationship}\n"
        "company: Alserkal\n"
        "channels: {whatsapp: +971500000000}\n"
        f"tier: {tier}\n"
        f"cadence_days: {cadence}\n"
        f"last_contact: {last_contact}\n"
        f"quiet_until: {quiet_until}\n"
        f"dates: {dates}\n"
        f"list_of_20: {str(list_of_20).lower()}\n"
        f"status: {status}\n"
        "---\n\n"
        f"# {name}\n\n"
        f"## Interaction log\n\n{interaction_log}\n\n"
        f"## Next action\n\n{next_action}\n", encoding="utf-8")
    return path


@pytest.fixture
def vault(tmp_path):
    (tmp_path / rel.PEOPLE_FOLDER).mkdir()
    return tmp_path


def one(vault, name="Priya Raman"):
    people = rel.load_people(vault)
    if name is None:
        return people
    return [p for p in people if p.name == name][0]


def all_people(vault):
    return rel.load_people(vault)


# ---- strip ----------------------------------------------------------------------

def test_strip_caps_at_five_and_counts_overflow():
    items = [
        q.Item(person_id=str(i), name=f"P{i}", tier="core", queue="follow_up",
              touch_type="remember", payload="x", source_key=f"k{i}", due=None, channel="")
        for i in range(7)
    ]
    queues = {v: [] for v in q.VIEWS}
    queues["follow_up"] = items
    capped, overflow = q.strip(queues)
    assert len(capped) == 5
    assert overflow == 2


def test_one_row_per_person_in_strip():
    item1 = q.Item(person_id="1", name="Priya", tier="core", queue="owe_reply",
                   touch_type="reply", payload="a", source_key="a", due=None, channel="")
    item2 = q.Item(person_id="1", name="Priya", tier="core", queue="follow_up",
                   touch_type="remember", payload="b", source_key="b", due=None, channel="")
    queues = {v: [] for v in q.VIEWS}
    queues["owe_reply"] = [item1]
    queues["follow_up"] = [item2]
    capped, overflow = q.strip(queues)
    assert len(capped) == 1
    assert capped[0] is item1
    assert overflow == 0


def test_strip_never_includes_waiting_on_them():
    item = q.Item(person_id="1", name="Priya", tier="core", queue="waiting_on_them",
                  touch_type="remember", payload="a", source_key="a", due=None, channel="")
    queues = {v: [] for v in q.VIEWS}
    queues["waiting_on_them"] = [item]
    capped, overflow = q.strip(queues)
    assert capped == []
    assert overflow == 0


def test_owe_reply_outranks_reconnect():
    owe = q.Item(person_id="1", name="Priya", tier="core", queue="owe_reply",
                touch_type="reply", payload="a", source_key="a", due=None, channel="")
    reconnect = q.Item(person_id="2", name="Amir", tier="core", queue="reconnect",
                       touch_type="presence", payload="", source_key="r", due=None, channel="")
    queues = {v: [] for v in q.VIEWS}
    queues["owe_reply"] = [owe]
    queues["reconnect"] = [reconnect]
    capped, _ = q.strip(queues)
    assert capped[0].queue == "owe_reply"


def test_reconnect_without_payload_listed_not_stripped():
    item = q.Item(person_id="1", name="Priya", tier="core", queue="reconnect",
                  touch_type="", payload="", source_key="reconnect", due=None, channel="")
    queues = {v: [] for v in q.VIEWS}
    queues["reconnect"] = [item]
    capped, overflow = q.strip(queues)
    assert item in queues["reconnect"]     # build()-style output still lists it
    assert capped == []
    assert overflow == 0


def test_inner_without_payload_gets_presence(vault):
    make_person(vault, tier="inner", last_contact=(TODAY - timedelta(days=30)).isoformat())
    queues = q.build(all_people(vault), TODAY)
    reconnect_items = queues["reconnect"]
    assert len(reconnect_items) == 1
    assert reconnect_items[0].touch_type == "presence"
    assert reconnect_items[0].payload == ""
    capped, _ = q.strip(queues)
    assert len(capped) == 1
    assert capped[0].touch_type == "presence"


# ---- owe_reply --------------------------------------------------------------------

def test_reputation_signal_does_not_create_owe_reply(vault):
    log = "- 2026-09-10 · in ·  · other · people say I deliver"
    make_person(vault, interaction_log=log)
    queues = q.build(all_people(vault), TODAY)
    assert queues["owe_reply"] == []


def test_reply_older_than_two_days_flagged(vault):
    log = "- 2026-09-14 · in ·  · reply · thanks!"
    make_person(vault, interaction_log=log)
    queues = q.build(all_people(vault), TODAY)
    assert len(queues["owe_reply"]) == 1
    item = queues["owe_reply"][0]
    assert item.touch_type == "reply"
    assert item.flagged is True


def test_ready_hold_creates_owe_reply_first_for_non_reply_draft(vault):
    make_person(vault)
    held = [q.Held(person_id="20260701090000", touch_type="remember", ready=True)]
    queues = q.build(all_people(vault), TODAY, held=held)
    assert len(queues["owe_reply"]) == 1
    item = queues["owe_reply"][0]
    assert item.held is True
    assert item.touch_type == "remember"
    assert item.payload == "Held draft: still want to send this?"
    assert item.flagged is False


def test_unready_hold_pauses_flag_only(vault):
    log = "- 2026-09-14 · in ·  · reply · thanks!"
    make_person(vault, interaction_log=log)
    held = [q.Held(person_id="20260701090000", touch_type="remember", ready=False)]
    queues = q.build(all_people(vault), TODAY, held=held)
    assert len(queues["owe_reply"]) == 1
    item = queues["owe_reply"][0]
    assert item.held is False
    assert item.touch_type == "reply"
    assert item.flagged is False


# ---- promises / ask_about / waiting_on_them / celebrate / follow_up --------------

def test_promise_stays_after_later_contact_until_closed(vault):
    next_action = "- 2026-09-01 · I promised: send the deck"
    make_person(vault, next_action=next_action, last_contact="2026-09-15")
    queues = q.build(all_people(vault), TODAY)
    assert len(queues["promises"]) == 1
    assert queues["promises"][0].touch_type == "keep_promise"


def test_closed_theirs_promise_leaves_queue(vault):
    key = "abc123"
    next_action = (f"- 2026-09-01 · Check in — they promised: send contract <!-- bc:{key} -->\n"
                  f"<!-- bc:close:{key} -->")
    make_person(vault, next_action=next_action)
    queues = q.build(all_people(vault), TODAY)
    assert queues["ask_about"] == []
    assert queues["waiting_on_them"] == []


def test_future_their_promise_is_waiting(vault):
    due = (TODAY + timedelta(days=5)).isoformat()
    next_action = f"- {due} · Check in — they promised: send contract"
    make_person(vault, next_action=next_action)
    queues = q.build(all_people(vault), TODAY)
    assert queues["ask_about"] == []
    assert len(queues["waiting_on_them"]) == 1
    assert queues["waiting_on_them"][0].touch_type == "remember"


def test_undated_action_follow_up_when_commitment_due(vault):
    next_action = "- Ask how it went with the launch today"
    make_person(vault, next_action=next_action)
    queues = q.build(all_people(vault), TODAY)
    assert len(queues["follow_up"]) == 1
    assert queues["follow_up"][0].touch_type == "remember"


def test_birthday_mm_dd_within_7_days(vault):
    upcoming = TODAY + timedelta(days=3)
    make_person(vault, dates=f"{{birthday: {upcoming.strftime('%m-%d')}}}")
    queues = q.build(all_people(vault), TODAY)
    assert len(queues["celebrate"]) == 1
    item = queues["celebrate"][0]
    assert item.touch_type == "celebrate"
    assert item.payload == f"Birthday on {upcoming.day} {upcoming.strftime('%b')}"


def test_bad_birthday_format_ignored(vault):
    make_person(vault, dates="{birthday: not-a-date}")
    queues = q.build(all_people(vault), TODAY)
    assert queues["celebrate"] == []


# ---- drop rule (quiet / floor) ---------------------------------------------------

def test_quiet_hidden_from_reconnect_but_promise_shows(vault):
    log = "- 2026-09-16 · out · whatsapp · remember · checking in"
    next_action = "- 2026-09-01 · I promised: send the deck"
    make_person(vault, tier="core", last_contact="2026-09-16",
               quiet_until=(TODAY + timedelta(days=10)).isoformat(),
               interaction_log=log, next_action=next_action)
    queues = q.build(all_people(vault), TODAY)
    assert queues["reconnect"] == []
    assert len(queues["promises"]) == 1


def test_floor_hides_core_touched_5_days_ago(vault):
    log = "- " + (TODAY - timedelta(days=5)).isoformat() + " · out · whatsapp · remember · checking in"
    next_action = "- Ask how it went with the launch today"
    make_person(vault, tier="core", created="2020-01-01",
               last_contact=(TODAY - timedelta(days=5)).isoformat(),
               interaction_log=log, next_action=next_action)
    queues = q.build(all_people(vault), TODAY)
    assert queues["follow_up"] == []


def test_wide_never_in_reconnect(vault):
    make_person(vault, tier="wide", cadence="30",
               last_contact=(TODAY - timedelta(days=365)).isoformat(),
               created="2020-01-01")
    queues = q.build(all_people(vault), TODAY)
    assert queues["reconnect"] == []
