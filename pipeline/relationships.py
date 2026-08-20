"""Relationship OS — reading and writing 07-People notes.

The person schema is SCHEMA-REFERENCE.md §7 exactly; nothing here invents a
field. What this module adds is the arithmetic the schema implies but doesn't
state: when a person has gone cold (last_contact + cadence), which warm-up
action is due, and how to rank "who needs me most" for the morning digest.

Two things the note format needs that the flat frontmatter parser can't do:
`channels` is an inline YAML map, and the body's `## Interaction log` is
append-only. Both live here.

Writers commit the vault (CLAUDE.md §3). Nothing in this module sends anything
to anyone — it prepares, the human sends (CLAUDE.md §4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

PEOPLE_FOLDER = "07-People"

# SCHEMA-REFERENCE.md §7 — the six stages, in order of warmth
WARMTH_STAGES = ["identified", "researched", "engaging", "conversing", "warm", "ready"]

# How often a relationship at each stage wants a touch, when the note doesn't
# say. Early stages move fast (the trail goes cold), a warm one can breathe.
STAGE_CADENCE_DAYS = {
    "identified": 7,
    "researched": 5,
    "engaging": 4,
    "conversing": 3,
    "warm": 7,
    "ready": 3,
}
DEFAULT_CADENCE_DAYS = 14

CHANNEL_PRIORITY = ["whatsapp", "email", "linkedin"]

_INLINE_MAP = re.compile(r"\{(.*)\}")


def parse_channels(raw: str) -> dict[str, str]:
    """`{whatsapp: +9715…, email: a@b.c, linkedin: }` → dict, blanks dropped."""
    m = _INLINE_MAP.search(raw or "")
    if not m:
        return {}
    out = {}
    for part in m.group(1).split(","):
        key, _, value = part.partition(":")
        key, value = key.strip().strip("\"'"), value.strip().strip("\"'")
        if key and value:
            out[key.lower()] = value
    return out


@dataclass
class Person:
    id: str
    name: str                      # from the filename/title — humans read this
    path: Path
    relationship: str = ""
    company: str = ""
    channels: dict[str, str] = field(default_factory=dict)
    cadence_days: int | None = None
    last_contact: date | None = None
    warmth_stage: str = ""
    dex_id: str = ""
    dex_deeplink: str = ""
    status: str = "active"
    sample: bool = False
    sections: dict[str, str] = field(default_factory=dict)

    @property
    def effective_cadence(self) -> int:
        """What the note says, else what the warmth stage implies."""
        if self.cadence_days:
            return self.cadence_days
        return STAGE_CADENCE_DAYS.get(self.warmth_stage, DEFAULT_CADENCE_DAYS)

    def days_since_contact(self, today: date) -> int | None:
        if not self.last_contact:
            return None
        return (today - self.last_contact).days

    def going_cold(self, today: date) -> bool:
        """Past the cadence — or never contacted at all, which is the coldest
        a relationship gets."""
        if self.status == "dormant":
            return False
        days = self.days_since_contact(today)
        return True if days is None else days >= self.effective_cadence

    def overdue_by(self, today: date) -> int:
        """Days past due; a never-contacted person counts as one cadence past."""
        days = self.days_since_contact(today)
        if days is None:
            return self.effective_cadence
        return max(0, days - self.effective_cadence)

    def next_action(self) -> str:
        return (self.sections.get("Next action") or "").strip()

    def interaction_log(self) -> str:
        return (self.sections.get("Interaction log") or "").strip()

    def preferred_channel(self, priority: list[str] | None = None) -> str | None:
        for channel in (priority or CHANNEL_PRIORITY):
            if self.channels.get(channel):
                return channel
        return None


def _parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _sections(body: str) -> dict[str, str]:
    """Body → {H2 heading: text}. Mirrors api/notes._sections, keyed for lookup."""
    out: dict[str, str] = {}
    heading, buf = "", []
    for line in body.splitlines():
        if line.strip().startswith("## "):
            if heading:
                out[heading] = "\n".join(buf).strip()
            heading, buf = line.strip()[3:].strip(), []
        else:
            buf.append(line)
    if heading:
        out[heading] = "\n".join(buf).strip()
    return out


def _title(text: str, path: Path) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    # filename is "YYYY-MM-DD-kebab-name.md" (SCHEMA §9) — strip the date
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", path.stem)
    return stem.replace("-", " ").title()


def parse_person(path: Path) -> Person | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    if not text.startswith("---\n"):
        return None
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return None
    fm: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()
    if fm.get("type") != "person":
        return None
    try:
        cadence = int(fm.get("cadence_days") or 0) or None
    except ValueError:
        cadence = None
    return Person(
        id=fm.get("id", ""),
        name=_title(parts[2], path),
        path=path,
        relationship=fm.get("relationship", ""),
        company=fm.get("company", ""),
        channels=parse_channels(fm.get("channels", "")),
        cadence_days=cadence,
        last_contact=_parse_date(fm.get("last_contact", "")),
        warmth_stage=(fm.get("warmth_stage") or "").strip().lower(),
        dex_id=fm.get("dex_id", "").strip(),
        dex_deeplink=fm.get("dex_deeplink", "").strip(),
        status=(fm.get("status") or "active").strip().lower(),
        sample=(fm.get("sample") or "").strip().lower() == "true",
        sections=_sections(parts[2]),
    )


def load_people(vault_path: Path) -> list[Person]:
    folder = Path(vault_path) / PEOPLE_FOLDER
    if not folder.is_dir():
        return []
    people = [parse_person(p) for p in sorted(folder.glob("*.md"))]
    return [p for p in people if p]


def find_person(vault_path: Path, person_id: str) -> Person | None:
    for person in load_people(vault_path):
        if person.id == person_id:
            return person
    return None


# ---- what needs me today -------------------------------------------------------

_TODAY_WORDS = re.compile(r"\b(today|now|overdue|asap|this morning|tonight)\b", re.I)


def commitment_due(person: Person, today: date) -> bool:
    """A "Next action" that names today, or a dated line already past."""
    text = person.next_action()
    if not text:
        return False
    if _TODAY_WORDS.search(text):
        return True
    for match in re.findall(r"\d{4}-\d{2}-\d{2}", text):
        due = _parse_date(match)
        if due and due <= today:
            return True
    return False


def warmup_due(person: Person, today: date) -> bool:
    """A stage-based warm-up: someone being worked up the warmth ladder whose
    stage cadence has elapsed. `warm`/`ready` are relationships, not warm-ups."""
    if not person.warmth_stage or person.warmth_stage in ("warm", "ready"):
        return False
    if person.status == "dormant":
        return False
    days = person.days_since_contact(today)
    return True if days is None else days >= STAGE_CADENCE_DAYS.get(
        person.warmth_stage, DEFAULT_CADENCE_DAYS)


def cadence_progress(person: Person, today: date) -> float:
    """How far through their cadence this person is: 1.0 = due today, 2.0 =
    twice as long as they should have been left. A never-contacted person is
    treated as one full cadence overdue."""
    cadence = max(1, person.effective_cadence)
    days = person.days_since_contact(today)
    if days is None:
        return 2.0
    return days / cadence


def rank(people: list[Person], today: date) -> list[Person]:
    """Who needs me most: furthest through their OWN cadence first, so a 3-day
    contact a month silent outranks a 90-day one a fortnight silent — and a
    weekly contact due today outranks a quarterly one with time still to run."""
    return sorted(people, key=lambda p: (-cadence_progress(p, today), p.name.lower()))


def needs_attention(people: list[Person], today: date) -> list[Person]:
    flagged = [p for p in people
               if p.status != "dormant"
               and (p.going_cold(today) or warmup_due(p, today) or commitment_due(p, today))]
    return rank(flagged, today)


# ---- writers -------------------------------------------------------------------

def _replace_field(text: str, key: str, value: str) -> str:
    """Set a column-0 frontmatter field, adding it if the note lacks it."""
    head, sep, body = text.partition("\n---\n")
    lines, seen = [], False
    for line in head.splitlines():
        if line.startswith(f"{key}:"):
            lines.append(f"{key}: {value}")
            seen = True
        else:
            lines.append(line)
    if not seen:
        lines.append(f"{key}: {value}")
    return "\n".join(lines) + sep + body


def _append_to_section(text: str, heading: str, line: str) -> str:
    """Append a line under an H2, creating the section if it isn't there."""
    marker = f"## {heading}"
    if marker not in text:
        return text.rstrip() + f"\n\n{marker}\n\n{line}\n"
    out, inserted = [], False
    lines = text.splitlines()
    for i, current in enumerate(lines):
        out.append(current)
        if inserted or current.strip() != marker:
            continue
        # find where this section ends (next H2 or EOF) and append at its tail
        j = i + 1
        while j < len(lines) and not lines[j].strip().startswith("## "):
            out.append(lines[j])
            j += 1
        while out and not out[-1].strip():
            out.pop()
        out.append(line)
        out.append("")
        out.extend(lines[j:])
        inserted = True
        break
    return "\n".join(out).rstrip() + "\n"


