"""Morning engine — the People section of the daily digest.

The relationship half of the 08:00 push: who has gone quiet past their cadence,
whose warm-up step is due, and what you told yourself you'd do today. It does
not get its own notification — it is folded into the existing unified digest in
todos.py, because two pushes in one morning is how a system starts getting
ignored.

Ranked by who is most overdue relative to their OWN cadence, and capped at
three names: a morning push that lists twelve people is a list nobody reads.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from . import dex, relationships

TOP_N = 3

# The staged half of the Pass-D batch. It counts, it never pushes: CLAUDE.md §3
# forbids bulk-writing unreviewed content, so the morning push can only say
# "there are N to look at" and the human confirms each one in the cockpit.
PUSH_STAGE = "push"


def _line(person, today: date) -> str:
    days = person.days_since_contact(today)
    if days is None:
        when = "never contacted"
    elif days == 0:
        when = "spoke today"
    elif days == 1:
        when = "1 day quiet"
    else:
        when = f"{days} days quiet"

    reasons = []
    if relationships.commitment_due(person, today):
        reasons.append("you owe them a step")
    elif relationships.warmup_due(person, today) and person.warmth_stage:
        reasons.append(f"warm-up due · {person.warmth_stage}")
    note = f" — {reasons[0]}" if reasons else ""
    return f"• {person.name} ({when}){note}"


def people_section(config, today: date, top_n: int = TOP_N) -> list[str]:
    """Digest lines for the people who need something, or [] when nobody does."""
    people = relationships.load_people(config.vault_path)
    flagged = relationships.needs_attention(people, today)
    if not flagged:
        return []
    lines = ["People:"] + [_line(p, today) for p in flagged[:top_n]]
    if len(flagged) > top_n:
        lines.append(f"• …and {len(flagged) - top_n} more on the People screen")
    return lines


# ---- the push queue, staged only -------------------------------------------------

def push_enabled(config) -> bool:
    """Is either push target set up at all? A cockpit with neither never gets
    nagged about a queue it can't act on."""
    scopes = str((config.raw.get("google") or {}).get("scopes") or "")
    # substring, not the full URL: api/google.py owns the scope constant, and
    # test_push.py pins that this fragment still matches it
    return dex.configured() or "auth/contacts" in scopes


def _last_push_dates(db_path: Path) -> dict[str, str]:
    """{person_id: date of their newest successful push}."""
    if not Path(db_path).exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        rows = conn.execute(
            "SELECT timestamp, message FROM events WHERE stage=? AND status='ok' "
            "ORDER BY id DESC", (PUSH_STAGE,)).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    out: dict[str, str] = {}
    for timestamp, message in rows:
        fields = dict(p.split("=", 1) for p in (message or "").split() if "=" in p)
        person_id = fields.get("person", "")
        if person_id and person_id not in out:
            out[person_id] = timestamp[:10]      # newest-first
    return out


def push_queue_count(config, db_path: Path) -> int:
    """How many people have moved on since their profile was last pushed out.
    Cheap by design — no model call, no external API, just the vault and the
    event log."""
    if not push_enabled(config):
        return 0
    pushed = _last_push_dates(db_path)
    count = 0
    for person in relationships.load_people(config.vault_path):
        if person.status == "dormant" or person.sample:
            continue
        last = pushed.get(person.id)
        if last is None:
            count += 1
        elif person.last_contact and person.last_contact.isoformat() >= last:
            count += 1
    return count


def push_section(config, db_path: Path) -> list[str]:
    """One digest line, or none. Never its own notification (same rule as the
    People section) and never an instruction to a machine — the cockpit is
    where a human reads each summary and confirms it."""
    count = push_queue_count(config, db_path)
    if not count:
        return []
    noun = "profile" if count == 1 else "profiles"
    return [f"{count} {noun} ready to push — review in the cockpit"]
