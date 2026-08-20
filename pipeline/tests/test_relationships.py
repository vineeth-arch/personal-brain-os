"""Pass MW — the arithmetic under the Relationship OS. SCHEMA-REFERENCE.md §7
gives the fields; these tests pin down what they MEAN: when someone has gone
cold, whose warm-up step is due, and who the morning should name first."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import morning, relationships as rel

TODAY = date(2026, 8, 20)


def person_note(folder: Path, name: str, *, last_contact="2026-08-01", cadence="",
                stage="", status="active", channels="{whatsapp: +971500000000, email: a@b.c}",
                next_action="", log="- 2026-08-01 — coffee at Alserkal", sample=""):
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
        f"channels: {channels}\n"
        f"cadence_days: {cadence}\n"
        f"last_contact: {last_contact}\n"
        f"warmth_stage: {stage}\n"
        f"status: {status}\n"
        + (f"sample: {sample}\n" if sample else "")
        + "---\n\n"
        f"# {name}\n\n## Context\n\nMet at a studio visit.\n\n## Needs\n\nA studio in Dubai.\n\n"
        f"## Interaction log\n\n{log}\n\n## Next action\n\n{next_action}\n")
    return path


@pytest.fixture
def vault(tmp_path):
    (tmp_path / rel.PEOPLE_FOLDER).mkdir()
    return tmp_path


# ---- parsing -------------------------------------------------------------------

def test_person_parses_including_the_inline_channels_map(vault):
    person_note(vault / rel.PEOPLE_FOLDER, "Priya Raman")
    people = rel.load_people(vault)
    assert len(people) == 1
    p = people[0]
    assert p.name == "Priya Raman" and p.relationship == "client"
    # channels is an inline YAML map — the flat frontmatter parser can't read it
    assert p.channels == {"whatsapp": "+971500000000", "email": "a@b.c"}
    assert p.last_contact == date(2026, 8, 1)
    assert "studio visit" in p.sections["Context"]


def test_blank_channel_values_are_dropped():
    assert rel.parse_channels("{whatsapp: +971, email: , linkedin: }") == {"whatsapp": "+971"}
    assert rel.parse_channels("") == {}


def test_non_person_notes_are_ignored(vault):
    (vault / rel.PEOPLE_FOLDER / "readme.md").write_text(
        "---\nid: 1\ntype: musing\n---\n\nnot a person\n")
    assert rel.load_people(vault) == []


def test_preferred_channel_follows_whatsapp_email_linkedin():
    p = rel.Person(id="1", name="X", path=Path("x"),
                   channels={"linkedin": "u", "email": "a@b.c"})
    assert p.preferred_channel() == "email"
    p.channels["whatsapp"] = "+971"
    assert p.preferred_channel() == "whatsapp"
    assert rel.Person(id="1", name="X", path=Path("x")).preferred_channel() is None


# ---- going cold ----------------------------------------------------------------

def test_cadence_falls_back_to_the_warmth_stage():
    p = rel.Person(id="1", name="X", path=Path("x"), warmth_stage="conversing")
    assert p.effective_cadence == rel.STAGE_CADENCE_DAYS["conversing"]
    p.cadence_days = 30
    assert p.effective_cadence == 30, "an explicit cadence always wins"
    assert rel.Person(id="1", name="X", path=Path("x")).effective_cadence == rel.DEFAULT_CADENCE_DAYS


def test_going_cold_is_measured_against_that_persons_own_cadence():
    fresh = rel.Person(id="1", name="X", path=Path("x"), cadence_days=30,
                       last_contact=date(2026, 8, 10))
    assert fresh.going_cold(TODAY) is False
    stale = rel.Person(id="2", name="Y", path=Path("y"), cadence_days=3,
                       last_contact=date(2026, 8, 10))
    assert stale.going_cold(TODAY) is True


def test_never_contacted_counts_as_cold():
    p = rel.Person(id="1", name="X", path=Path("x"), warmth_stage="identified")
    assert p.days_since_contact(TODAY) is None
    assert p.going_cold(TODAY) is True


def test_dormant_people_are_left_alone():
    p = rel.Person(id="1", name="X", path=Path("x"), status="dormant", cadence_days=1,
                   last_contact=date(2020, 1, 1))
    assert p.going_cold(TODAY) is False


# ---- warm-up + commitments -------------------------------------------------------

def test_warmup_is_due_for_ladder_stages_only():
    engaging = rel.Person(id="1", name="X", path=Path("x"), warmth_stage="engaging",
                          last_contact=date(2026, 8, 1))
    assert rel.warmup_due(engaging, TODAY) is True
    warm = rel.Person(id="2", name="Y", path=Path("y"), warmth_stage="warm",
                      last_contact=date(2026, 8, 1))
    assert rel.warmup_due(warm, TODAY) is False, "warm is a relationship, not a warm-up"
    unstaged = rel.Person(id="3", name="Z", path=Path("z"), last_contact=date(2026, 8, 1))
    assert rel.warmup_due(unstaged, TODAY) is False


def test_commitment_due_reads_the_next_action_section():
    def with_action(text):
        return rel.Person(id="1", name="X", path=Path("x"), sections={"Next action": text})

    assert rel.commitment_due(with_action("Send the deck today"), TODAY) is True
    assert rel.commitment_due(with_action("Follow up by 2026-08-18"), TODAY) is True
    assert rel.commitment_due(with_action("Follow up by 2026-09-30"), TODAY) is False
    assert rel.commitment_due(with_action(""), TODAY) is False


# ---- ranking -------------------------------------------------------------------

def test_ranking_puts_the_most_overdue_relative_to_cadence_first():
    weekly = rel.Person(id="1", name="Weekly", path=Path("a"), cadence_days=7,
                        last_contact=date(2026, 8, 13))          # 7 days, exactly due
    tight = rel.Person(id="2", name="Tight", path=Path("b"), cadence_days=3,
                       last_contact=date(2026, 7, 20))           # 31 days on a 3-day cadence
    quarterly = rel.Person(id="3", name="Quarterly", path=Path("c"), cadence_days=90,
                           last_contact=date(2026, 6, 1))        # 80 days, not yet due
    ordered = [p.name for p in rel.rank([weekly, quarterly, tight], TODAY)]
    assert ordered[0] == "Tight", "a 3-day contact a month silent outranks a 90-day one"
    assert ordered[-1] == "Quarterly"


def test_needs_attention_collects_cold_warmup_and_commitments(vault):
    folder = vault / rel.PEOPLE_FOLDER
    person_note(folder, "Cold One", last_contact="2026-06-01", cadence="7")
    person_note(folder, "Fresh One", last_contact="2026-08-19", cadence="30")
    person_note(folder, "Owed One", last_contact="2026-08-19", cadence="30",
                next_action="Send the studio deck today")
    flagged = {p.name for p in rel.needs_attention(rel.load_people(vault), TODAY)}
    assert flagged == {"Cold One", "Owed One"}


# ---- writers -------------------------------------------------------------------

def test_log_contact_appends_a_dated_line_and_resets_last_contact(vault):
    path = person_note(vault / rel.PEOPLE_FOLDER, "Priya Raman", last_contact="2026-06-01")
    person = rel.load_people(vault)[0]
    updated = rel.log_contact(person, "Sent a note about the studio", TODAY, channel="whatsapp")
    path.write_text(updated)

    again = rel.load_people(vault)[0]
    assert again.last_contact == TODAY
    assert again.days_since_contact(TODAY) == 0 and again.going_cold(TODAY) is False
    log = again.interaction_log()
    assert "- 2026-08-20 (whatsapp) — Sent a note about the studio" in log
    assert "- 2026-08-01 — coffee at Alserkal" in log, "the log is append-only"
    # the rest of the note survives untouched
    assert "A studio in Dubai." in again.sections["Needs"]


def test_logging_contact_revives_a_cold_person(vault):
    path = person_note(vault / rel.PEOPLE_FOLDER, "X", status="cold")
    person = rel.load_people(vault)[0]
    path.write_text(rel.log_contact(person, "caught up", TODAY))
    assert rel.load_people(vault)[0].status == "active"


def test_set_warmth_stage_only_accepts_the_six_stages(vault):
    path = person_note(vault / rel.PEOPLE_FOLDER, "X", stage="engaging")
    person = rel.load_people(vault)[0]
    path.write_text(rel.set_warmth_stage(person, "conversing"))
    assert rel.load_people(vault)[0].warmth_stage == "conversing"
    with pytest.raises(ValueError):
        rel.set_warmth_stage(rel.load_people(vault)[0], "besties")


def test_next_stage_walks_the_ladder_and_stops_at_the_top():
    assert rel.next_stage("identified") == "researched"
    assert rel.next_stage("ready") is None
    assert rel.next_stage("") is None


def test_append_context_keeps_existing_context(vault):
    path = person_note(vault / rel.PEOPLE_FOLDER, "X")
    person = rel.load_people(vault)[0]
    path.write_text(rel.append_context(person, ["- Head of Studio at Alserkal <!-- origin: ai -->"]))
    context = rel.load_people(vault)[0].sections["Context"]
    assert "Met at a studio visit." in context and "Head of Studio" in context


# ---- the morning section ---------------------------------------------------------

def cfg(vault):
    return SimpleNamespace(vault_path=vault, raw={})


def test_people_section_names_the_top_three_with_days_quiet(vault):
    folder = vault / rel.PEOPLE_FOLDER
    for i, day in enumerate(["2026-06-01", "2026-06-05", "2026-06-10", "2026-06-15"]):
        person_note(folder, f"Person {i}", last_contact=day, cadence="7")
    lines = morning.people_section(cfg(vault), TODAY)
    assert lines[0] == "People:"
    assert len(lines) == 5, "three names plus an overflow line"
    assert "days quiet" in lines[1]
    assert "1 more on the People screen" in lines[-1]


def test_people_section_is_empty_when_nobody_needs_anything(vault):
    person_note(vault / rel.PEOPLE_FOLDER, "Fresh", last_contact="2026-08-19", cadence="30")
    assert morning.people_section(cfg(vault), TODAY) == []


def test_people_section_says_never_contacted_rather_than_guessing(vault):
    person_note(vault / rel.PEOPLE_FOLDER, "New Lead", last_contact="", stage="identified")
    lines = morning.people_section(cfg(vault), TODAY)
    assert "never contacted" in lines[1]


def test_a_missing_people_folder_is_a_quiet_no_op(tmp_path):
    assert morning.people_section(cfg(tmp_path), TODAY) == []
