"""Pass RM — relationship memory. What a capture says about a known person is
proposed (never written), and an approved proposal lands in exactly the
section SCHEMA-REFERENCE.md §7's taxonomy table names."""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from pipeline import proposals as pr
from pipeline import relationships as rel
from pipeline.tests.test_relationships import person_note, vault  # noqa: F401

TODAY = date(2026, 9, 17)
NOTE_ID = "20260917101500"
SCHEMA = Path(__file__).resolve().parents[2] / "SCHEMA-REFERENCE.md"


def _people(vault, *names):
    for i, name in enumerate(names):
        path = person_note(vault / rel.PEOPLE_FOLDER, name)
        path.write_text(path.read_text().replace("20260701090000", f"2026070109000{i}"))
    return rel.load_people(vault)


# ---- the SCHEMA table and the code can't drift ---------------------------------

def test_sections_mirror_the_schema_table():
    text = SCHEMA.read_text(encoding="utf-8")
    table = text.split("#### Handshake proposal types → sections", 1)[1]
    rows = {}
    for line in table.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] and not set(cells[0]) <= {"-", " "} and cells[0] != "Proposal":
            rows[cells[0].replace("\\_", "_")] = tuple(re.findall(r"`## ([^`]+)`", cells[2]))
        elif rows and not line.strip():
            break
    assert rows == pr.SECTIONS


# ---- the gate ---------------------------------------------------------------------

def test_nobody_named_means_no_model_call(vault):
    people = _people(vault, "Priya Raman")
    calls = []
    out = pr.propose("groceries and a gym session", people, TODAY, None,
                     llm_fn=lambda *a: calls.append(a))
    assert out == [] and calls == []


def test_first_name_matches_only_when_unique(vault):
    people = _people(vault, "Priya Raman", "Priya Shah", "Omar Khan")
    named = pr.mentioned_people("met Priya and Omar for coffee", people)
    assert [p.name for p in named] == ["Omar Khan"]
    named = pr.mentioned_people("Priya Shah called", people)
    assert [p.name for p in named] == ["Priya Shah"]


def test_invalid_items_are_dropped_not_the_whole_batch(vault):
    people = _people(vault, "Priya Raman")
    pid = people[0].id
    reply = {"proposals": [
        {"type": "upcoming", "person_id": pid, "text": "moving to Dubai", "date": "2026-10-12"},
        {"type": "upcoming", "person_id": pid, "text": "no date given"},
        {"type": "gossip", "person_id": pid, "text": "not a type"},
        {"type": "fact", "person_id": "99999999999999", "text": "not on the roster"},
    ]}
    out = pr.propose("met Priya, she's moving to Dubai on 12 Oct", people, TODAY, None,
                     llm_fn=lambda *a: reply)
    assert out == [{"type": "upcoming", "person_id": pid, "text": "moving to Dubai",
                    "date": "2026-10-12"}]


def test_garbage_from_the_model_is_nothing_not_a_crash(vault):
    people = _people(vault, "Priya Raman")
    assert pr.propose("Priya", people, TODAY, None, llm_fn=lambda *a: None) == []
    assert pr.propose("Priya", people, TODAY, None, llm_fn=lambda *a: {"x": 1}) == []


# ---- what an approval writes ---------------------------------------------------------

def _apply(vault, p, index=0):
    person = _people(vault, "Priya Raman")[0]
    p = {"person_id": person.id, **p}
    text = pr.apply(person.path.read_text(), p, note_id=NOTE_ID, index=index, today=TODAY)
    person.path.write_text(text)
    return rel.parse_person(person.path), text


def test_upcoming_becomes_a_next_action_due_the_day_after(vault):
    person, text = _apply(vault, {"type": "upcoming", "text": "moving to Dubai",
                                  "date": "2026-10-12"})
    assert "- 2026-10-13 · Ask how it went: moving to Dubai (12 Oct)" in person.next_action()
    assert f"derived-from:: [[{NOTE_ID}]]" in text and f"<!-- bc:{NOTE_ID}:0 -->" in text
    assert not rel.commitment_due(person, date(2026, 10, 12))   # never early
    assert rel.commitment_due(person, date(2026, 10, 13))


def test_applying_twice_is_a_no_op(vault):
    person = _people(vault, "Priya Raman")[0]
    p = {"type": "fact", "person_id": person.id, "text": "Has two kids"}
    once = pr.apply(person.path.read_text(), p, note_id=NOTE_ID, index=0, today=TODAY)
    twice = pr.apply(once, p, note_id=NOTE_ID, index=0, today=TODAY)
    assert once == twice and once.count("Has two kids") == 1


