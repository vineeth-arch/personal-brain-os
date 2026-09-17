"""pipeline/tests/test_greene.py — the Greene helper (GREENE-HELPER.md v1.0,
RELATIONSHIP-OS-ARCHITECTURE.md A11): parsing the 18 situations out of
`_System/greene-helper.md`, the touch-type/relationship presets, seeding the
vault copy without ever clobbering an owner edit, and the two record-derived
reads (`pride`, `record`)."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from pipeline import greene as gr
from pipeline import relationships as rel
from pipeline import touchlog as tl

SEED = (Path(__file__).resolve().parents[1] / "seeds" / "greene-helper.md").read_text(encoding="utf-8")


def make_person(vault, name="Priya Raman", interpretations=""):
    """A full-shape person note (all §7 sections present) via
    `new_person_note`, with `interpretations` (already-formatted lines)
    inserted into `## Interpretations`."""
    filename, text = rel.new_person_note(name, "whatsapp", "+971500000000",
                                         datetime(2026, 8, 1, 10, 0))
    if interpretations:
        text = text.replace("## Interpretations\n\n", f"## Interpretations\n\n{interpretations}\n", 1)
    folder = vault / rel.PEOPLE_FOLDER
    folder.mkdir(exist_ok=True)
    (folder / filename).write_text(text, encoding="utf-8")
    return rel.load_people(vault)[0]


def touch(day, direction, touch_type):
    return tl.Touch(day=day, direction=direction, channel="whatsapp", touch_type=touch_type,
                    summary="x", greene="", requested=False, legacy=False)


# ---- parse -----------------------------------------------------------------

def test_parses_eighteen_situations_in_order():
    situations = gr.parse(SEED)
    assert len(situations) == 18
    assert [s.code for s in situations] == [f"3.{i}" for i in range(1, 19)]
    assert situations[0].title == "They push back on price"


def test_price_pushback_fields():
    s = gr.parse(SEED)[0]
    assert s.code == "3.1"
    assert "testing whether the number" in s.happening
    assert "defending the fee" in s.trap
    assert "hand them a choice between scopes" in s.move
    assert s.line.startswith("The fee for that scope is 4.5 lakh.")


def test_situation_3_13_line_is_its_own():
    s13 = next(s for s in gr.parse(SEED) if s.code == "3.13")
    assert s13.line.startswith("Speak through your work.")
    # the block after it (3.14) must not have leaked its own quote upward
    s14 = next(s for s in gr.parse(SEED) if s.code == "3.14")
    assert s14.line.startswith("I can't take this on in October")


def test_missing_line_does_not_steal_next():
    md = (
        "### 3.1 First situation\n"
        "**Actually happening:** a happens\n"
        "**Trap:** a trap\n"
        "**Move:** a move\n"
        "\n"
        "### 3.2 Second situation\n"
        "**Actually happening:** b happens\n"
        "**Trap:** b trap\n"
        "**Move:** b move\n"
        "> b's own line\n"
    )
    situations = gr.parse(md)
    assert len(situations) == 2
    assert situations[0].line == ""
    assert situations[1].line == "b's own line"


# ---- presets -----------------------------------------------------------------

def test_presets_ask_and_mentor():
    assert gr.presets("ask", []) == ["3.11"]
    assert gr.presets("ask", ["mentor"]) == ["3.11", "3.12"]
    assert gr.presets("kind_truth", ["mentor"]) == ["3.4", "3.15", "3.12"]
    assert gr.presets("unknown_touch_type", []) == []


# ---- reads -----------------------------------------------------------------

def test_pride_from_interpretations_only(tmp_path):
    lines = ("- 2026-08-01 · pride: her retail listing after 18 months\n"
            "- 2026-08-02 · caution: praises early\n")
    person = make_person(tmp_path, interpretations=lines)
    result = gr.reads(person, [])
    assert result["pride"] == "her retail listing after 18 months"


def test_reads_record_uses_ledger(tmp_path):
    person = make_person(tmp_path)
    touches = [touch(date(2026, 8, 1), "in", "promise_kept"),
              touch(date(2026, 8, 5), "in", "promise_late")]
    result = gr.reads(person, touches)
    assert result["record"] == "their promises: 1 kept · 1 late · 0 dropped"


# ---- ensure -----------------------------------------------------------------

def test_ensure_seeds_when_missing_reports_wrote(tmp_path):
    (tmp_path / "_System").mkdir()
    text, wrote = gr.ensure(tmp_path)
    assert wrote is True
    assert (tmp_path / gr.HELPER_FILE).read_text(encoding="utf-8") == text
    assert "GREENE-HELPER" in text


def test_ensure_never_overwrites_owner_edit(tmp_path):
    system = tmp_path / "_System"
    system.mkdir()
    dest = tmp_path / gr.HELPER_FILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("owner's own edited copy")
    text, wrote = gr.ensure(tmp_path)
    assert wrote is False
    assert text == "owner's own edited copy"
    assert dest.read_text(encoding="utf-8") == "owner's own edited copy"
