"""Relationship lifecycle — where a person sits against the cadence you chose.

SCHEMA-REFERENCE.md §6 fixes the vocabulary (`active → cold → dormant`) but not
the arithmetic, so this module fixes it: a person goes **cold** once more days
have passed than their `cadence_days`, and **dormant** at three times that. The
multiplier is the judgement call — one missed cycle is life, three is a
relationship that has quietly lapsed — and it lives here, in one named
constant, rather than being scattered through the API and the UI.

Everything is a pure function over parsed frontmatter with the date passed in.
No file reads, no clock reads: the caller supplies `today`, which is what makes
the thresholds testable without freezing time.

A person note that predates these fields is **not** an error. It reports
`unset`, and the UI asks for a cadence instead of inventing one — guessing here
would silently manufacture "going cold" nudges about people the owner never
asked to be nudged about.
"""
from __future__ import annotations

from datetime import date

# status values, verbatim from SCHEMA-REFERENCE.md §6
ACTIVE, COLD, DORMANT, UNSET = "active", "cold", "dormant", "unset"

# warmth ladder for warm-up targets, verbatim from SCHEMA-REFERENCE.md §7
WARMTH_STAGES = ["identified", "researched", "engaging", "conversing", "warm", "ready"]

# stored statuses a person note may carry (unset is computed, never written)
PERSON_STATUSES = [ACTIVE, COLD, DORMANT]

DORMANT_MULTIPLIER = 3


def parse_date(value: str | None) -> date | None:
    """Lenient ISO date read — a malformed or empty value is 'unknown', never
    an exception. Vault notes are hand-editable; one typo must not break a list."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def parse_cadence(value: str | None) -> int | None:
    """Positive whole days, or None. Zero and negatives are meaningless as a
    contact frequency, so they read as 'not set' rather than 'always cold'."""
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return days if days > 0 else None


def days_since_contact(fm: dict, today: date) -> int | None:
    """Whole days since `last_contact`, or None when it isn't set/parseable.
    A future date reads as 0 — logging contact today shouldn't render as -1."""
    last = parse_date(fm.get("last_contact"))
    if last is None:
        return None
    return max(0, (today - last).days)


def classify_person(fm: dict, today: date) -> str:
    """active | cold | dormant | unset, computed from last_contact + cadence_days.

    The computed value is what the UI groups by; the note's stored `status:` is
    stamped to match whenever the app writes the note, so an outside reader of
    the vault (Obsidian Bases, a query) sees the same answer."""
    cadence = parse_cadence(fm.get("cadence_days"))
    elapsed = days_since_contact(fm, today)
    if cadence is None or elapsed is None:
        return UNSET
    if elapsed > cadence * DORMANT_MULTIPLIER:
        return DORMANT
    if elapsed > cadence:
        return COLD
    return ACTIVE


def days_overdue(fm: dict, today: date) -> int | None:
    """How far past the cadence this person is (0 when on track). None when
    unset — the caller renders the set-up prompt instead of a number."""
    cadence = parse_cadence(fm.get("cadence_days"))
    elapsed = days_since_contact(fm, today)
    if cadence is None or elapsed is None:
        return None
    return max(0, elapsed - cadence)