def log_contact(person: Person, note: str, when: date, *, channel: str = "") -> str:
    """Append a dated line to the interaction log and reset last_contact.

    Returns the new file text (the caller writes it and commits the vault)."""
    text = person.path.read_text()
    detail = note.strip() or "Reached out."
    via = f" ({channel})" if channel else ""
    text = _append_to_section(text, "Interaction log", f"- {when.isoformat()}{via} — {detail}")
    text = _replace_field(text, "last_contact", when.isoformat())
    if person.status == "cold":
        text = _replace_field(text, "status", "active")
    return text


def set_warmth_stage(person: Person, stage: str) -> str:
    if stage not in WARMTH_STAGES:
        raise ValueError(f"{stage!r} is not one of the six warmth stages")
    return _replace_field(person.path.read_text(), "warmth_stage", stage)


def next_stage(stage: str) -> str | None:
    """The stage after this one, or None at the top of the ladder."""
    if stage not in WARMTH_STAGES:
        return None
    i = WARMTH_STAGES.index(stage)
    return WARMTH_STAGES[i + 1] if i + 1 < len(WARMTH_STAGES) else None


def slug(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return cleaned or "person"


def new_person_note(name: str, channel_kind: str, channel_value: str,
                    when: datetime) -> tuple[str, str]:
    """(filename, text) for a brand-new warm-up target.

    Schema-exact per SCHEMA-REFERENCE.md §7 — the same field set the seed
    writes, minus `sample`. It starts at `identified` because that is what a
    name and one channel IS: someone spotted, not yet researched. The body
    sections exist but are empty; the point of the quick-add is that adding a
    target must cost one input and one tap, not a trip to Obsidian.
    """
    if not name.strip():
        raise ValueError("a target needs a name")
    if channel_kind not in CHANNEL_PRIORITY:
        raise ValueError(f"{channel_kind!r} is not one of {', '.join(CHANNEL_PRIORITY)}")
    if not channel_value.strip():
        raise ValueError("a target needs one way to reach them")

    note_id = when.strftime("%Y%m%d%H%M%S")
    created = when.date().isoformat()
    text = (
        "---\n"
        f"id: {note_id}\n"
        "type: person\n"
        f"created: {created}\n"
        "source: manual\n"
        "origin: human\n"          # the owner typed this, not a model
        "relationship:\n"
        "company:\n"
        f"channels: {{{channel_kind}: {channel_value.strip()}}}\n"
        "dex_id:\n"
        "dex_deeplink:\n"
        "cadence_days:\n"
        "last_contact:\n"
        "warmth_stage: identified\n"
        "status: active\n"
        "categories: []\n"
        "subjects: []\n"
        "tags: []\n"
        "---\n\n"
        f"# {name.strip()}\n\n"
        "## Context\n\n\n"
        "## Needs\n\n\n"
        "## Interaction log\n\n\n"
        "## Next action\n\n\n"
    )
    return f"{created}-{slug(name)}.md", text


def create_person(vault_path: Path, name: str, channel_kind: str, channel_value: str,
                  when: datetime | None = None) -> Person:
    """Write the note and hand back the parsed person. Raises ValueError on bad
    input; the caller turns that into a plain-English refusal and commits."""
    when = when or datetime.now()
    folder = Path(vault_path) / PEOPLE_FOLDER
    folder.mkdir(parents=True, exist_ok=True)

    # The id is the durable handle every link points at (SCHEMA §1), so it has
    # to be unique even for two targets added inside the same second — step the
    # timestamp forward until no note in the vault claims it.
    taken = {p.id for p in load_people(vault_path)}
    while when.strftime("%Y%m%d%H%M%S") in taken:
        when += timedelta(seconds=1)

    filename, text = new_person_note(name, channel_kind, channel_value, when)
    path = folder / filename
    # two targets with the same name on the same day: never overwrite a note
    suffix = 2
    while path.exists():
        path = folder / f"{filename[:-3]}-{suffix}.md"
        suffix += 1
    path.write_text(text)
    return parse_person(path)


def append_context(person: Person, lines: list[str]) -> str:
    """Add AI-found facts under ## Context, flagged as AI-written (SCHEMA §1)."""
    text = person.path.read_text()
    for line in lines:
        text = _append_to_section(text, "Context", line)
    return text
