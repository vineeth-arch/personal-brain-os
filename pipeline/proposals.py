"""Relationship memory (Pass RM). What a capture says about a known person is
PROPOSED, never written: propose() only returns suggestions the watcher logs
to events.db, and apply() only runs after a human taps Remember in Triage
(api/notes.py::apply_person_proposal) — CLAUDE.md §3.

The proposal types and where each lands are SCHEMA-REFERENCE.md §7
"Handshake proposal types → sections". SECTIONS below mirrors that table —
keep the two in sync (pipeline/tests/test_proposals.py parses the table and
fails if they drift). Self-contained: stdlib + pipeline.llm/merge only."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from . import llm, merge

SECTIONS = {
    "fact": ("Facts",),
    "interpretation": ("Interpretations",),
    "commitment_mine": ("Interaction log", "Next action"),
    "commitment_theirs": ("Interaction log", "Next action"),
    "follow_up": ("Next action",),
    "personal_detail": ("Context",),
    "upcoming": ("Next action",),
    "milestone": ("Next action",),
    "need": ("Needs",),
    "company_knowledge": ("Facts",),     # on the COMPANY note, not the person
    "person_update": ("Context",),
}
TYPES = tuple(SECTIONS)
TOPICS = ("family", "health", "home", "interests", "preference", "favour")
PATCHABLE = ("company", "relationship")   # the only frontmatter a proposal may Fill
MAX_PROPOSALS = 12
MIN_FIRST_NAME = 3


# ---- the gate: no model call unless someone we know is named ------------------

def mentioned_people(transcript: str, people) -> list:
    """People named in the text: a full-name match, or a first name that only
    one person in 07-People carries (two Priyas → neither, rather than a
    guess). Deterministic — a capture about nobody costs no model call."""
    text = transcript or ""
    first_counts: dict[str, int] = {}
    for p in people:
        first = p.name.split()[0].lower() if p.name.split() else ""
        first_counts[first] = first_counts.get(first, 0) + 1
    found = []
    for p in people:
        if not p.id or not p.name.strip():
            continue
        if re.search(rf"\b{re.escape(p.name.strip())}\b", text, re.IGNORECASE):
            found.append(p)
            continue
        first = p.name.split()[0]
        if (len(first) >= MIN_FIRST_NAME and first_counts.get(first.lower()) == 1
                and re.search(rf"\b{re.escape(first)}\b", text, re.IGNORECASE)):
            found.append(p)
    return found


# ---- the model call ----------------------------------------------------------

def _prompt(transcript: str, people, captured: date) -> str:
    roster = "\n".join(f"- {p.id}: {p.name}" for p in people)
    return (
        "You help the owner remember what people told them. Read the capture "
        "below and list what it says about the people on the roster. Return "
        'ONLY JSON: {"proposals": [ ... ]}, each item '
        '{"type", "person_id", "text", "topic"?, "date"?, "field"?, "value"?}.\n'
        f"type is one of: {', '.join(TYPES)}.\n"
        "- fact: something stated. interpretation: YOUR reading, not stated — never "
        "dress a guess as a fact.\n"
        "- commitment_mine / commitment_theirs: a promise by the owner / by them.\n"
        "- follow_up: something the owner must do.\n"
        f"- personal_detail: needs topic, one of {', '.join(TOPICS)}.\n"
        "- upcoming: an event in THEIR life; needs date (YYYY-MM-DD).\n"
        "- milestone: new job, baby, move, award that already happened.\n"
        "- need: what they're looking for. company_knowledge: a durable fact "
        "about their organisation.\n"
        f"- person_update: a durable change; set field ({', '.join(PATCHABLE)}) "
        "and value when it is one of those.\n"
        "text is one short line in plain English. Resolve relative dates against "
        f"the capture date {captured.isoformat()}; omit date if none was said. "
        "person_id must come from the roster. Nothing worth remembering → "
        '{"proposals": []}.\n\n'
        f"ROSTER:\n{roster}\n\nCAPTURE:\n{transcript}"
    )


def validate_envelope(data: object) -> str | None:
    if not isinstance(data, dict) or not isinstance(data.get("proposals"), list):
        return 'expected {"proposals": [...]}'
    return None


def _iso(raw) -> str | None:
    try:
        return date.fromisoformat(str(raw)[:10]).isoformat() if raw else None
    except ValueError:
        return None


def clean(items: list, allowed_ids: set[str]) -> list[dict]:
    """Keep only well-formed items; one bad item never costs the good ones."""
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind, pid = item.get("type"), str(item.get("person_id") or "")
        text = str(item.get("text") or "").strip()
        if kind not in SECTIONS or pid not in allowed_ids or not text:
            continue
        p = {"type": kind, "person_id": pid, "text": " ".join(text.split())[:280]}
        day = _iso(item.get("date"))
        if kind == "upcoming" and not day:
            continue
        if day:
            p["date"] = day
        if kind == "personal_detail":
            topic = str(item.get("topic") or "").lower()
            p["topic"] = topic if topic in TOPICS else ""
        if kind == "person_update" and item.get("field") in PATCHABLE and item.get("value"):
            p["field"], p["value"] = item["field"], str(item["value"]).strip()
        out.append(p)
    return out[:MAX_PROPOSALS]


def propose(transcript: str, people, captured: date, config, *,
            extra_ids: tuple[str, ...] = (), llm_fn=None) -> list[dict]:
    """[] when nobody known is named, every provider failed, or nothing was
    worth remembering — never a guess. `llm_fn(prompt, config) -> dict|None`
    replaces the real call in tests."""
    named = mentioned_people(transcript, people)
    named_ids = {p.id for p in named}
    named += [p for p in people if p.id in extra_ids and p.id not in named_ids]
    if not named:
        return []
    prompt = _prompt(transcript, named, captured)
    if llm_fn is not None:
        data = llm_fn(prompt, config)
    else:
        data, _provider, _attempts = llm.complete_json(prompt, config, validate_envelope)
    if validate_envelope(data):
        return []
    return clean(data["proposals"], {p.id for p in named})


# ---- what a proposal becomes (shown on the card BEFORE the decision) ----------

def _short(day: date) -> str:
    return f"{day.day} {day.strftime('%b')}"


def outcome(p: dict, today: date) -> dict:
    """{section, line, due} — the exact line apply() will write. Next-action
    lines lead with their DUE date, because relationships.commitment_due
    fires on any YYYY-MM-DD in that section at or before today; the event's
    own date is written as "12 Oct" so it can never fire early."""
    kind, text = p["type"], p["text"]
    day = date.fromisoformat(p["date"]) if p.get("date") else None
    due: date | None = None
    if kind == "upcoming":
        due, line = day + timedelta(days=1), f"Ask how it went: {text} ({_short(day)})"
    elif kind == "milestone":
        due, line = today, f"Congratulate: {text}"
    elif kind in ("follow_up", "commitment_mine"):
        due = day or today
        line = f"{'I promised' if kind == 'commitment_mine' else 'Follow up'}: {text}"
    elif kind == "commitment_theirs":
        due = day + timedelta(days=1) if day else None
        line = f"Check in — they promised: {text}" + (f" (by {_short(day)})" if day else "")
    elif kind == "personal_detail" and p.get("topic"):
        line = f"{p['topic']} · {text}"
    elif kind == "person_update" and p.get("field"):
        line = f"{p['field']}: {p['value']} — {text}"
    else:
        line = text
    section = SECTIONS[kind][-1]
    return {"section": section, "line": line, "due": due.isoformat() if due else None}


def _split_front(text: str) -> tuple[str, str]:
    head, sep, body = text.partition("\n---\n")
    return head + sep, body


def _front_value(head: str, key: str) -> str:
    for line in head.splitlines():
        if line.startswith(f"{key}:"):
            return line.partition(":")[2].strip().strip("\"'")
    return ""


def _set_front(head: str, key: str, value: str) -> str:
    lines, seen = [], False
    for line in head.splitlines(keepends=True):
        if line.startswith(f"{key}:"):
            lines.append(f"{key}: {value}\n")
            seen = True
        else:
            lines.append(line)
    if not seen:  # insert before the closing fence
        lines.insert(len(lines) - 1, f"{key}: {value}\n")
    return "".join(lines)


def apply(note_text: str, p: dict, *, note_id: str, index: int, today: date) -> str:
    """The approved proposal written into a person (or company) note. Append
    only, dated, cited (`derived-from::` the capture), idempotent via the
    `<!-- bc: -->` marker — a repeat is a no-op."""
    from .relationships import append_marked

    text = note_text
    result = outcome(p, today)
    cite = f"· derived-from:: [[{note_id}]] (ai, approved)"
    marker_base = f"bc:{note_id}:{index}"

    if p["type"] in ("commitment_mine", "commitment_theirs"):
        who = "I promised" if p["type"] == "commitment_mine" else "They promised"
        text = append_marked(text, "Interaction log",
                             f"- {today.isoformat()} — {who}: {p['text']} {cite}",
                             f"<!-- {marker_base}:log -->")

    if p["type"] == "person_update" and p.get("field"):
        head, body = _split_front(text)
        fm = {p["field"]: _front_value(head, p["field"])}
        new_fm, suggestion = merge.apply_field(fm, p["field"], p["value"], source="cockpit",
                                               origin="ai", today=today.isoformat())
        if suggestion:
            text = append_marked(text, "Updates", suggestion, f"<!-- {marker_base}:upd -->")
        elif new_fm[p["field"]] != fm[p["field"]]:
            text = _set_front(head, p["field"], new_fm[p["field"]]) + body

    stamp = result["due"] or today.isoformat()
    if result["section"] == "Next action" and not result["due"]:
        stamp = "open"
    line = f"- {stamp} · {result['line']} {cite}"
    return append_marked(text, result["section"], line, f"<!-- {marker_base} -->")


def dumps(p: dict, *, note_id: str, index: int, title: str) -> str:
    return json.dumps({**p, "note_id": note_id, "index": index, "title": title})