def test_facts_and_interpretations_never_share_a_section(vault):
    person, _ = _apply(vault, {"type": "fact", "text": "Moving to Dubai"})
    text = pr.apply(person.path.read_text(),
                    {"type": "interpretation", "person_id": person.id, "text": "Seems unsettled"},
                    note_id=NOTE_ID, index=1, today=TODAY)
    person.path.write_text(text)
    person = rel.parse_person(person.path)
    assert "Moving to Dubai" in person.sections["Facts"]
    assert "Seems unsettled" in person.sections["Interpretations"]
    assert "Seems unsettled" not in person.sections["Facts"]


def test_their_promise_logs_and_waits(vault):
    person, _ = _apply(vault, {"type": "commitment_theirs", "text": "send the brief"})
    assert "They promised: send the brief" in person.interaction_log()
    assert "- open · Check in — they promised: send the brief" in person.next_action()
    assert not rel.commitment_due(person, TODAY)


def test_person_update_fills_blank_and_suggests_over_a_set_value(vault):
    person, text = _apply(vault, {"type": "person_update", "text": "joined Emaar",
                                  "field": "company", "value": "Emaar"})
    # the seed note already says company: Alserkal — never overwritten
    assert "company: Alserkal" in text
    assert "company: Alserkal → Emaar? (cockpit, ai)" in person.sections["Updates"]


def test_personal_detail_is_topic_prefixed_in_context(vault):
    person, _ = _apply(vault, {"type": "personal_detail", "topic": "family",
                               "text": "Daughter starts school in Sept"})
    assert "family · Daughter starts school in Sept" in person.sections["Context"]


# ---- v2.2 additions (SCHEMA-REFERENCE.md §7) ------------------------------------

def test_give_theirs_logs_in_touch_thanks_today_reports_in_30(vault):
    person, _ = _apply(vault, {"type": "give_theirs", "text": "introduced me to their CFO"})
    log = person.interaction_log()
    assert (f"- {TODAY.isoformat()} · in ·  · give_theirs · introduced me to their CFO"
            in log)
    next_action = person.next_action()
    assert f"- {TODAY.isoformat()} · Thank: introduced me to their CFO" in next_action
    report_due = (TODAY + timedelta(days=30)).isoformat()
    assert f"- {report_due} · Report outcome: introduced me to their CFO" in next_action


def test_intro_logs_give_who_and_checks_in_after_14_days(vault):
    person, _ = _apply(vault, {"type": "intro", "text": "introduced Omar to Priya",
                               "date": "2026-09-20"})
    log = person.interaction_log()
    assert f"- {TODAY.isoformat()} · out ·  · give_who · introduced Omar to Priya" in log
    due = (date(2026, 9, 20) + timedelta(days=14)).isoformat()
    assert f"- {due} · Intro check-in: introduced Omar to Priya" in person.next_action()


def test_intro_without_date_uses_today_plus_14(vault):
    person, _ = _apply(vault, {"type": "intro", "text": "introduced Omar to Priya"})
    due = (TODAY + timedelta(days=14)).isoformat()
    assert f"- {due} · Intro check-in: introduced Omar to Priya" in person.next_action()


def test_important_date_reminds_a_week_before(vault):
    person, _ = _apply(vault, {"type": "important_date", "text": "birthday",
                               "date": "2026-10-01"})
    due = date(2026, 9, 24).isoformat()  # 2026-10-01 minus 7 days
    assert f"- {due} · Remember date: birthday (1 Oct)" in person.next_action()


def test_important_date_without_date_is_dropped(vault):
    people = _people(vault, "Priya Raman")
    pid = people[0].id
    reply = {"proposals": [{"type": "important_date", "person_id": pid, "text": "birthday"}]}
    out = pr.propose("Priya's birthday is coming", people, TODAY, None, llm_fn=lambda *a: reply)
    assert out == []


def test_problem_lands_in_current_state(vault):
    person, _ = _apply(vault, {"type": "problem", "text": "cash flow is tight this quarter"})
    assert "cash flow is tight this quarter" in person.sections["Current state"]


def test_give_mine_writes_exactly_one_log_line(vault):
    person, text = _apply(vault, {"type": "give_mine", "text": "sent them the intro to Zara"})
    log = person.interaction_log()
    assert (f"- {TODAY.isoformat()} · out ·  · give_know · sent them the intro to Zara"
            in log)
    assert text.count("sent them the intro to Zara") == 1


def test_reputation_signal_writes_exactly_one_log_line(vault):
    person, text = _apply(vault, {"type": "reputation_signal", "text": "always follows through"})
    log = person.interaction_log()
    assert (f"- {TODAY.isoformat()} · in ·  · other · They said about me: always follows through"
            in log)
    assert text.count("always follows through") == 1


def test_relationship_update_writes_bracket_list(vault):
    # the seed note already has relationship: client (a legacy scalar)
    person, text = _apply(vault, {"type": "person_update", "text": "became a friend too",
                                  "field": "relationship", "value": "friend"})
    assert "relationship: [client, friend]" in text
    assert "Updates" not in person.sections or "relationship" not in person.sections["Updates"]
