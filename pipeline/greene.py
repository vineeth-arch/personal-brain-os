"""Greene helper — the situation lookup for the draft composer's Greene panel
(GREENE-HELPER.md v1.0, RELATIONSHIP-OS-ARCHITECTURE.md A11). Parses the 18
situations out of `_System/greene-helper.md`, seeds that vault file from the
repo copy the first time it is missing (never overwriting an owner edit),
maps a `touch_type`/relationship to preset situation codes, and computes the
two record-derived reads (`pride`, `record`) the panel's line 1 shows. Pure
and read-only: this module never sends anything and never writes to a
person note."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import ledger

HELPER_FILE = "_System/greene-helper.md"
RULES_FILE = "_System/draft-rules.md"
SEEDS = Path(__file__).parent / "seeds"

PRESETS = {
    "kind_truth": ["3.4", "3.15"],
    "ask": ["3.11"],
    "keep_promise_late": ["3.9"],
    "celebrate": ["3.5"],
    "thank": ["3.6"],
}

_HEADING = re.compile(r"^### (3\.\d{1,2}) (.+)$")
_NEXT_SECTION = re.compile(r"^#{2,3} ")
_HAPPENING = re.compile(r"^\*\*Actually happening:\*\* (.+)$")
_TRAP = re.compile(r"^\*\*Trap:\*\* (.+)$")
_MOVE = re.compile(r"^\*\*Move:\*\* (.+)$")
_QUOTE = re.compile(r"^> (.+)$")
_PRIDE = re.compile(r"^-\s+(?:\d{4}-\d{2}-\d{2}\s+·\s+)?pride:\s*(.*)$")


@dataclass
class Situation:
    code: str
    title: str
    happening: str
    trap: str
    move: str
    line: str


def parse(md: str) -> list[Situation]:
    """Split `md` into `### 3.N Title` blocks, each bounded by the next
    `## ` or `### ` heading (R29) so a situation missing a field never picks
    up the next situation's — the search for each field stops at the
    boundary instead of running to end-of-file. Never raises: a missing
    field is `""`."""
    lines = md.splitlines()
    headings = []
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if m:
            headings.append((i, m.group(1), m.group(2)))

    situations = []
    for idx, (start, code, title) in enumerate(headings):
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if _NEXT_SECTION.match(lines[j]):
                end = j
                break
        block = lines[start + 1:end]

        happening = trap = move = quote_line = ""
        for line in block:
            if not happening and (m := _HAPPENING.match(line)):
                happening = m.group(1)
            elif not trap and (m := _TRAP.match(line)):
                trap = m.group(1)
            elif not move and (m := _MOVE.match(line)):
                move = m.group(1)
            elif not quote_line and (m := _QUOTE.match(line)):
                quote_line = m.group(1)

        situations.append(Situation(code=code, title=title, happening=happening,
                                    trap=trap, move=move, line=quote_line))
    return situations


def ensure(vault_path: Path, name: str = HELPER_FILE) -> tuple[str, bool]:
    """Return the vault's copy of `name`, seeding it from `SEEDS` the first
    time it is missing or blank (R28). Never overwrites a non-blank existing
    file — that would clobber an owner edit. Does not commit; the caller
    does."""
    dest = vault_path / name
    if dest.exists() and dest.read_text(encoding="utf-8").strip():
        return dest.read_text(encoding="utf-8"), False

    seed_text = (SEEDS / Path(name).name).read_text(encoding="utf-8")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(seed_text, encoding="utf-8")
    return seed_text, True


def presets(touch_type: str, relationships: list[str]) -> list[str]:
    """The situation codes to pre-select for a `touch_type`, plus 3.12
    (writing to a mentor) when `"mentor"` is among the person's
    relationships and not already in the list."""
    codes = list(PRESETS.get(touch_type, []))
    if "mentor" in relationships and "3.12" not in codes:
        codes.append("3.12")
    return codes


def reads(person, touches) -> dict:
    """Two of the panel's four reads that come from the record rather than
    the owner: `pride` (the first `pride:` line in Interpretations) and
    `record` (their reliability line from the ledger)."""
    pride = ""
    interpretations = person.sections.get("Interpretations", "")
    for line in interpretations.splitlines():
        m = _PRIDE.match(line.strip())
        if m:
            pride = m.group(1).strip()
            break

    record = ledger.reliability_line(ledger.reliability(touches))
    return {"pride": pride, "record": record}
