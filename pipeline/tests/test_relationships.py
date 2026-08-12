"""Lifecycle arithmetic (Pass 7). Every case passes `today` explicitly — the
thresholds are testable precisely because nothing here reads the clock."""
from __future__ import annotations

from datetime import date

from pipeline import people, relationships as rel

TODAY = date(2026, 8, 12)


def person(last_contact="2026-08-01", cadence="7", **extra):
    fm = {"type": "person", "last_contact": last_contact, "cadence_days": cadence}
    fm.update(extra)
    return fm


def test_within_cadence_is_active():
    assert rel.classify_person(person("2026-08-10", "7"), TODAY) == rel.ACTIVE
    # exactly at the cadence is still active — the cadence is a target, not a deadline
    assert rel.classify_person(person("2026-08-05", "7"), TODAY) == rel.ACTIVE


def test_past_cadence_is_cold():
    assert rel.classify_person(person("2026-08-04", "7"), TODAY) == rel.COLD    # 8 days
    assert rel.classify_person(person("2026-07-23", "7"), TODAY) == rel.COLD    # 20 days


def test_three_cadences_past_is_dormant():
    assert rel.classify_person(person("2026-07-22", "7"), TODAY) == rel.COLD    # 21 == 3x
    assert rel.classify_person(person("2026-07-21", "7"), TODAY) == rel.DORMANT  # 22 > 3x


def test_old_notes_without_the_new_fields_report_unset():
    """A person note written before Pass 7 must render, not crash or get
    invented thresholds."""
    assert rel.classify_person({"type": "person"}, TODAY) == rel.UNSET
    assert rel.classify_person(person(last_contact=None), TODAY) == rel.UNSET
    assert rel.classify_person(person(cadence=None), TODAY) == rel.UNSET
    assert rel.days_since_contact({"type": "person"}, TODAY) is None
    assert rel.days_overdue({"type": "person"}, TODAY) is None


def test_malformed_values_read_as_unset_never_raise():
    assert rel.classify_person(person("not-a-date", "7"), TODAY) == rel.UNSET
    assert rel.classify_person(person("2026-08-01", "soon"), TODAY) == rel.UNSET
    assert rel.classify_person(person("2026-08-01", "0"), TODAY) == rel.UNSET
    assert rel.classify_person(person("2026-08-01", "-5"), TODAY) == rel.UNSET


def test_future_last_contact_reads_as_today():
    assert rel.days_since_contact(person("2026-09-01"), TODAY) == 0
    assert rel.classify_person(person("2026-09-01", "7"), TODAY) == rel.ACTIVE


def test_days_overdue_counts_past_the_cadence_only():
    assert rel.days_overdue(person("2026-08-10", "7"), TODAY) == 0    # 2 days in
    assert rel.days_overdue(person("2026-08-01", "7"), TODAY) == 4    # 11 days, 4 over


def test_append_interaction_is_append_only():
    body = ("## Context\nMet at a conference.\n\n## Needs\nIntros.\n\n"
            "## Interaction log\n- 2026-07-01 — coffee\n\n## Next action\nSend the deck.")
    out = people.append_interaction(body, "- 2026-08-12 — contact logged")
    assert "- 2026-07-01 — coffee" in out           # history is never rewritten
    assert out.index("2026-07-01") < out.index("2026-08-12")
    assert out.index("2026-08-12") < out.index("## Next action")
    assert "Met at a conference." in out and "Send the deck." in out


def test_append_interaction_creates_a_missing_section():
    out = people.append_interaction("## Context\nOld note.", "- 2026-08-12 — contact logged")
    assert "## Interaction log" in out
    assert out.strip().endswith("- 2026-08-12 — contact logged")
    assert "Old note." in out


def test_interaction_log_reads_entries_back():
    body = "## Interaction log\n- 2026-07-01 — coffee\n- 2026-08-01 — call\n\n## Next action\nx"
    assert people.interaction_log(body) == ["2026-07-01 — coffee", "2026-08-01 — call"]


def test_stamp_status_writes_the_computed_value_but_never_unset():
    fm_block = "id: 20260101090000\ntype: person\nstatus: active"
    fm = person("2026-07-01", "7")     # long past 3x cadence
    assert "status: dormant" in people.stamp_status(fm_block, fm, TODAY)
    unchanged = people.stamp_status(fm_block, {"type": "person"}, TODAY)
    assert "status: active" in unchanged    # nothing computable, nothing written
