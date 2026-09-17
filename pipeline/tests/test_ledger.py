"""pipeline/tests/test_ledger.py — the ledger read side (SCHEMA-
REFERENCE.md §7 / A4, A6, A11.2): gives/asks/received counts, their
reliability record, the contact floor, the quiet rule, ask-allowed, the
give/ask ratio flags, and the status heartbeat. Pure functions over
`list[Touch]` and `Person`; a tmp vault only where a real `Person` (tier,
cadence, frontmatter) is needed."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from pipeline import ledger as lg
from pipeline import relationships as rel
from pipeline import touchlog as tl

TODAY = date(2026, 9, 17)


def make_person(vault, name="Priya Raman", *, tier="core", cadence="", status="active",
                sample=False, created="2026-01-01", list_of_20=False,
                relationship="client", last_contact="2026-08-01",
                last_give="", last_ask="", quiet_until=""):
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
        f"last_give: {last_give}\n"
        f"last_ask: {last_ask}\n"
        f"quiet_until: {quiet_until}\n"
        f"list_of_20: {str(list_of_20).lower()}\n"
        f"sample: {str(sample).lower()}\n"
        f"status: {status}\n"
        "---\n\n"
        f"# {name}\n\n"
        "## Interaction log\n\n\n\n"
        "## Next action\n\n\n", encoding="utf-8")
    return path


@pytest.fixture
def vault(tmp_path):
    (tmp_path / rel.PEOPLE_FOLDER).mkdir()
    return tmp_path


def one(vault):
    return rel.load_people(vault)[0]


def touch(day, direction, touch_type, *, legacy=False, requested=False):
    return tl.Touch(day=day, direction=direction, channel="whatsapp", touch_type=touch_type,
                    summary="x", greene="", requested=requested, legacy=legacy)


# ---- counts -----------------------------------------------------------------

def test_counts_gives_asks_received_within_windows():
    touches = [
        touch(TODAY - timedelta(days=10), "out", "give_know"),   # gives90+180
        touch(TODAY - timedelta(days=100), "out", "give_know"),  # gives180 only
        touch(TODAY - timedelta(days=200), "out", "give_know"),  # too old, ignored
        touch(TODAY - timedelta(days=5), "out", "ask"),          # asks90+180
        touch(TODAY - timedelta(days=150), "out", "ask"),        # asks180 only
        touch(TODAY - timedelta(days=20), "in", "give_theirs"),  # received90+180
    ]
    c = lg.counts(touches, TODAY)
    assert c == {"gives90": 1, "asks90": 1, "received90": 1,
                "gives180": 2, "asks180": 2, "received180": 1}


def test_legacy_never_counts_as_give():
    touches = [touch(TODAY - timedelta(days=1), "out", "give_know", legacy=True),
              touch(TODAY - timedelta(days=1), "out", "other", legacy=True)]
    c = lg.counts(touches, TODAY)
    assert c["gives90"] == 0 and c["gives180"] == 0


def test_promise_touches_never_count_in_counts():
    touches = [touch(TODAY - timedelta(days=1), "in", "promise_kept"),
              touch(TODAY - timedelta(days=1), "in", "promise_late"),
              touch(TODAY - timedelta(days=1), "in", "promise_dropped")]
    c = lg.counts(touches, TODAY)
    assert c == {"gives90": 0, "asks90": 0, "received90": 0,
                "gives180": 0, "asks180": 0, "received180": 0}


# ---- reliability --------------------------------------------------------------

def test_reliability_reads_incoming_promise_outcomes_only():
    touches = [
        touch(TODAY, "in", "promise_kept"),
        touch(TODAY, "in", "promise_kept"),
        touch(TODAY, "in", "promise_late"),
        touch(TODAY, "in", "promise_dropped"),
        touch(TODAY, "in", "promise_dropped"),
        touch(TODAY, "out", "keep_promise"),           # ours — never counted
        touch(TODAY, "in", "promise_kept", legacy=True),  # legacy — ignored
    ]
    assert lg.reliability(touches) == {"kept": 2, "late": 1, "dropped": 2}


def test_reliability_line_formats_and_empty():
    assert lg.reliability_line({"kept": 5, "late": 1, "dropped": 2}) == \
        "their promises: 5 kept · 1 late · 2 dropped"
    assert lg.reliability_line({"kept": 0, "late": 0, "dropped": 0}) == ""


# ---- status_for ---------------------------------------------------------------

def test_status_for_active_cold_dormant_ratios(vault):
    make_person(vault, tier="core", last_contact=(TODAY - timedelta(days=5)).isoformat())
    assert lg.status_for(one(vault), TODAY) == "active"

    make_person(vault, name="Cold Carl", tier="core",
               last_contact=(TODAY - timedelta(days=45)).isoformat())  # 30 * 1.5
    assert lg.status_for([p for p in rel.load_people(vault) if p.name == "Cold Carl"][0], TODAY) == "cold"


def test_dormant_at_3x(vault):
    make_person(vault, tier="core", last_contact=(TODAY - timedelta(days=90)).isoformat())  # 30 * 3
    assert lg.status_for(one(vault), TODAY) == "dormant"


def test_status_for_dormant_stays_dormant(vault):
    make_person(vault, tier="core", status="dormant",
               last_contact=(TODAY - timedelta(days=1)).isoformat())
    assert lg.status_for(one(vault), TODAY) == "dormant"


def test_status_for_no_last_contact_keeps_current_status(vault):
    make_person(vault, tier="core", status="active", last_contact="")
    assert lg.status_for(one(vault), TODAY) == "active"


# ---- inside_floor ---------------------------------------------------------------

def test_untiered_has_no_floor(vault):
    make_person(vault, tier="")
    assert lg.inside_floor(one(vault), [], TODAY) is False


def test_new_relationship_exempt_from_floor(vault):
    make_person(vault, tier="core", created=(TODAY - timedelta(days=10)).isoformat())
    assert lg.inside_floor(one(vault), [], TODAY) is False


def test_list_of_20_exempt(vault):
    make_person(vault, tier="core", list_of_20=True)
    assert lg.inside_floor(one(vault), [], TODAY) is False


def test_inner_tier_has_no_floor(vault):
    make_person(vault, tier="inner")
    assert lg.inside_floor(one(vault), [], TODAY) is False


def test_wide_always_inside_floor(vault):
    make_person(vault, tier="wide")
    assert lg.inside_floor(one(vault), [], TODAY) is True
    assert lg.inside_floor(one(vault), [touch(TODAY - timedelta(days=999), "out", "remember")], TODAY) is True


def test_core_inside_floor_within_window_outside_beyond(vault):
    make_person(vault, tier="core")
    person = one(vault)
    within = [touch(TODAY - timedelta(days=10), "out", "remember")]     # FLOOR_DAYS["core"] == 10
    assert lg.inside_floor(person, within, TODAY) is True
    beyond = [touch(TODAY - timedelta(days=11), "out", "remember")]
    assert lg.inside_floor(person, beyond, TODAY) is False


def test_inside_floor_ignores_exempt_and_requested_touches(vault):
    make_person(vault, tier="core")
    person = one(vault)
    touches = [touch(TODAY - timedelta(days=1), "out", "thank"),
              touch(TODAY - timedelta(days=1), "out", "remember", requested=True),
              touch(TODAY - timedelta(days=20), "out", "remember")]
    assert lg.inside_floor(person, touches, TODAY) is False


def test_inside_floor_no_qualifying_touch_is_false(vault):
    make_person(vault, tier="core")
    assert lg.inside_floor(one(vault), [], TODAY) is False


# ---- is_quiet -------------------------------------------------------------------

def test_is_quiet_true_when_last_real_touch_is_ours_out(vault):
    make_person(vault, tier="core", quiet_until=(TODAY + timedelta(days=10)).isoformat())
    person = one(vault)
    touches = [touch(TODAY - timedelta(days=1), "out", "give_know")]
    assert lg.is_quiet(person, touches, TODAY) is True


def test_is_quiet_false_when_quiet_until_in_past(vault):
    make_person(vault, tier="core", quiet_until=(TODAY - timedelta(days=1)).isoformat())
    person = one(vault)
    touches = [touch(TODAY - timedelta(days=5), "out", "give_know")]
    assert lg.is_quiet(person, touches, TODAY) is False


def test_quiet_lifts_when_they_reply(vault):
    make_person(vault, tier="core", quiet_until=(TODAY + timedelta(days=10)).isoformat())
    person = one(vault)
    touches = [touch(TODAY - timedelta(days=5), "out", "give_know"),
              touch(TODAY - timedelta(days=1), "in", "reply")]
    assert lg.is_quiet(person, touches, TODAY) is False


def test_promise_close_does_not_lift_quiet(vault):
    make_person(vault, tier="core", quiet_until=(TODAY + timedelta(days=10)).isoformat())
    person = one(vault)
    touches = [touch(TODAY - timedelta(days=5), "out", "give_know"),
              touch(TODAY - timedelta(days=1), "in", "promise_kept")]
    assert lg.is_quiet(person, touches, TODAY) is True


def test_in_other_touch_does_not_lift_quiet(vault):
    """An `in · other` touch (e.g. reputation_signal — someone else talking
    ABOUT the quiet person, not a reply FROM them) must not lift quiet."""
    make_person(vault, tier="core", quiet_until=(TODAY + timedelta(days=10)).isoformat())
    person = one(vault)
    touches = [touch(TODAY - timedelta(days=5), "out", "give_know"),
              touch(TODAY - timedelta(days=1), "in", "other")]
    assert lg.is_quiet(person, touches, TODAY) is True


# ---- ask_allowed ------------------------------------------------------------------

def test_ask_allowed_true_after_enough_gives_and_time(vault):
    make_person(vault, tier="core", last_ask=(TODAY - timedelta(days=100)).isoformat())
    person = one(vault)
    touches = [touch(TODAY - timedelta(days=90), "out", "give_know"),
              touch(TODAY - timedelta(days=60), "out", "give_know"),
              touch(TODAY - timedelta(days=30), "out", "give_know")]
    assert lg.ask_allowed(person, touches, TODAY) is True


def test_ask_allowed_false_not_enough_gives_since_last_ask(vault):
    make_person(vault, tier="core", last_ask=(TODAY - timedelta(days=100)).isoformat())
    person = one(vault)
    touches = [touch(TODAY - timedelta(days=90), "out", "give_know"),
              touch(TODAY - timedelta(days=60), "out", "give_know")]
    assert lg.ask_allowed(person, touches, TODAY) is False


def test_ask_allowed_false_too_soon_after_last_ask(vault):
    make_person(vault, tier="core", last_ask=(TODAY - timedelta(days=10)).isoformat())
    person = one(vault)
    touches = [touch(TODAY - timedelta(days=9), "out", "give_know"),
              touch(TODAY - timedelta(days=8), "out", "give_know"),
              touch(TODAY - timedelta(days=7), "out", "give_know")]
    assert lg.ask_allowed(person, touches, TODAY) is False


def test_ask_allowed_never_asked_before_needs_only_gives(vault):
    make_person(vault, tier="core", last_ask="")
    person = one(vault)
    touches = [touch(TODAY - timedelta(days=90), "out", "give_know"),
              touch(TODAY - timedelta(days=60), "out", "give_know"),
              touch(TODAY - timedelta(days=30), "out", "give_know")]
    assert lg.ask_allowed(person, touches, TODAY) is True


def test_family_never_ask_allowed(vault):
    make_person(vault, tier="core", relationship="family",
               last_ask="")
    person = one(vault)
    touches = [touch(TODAY - timedelta(days=90), "out", "give_know"),
              touch(TODAY - timedelta(days=60), "out", "give_know"),
              touch(TODAY - timedelta(days=30), "out", "give_know")]
    assert lg.ask_allowed(person, touches, TODAY) is False


# ---- flags ------------------------------------------------------------------------

def test_flags_i_only_take_when_asks_at_least_gives():
    touches = [touch(TODAY - timedelta(days=10), "out", "ask"),
              touch(TODAY - timedelta(days=20), "out", "ask")]
    assert "i_only_take" in lg.flags(touches, TODAY)


def test_flags_no_i_only_take_when_gives_exceed_asks():
    touches = [touch(TODAY - timedelta(days=10), "out", "ask"),
              touch(TODAY - timedelta(days=20), "out", "give_know"),
              touch(TODAY - timedelta(days=30), "out", "give_know")]
    assert "i_only_take" not in lg.flags(touches, TODAY)


def test_flags_one_way_street_when_no_reciprocation():
    touches = [touch(TODAY - timedelta(days=i), "out", "give_know") for i in range(1, 9)]
    assert "one_way_street" in lg.flags(touches, TODAY)


def test_flags_no_one_way_street_when_they_reciprocated():
    touches = [touch(TODAY - timedelta(days=i), "out", "give_know") for i in range(1, 9)]
    touches.append(touch(TODAY - timedelta(days=5), "in", "reply"))
    assert "one_way_street" not in lg.flags(touches, TODAY)


# ---- advance_statuses ---------------------------------------------------------------

def test_advance_statuses_returns_changed_people_only(vault):
    make_person(vault, name="Stale Sam", tier="core", status="active",
               last_contact=(TODAY - timedelta(days=90)).isoformat())   # -> dormant
    make_person(vault, name="Fresh Fred", tier="core", status="active",
               last_contact=(TODAY - timedelta(days=1)).isoformat())    # -> active, no change
    people = rel.load_people(vault)
    changes = lg.advance_statuses(people, TODAY)
    assert len(changes) == 1
    path, new_text = changes[0]
    assert path.name.startswith("2026-07-01-stale-sam")
    assert "status: dormant" in new_text


def test_heartbeat_skips_untiered(vault):
    make_person(vault, name="Legacy Lee", tier="", status="active",
               last_contact=(TODAY - timedelta(days=9999)).isoformat())
    people = rel.load_people(vault)
    assert lg.advance_statuses(people, TODAY) == []


def test_heartbeat_skips_sample(vault):
    make_person(vault, name="Sample Sue", tier="core", status="active", sample=True,
               last_contact=(TODAY - timedelta(days=90)).isoformat())
    people = rel.load_people(vault)
    assert lg.advance_statuses(people, TODAY) == []
