"""pipeline/tests/test_touchlog.py — v2 interaction log format (SCHEMA-
REFERENCE.md §7): Touch parsing/formatting, give/ask dates, the quiet rule,
and promise close. Pure functions over note text; a tmp vault only where a
Person object (tier/cadence/frontmatter) is needed."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from pipeline import proposals as pr
from pipeline import relationships as rel
from pipeline import touchlog as tl

TODAY = date(2026, 9, 17)


def make_person(vault, name="Priya Raman", *, tier="core", cadence="", status="active",
                interaction_log="", next_action="", last_contact="2026-08-01",
                last_give="", last_ask="", quiet_until=""):
    folder = vault / rel.PEOPLE_FOLDER
    path = folder / f"2026-07-01-{name.lower().replace(' ', '-')}.md"
    path.write_text(
        "---\n"
        "id: 20260701090000\n"
        "type: person\n"
        "created: 2026-07-01\n"
        "source: manual\n"
        "origin: human\n"
        "relationship: client\n"
        "company: Alserkal\n"
        "channels: {whatsapp: +971500000000}\n"
        f"tier: {tier}\n"
        f"cadence_days: {cadence}\n"
        f"last_contact: {last_contact}\n"
        f"last_give: {last_give}\n"
        f"last_ask: {last_ask}\n"
        f"quiet_until: {quiet_until}\n"
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


def one(vault):
    return rel.load_people(vault)[0]


# ---- format_line / parse_log round trips ---------------------------------------

def test_round_trip_with_empty_channel():
    line = tl.format_line(TODAY, "out", "", "remember", "loved the show")
    assert line == f"- {TODAY.isoformat()} · out ·  · remember · loved the show"
    t = tl.parse_log(line)[0]
    assert (t.day, t.direction, t.channel, t.touch_type, t.summary) == \
        (TODAY, "out", "", "remember", "loved the show")
    assert t.legacy is False


def test_round_trip_summary_with_separator():
    line = tl.format_line(TODAY, "out", "whatsapp", "give_know",
                          "shared the deck · and notes\nline two")
    t = tl.parse_log(line)[0]
    assert t.summary == "shared the deck, and notes line two"
    assert t.channel == "whatsapp"


def test_tags_parse_greene_and_requested():
    line = tl.format_line(TODAY, "out", "email", "ask", "need intro",
                          greene="3.4", requested=True)
    assert line == f"- {TODAY.isoformat()} · out · email · ask · need intro · greene:3.4 · requested"
    t = tl.parse_log(line)[0]
    assert t.greene == "3.4" and t.requested is True and t.summary == "need intro"


def test_cite_and_marker_tail_stripped():
    line = tl.format_line(TODAY, "in", "email", "reply", "thanks!")
    decorated = f"{line} · derived-from:: [[20260917101500]] (ai, approved) <!-- bc:abc123 -->"
    t = tl.parse_log(decorated)[0]
    assert t.summary == "thanks!" and t.channel == "email"


def test_legacy_contact_line_is_legacy_touch():
    t = tl.parse_log("- 2026-08-01 (whatsapp) — coffee at Alserkal")[0]
    assert t.legacy is True and t.direction == "out" and t.touch_type == "other"
    assert t.channel == "whatsapp" and t.summary == "coffee at Alserkal"


def test_legacy_promise_line_ignored():
    assert tl.parse_log("- 2026-08-01 — I promised: send the deck") == []
    assert tl.parse_log("- 2026-08-01 — They promised: send the brief") == []


def test_handshake_heading_block_ignored():
    block = "### Handshake enrichment\n\nSome notes about a meeting.\n- not a touch line\n"
    assert tl.parse_log(block) == []


def test_task1_proposal_lines_parse(vault):
    make_person(vault)
    person = one(vault)
    text = person.path.read_text()
    for i, p in enumerate([
        {"type": "give_mine", "text": "sent the intro to Zara"},
        {"type": "give_theirs", "text": "introduced me to their CFO"},
        {"type": "intro", "text": "introduced Omar to Priya"},
        {"type": "reputation_signal", "text": "always follows through"},
    ]):
        text = pr.apply(text, p, note_id="20260917101500", index=i, today=TODAY)
    types = {(t.direction, t.touch_type) for t in tl.parse_log(text)}
    assert {("out", "give_know"), ("in", "give_theirs"),
            ("out", "give_who"), ("in", "other")} <= types


# ---- record_touch: frontmatter dates -------------------------------------------

def test_give_sets_last_give_and_contact(vault):
    make_person(vault, last_contact="2026-08-01")
    person = one(vault)
    text = tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                           channel="whatsapp", touch_type="remember", summary="loved the show")
    person.path.write_text(text)
    updated = one(vault)
    assert updated.last_contact == TODAY
    assert updated.last_give == TODAY


def test_ask_sets_last_ask(vault):
    make_person(vault, last_contact="2026-08-01")
    person = one(vault)
    text = tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                           channel="email", touch_type="ask", summary="can we grab coffee")
    person.path.write_text(text)
    updated = one(vault)
    assert updated.last_ask == TODAY
    assert updated.last_give is None


def test_touch_revives_cold_and_dormant(vault):
    for status in ("cold", "dormant"):
        make_person(vault, name=f"Person {status}", status=status, last_contact="2026-06-01")
    people = {p.name: p for p in rel.load_people(vault)}
    for status in ("cold", "dormant"):
        p = people[f"Person {status}"]
        text = tl.record_touch(p.path.read_text(), p, day=TODAY, direction="out",
                               channel="whatsapp", touch_type="remember", summary="hi")
        p.path.write_text(text)
    reloaded = {p.name: p for p in rel.load_people(vault)}
    assert reloaded["Person cold"].status == "active"
    assert reloaded["Person dormant"].status == "active"


# ---- quiet rule -----------------------------------------------------------------

def test_second_unprompted_out_sets_quiet_2x_cadence(vault):
    make_person(vault, tier="core", last_contact="2026-08-01")
    person = one(vault)
    text = tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                           channel="whatsapp", touch_type="remember", summary="first")
    person.path.write_text(text)
    person = one(vault)
    assert person.quiet_until is None

    day2 = TODAY + timedelta(days=1)
    text = tl.record_touch(person.path.read_text(), person, day=day2, direction="out",
                           channel="whatsapp", touch_type="give_know", summary="second")
    person.path.write_text(text)
    person = one(vault)
    assert person.quiet_until == day2 + timedelta(days=tl.QUIET_MULTIPLIER * person.effective_cadence)


def test_wide_uses_fallback_window(vault):
    make_person(vault, tier="wide", last_contact="2026-08-01")
    person = one(vault)
    text = tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                           channel="whatsapp", touch_type="remember", summary="first")
    person.path.write_text(text)
    person = one(vault)

    day2 = TODAY + timedelta(days=1)
    text = tl.record_touch(person.path.read_text(), person, day=day2, direction="out",
                           channel="whatsapp", touch_type="give_know", summary="second")
    person.path.write_text(text)
    person = one(vault)
    assert person.quiet_until == day2 + timedelta(days=tl.QUIET_MULTIPLIER * tl.QUIET_FALLBACK_DAYS)


def test_reply_between_resets_quiet_count(vault):
    make_person(vault, tier="core", last_contact="2026-08-01")
    person = one(vault)
    text = tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                           channel="whatsapp", touch_type="remember", summary="out one")
    person.path.write_text(text)
    person = one(vault)
    text = tl.record_touch(person.path.read_text(), person, day=TODAY, direction="in",
                           channel="whatsapp", touch_type="reply", summary="thanks")
    person.path.write_text(text)
    person = one(vault)
    text = tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                           channel="whatsapp", touch_type="give_know", summary="out two")
    person.path.write_text(text)
    person = one(vault)
    assert person.quiet_until is None


def test_thank_and_requested_do_not_count(vault):
    make_person(vault, tier="core", last_contact="2026-08-01")
    person = one(vault)
    text = tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                           channel="whatsapp", touch_type="remember", summary="out one")
    person.path.write_text(text)
    person = one(vault)
    # a thank (floor-exempt) between two unprompted outs breaks the streak
    text = tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                           channel="whatsapp", touch_type="thank", summary="thanks!")
    person.path.write_text(text)
    person = one(vault)
    text = tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                           channel="whatsapp", touch_type="give_know", summary="out two")
    person.path.write_text(text)
    person = one(vault)
    assert person.quiet_until is None

    # a requested out touch also breaks the streak
    text = tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                           channel="whatsapp", touch_type="remember", summary="requested",
                           requested=True)
    person.path.write_text(text)
    person = one(vault)
    text = tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                           channel="whatsapp", touch_type="give_know", summary="out three")
    person.path.write_text(text)
    person = one(vault)
    assert person.quiet_until is None


def test_promise_close_in_lines_do_not_break_quiet_count(vault):
    make_person(vault, tier="core", last_contact="2026-08-01",
               next_action="- 2026-09-10 · They promised: send the brief <!-- bc:promise1 -->")
    person = one(vault)
    text = tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                           channel="whatsapp", touch_type="remember", summary="out one")
    person.path.write_text(text)
    person = one(vault)
    text = tl.close_promise(person.path.read_text(), person, key="promise1", result="kept",
                            side="theirs", day=TODAY)
    person.path.write_text(text)
    person = one(vault)
    day2 = TODAY + timedelta(days=1)
    text = tl.record_touch(person.path.read_text(), person, day=day2, direction="out",
                           channel="whatsapp", touch_type="give_know", summary="out two")
    person.path.write_text(text)
    person = one(vault)
    assert person.quiet_until is not None


def test_legacy_lines_never_trigger_quiet(vault):
    make_person(vault, tier="core", last_contact="2026-08-01",
               interaction_log="- 2026-08-01 (whatsapp) — coffee\n- 2026-08-05 (email) — lunch")
    person = one(vault)
    text = tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                           channel="whatsapp", touch_type="remember", summary="only real touch")
    person.path.write_text(text)
    person = one(vault)
    assert person.quiet_until is None


# ---- validation -------------------------------------------------------------------

def test_in_type_on_out_raises(vault):
    make_person(vault)
    person = one(vault)
    with pytest.raises(ValueError):
        tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                        channel="", touch_type="reply", summary="x")


def test_bad_greene_code_raises(vault):
    make_person(vault)
    person = one(vault)
    with pytest.raises(ValueError):
        tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                        channel="", touch_type="remember", summary="x", greene="bad")


# ---- promise close ----------------------------------------------------------------

def test_close_theirs_writes_promise_in_and_closes(vault):
    make_person(vault, next_action="- 2026-09-10 · They promised: send the brief <!-- bc:promise1 -->")
    person = one(vault)
    text = tl.close_promise(person.path.read_text(), person, key="promise1", result="kept",
                            side="theirs", day=TODAY)
    assert (f"- {TODAY.isoformat()} · in ·  · promise_kept · They promised: send the brief "
            "<!-- bc:close:promise1 -->") in text
    person.path.write_text(text)
    updated = one(vault)
    action = [a for a in rel.next_actions(updated) if a.key == "promise1"][0]
    assert action.closed is True


def test_close_mine_kept_writes_keep_promise_touch_and_closes(vault):
    make_person(vault, next_action="- 2026-09-10 · I promised: send the deck <!-- bc:promise2 -->",
               last_contact="2026-08-01")
    person = one(vault)
    text = tl.close_promise(person.path.read_text(), person, key="promise2", result="kept",
                            side="mine", day=TODAY)
    assert (f"- {TODAY.isoformat()} · out ·  · keep_promise · I promised: send the deck "
            "<!-- bc:close:promise2 -->") in text
    person.path.write_text(text)
    updated = one(vault)
    assert updated.last_contact == TODAY
    action = [a for a in rel.next_actions(updated) if a.key == "promise2"][0]
    assert action.closed is True


def test_close_mine_dropped_is_not_a_touch(vault):
    make_person(vault, next_action="- 2026-09-10 · I promised: send the deck <!-- bc:promise3 -->",
               last_contact="2026-08-01")
    person = one(vault)
    text = tl.close_promise(person.path.read_text(), person, key="promise3", result="dropped",
                            side="mine", day=TODAY)
    assert (f"- {TODAY.isoformat()} · dropped: I promised: send the deck "
            "<!-- bc:close:promise3 -->") in text
    assert "keep_promise" not in text
    person.path.write_text(text)
    updated = one(vault)
    assert updated.last_contact == date(2026, 8, 1)   # not a touch — unchanged
    action = [a for a in rel.next_actions(updated) if a.key == "promise3"][0]
    assert action.closed is True


def test_record_touch_idempotent(vault):
    make_person(vault)
    person = one(vault)
    once = tl.record_touch(person.path.read_text(), person, day=TODAY, direction="out",
                           channel="whatsapp", touch_type="remember", summary="loved the show")
    twice = tl.record_touch(once, person, day=TODAY, direction="out", channel="whatsapp",
                            touch_type="remember", summary="loved the show")
    assert once == twice
    assert twice.count("loved the show") == 1
