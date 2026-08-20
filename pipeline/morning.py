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

from datetime import date

from . import relationships

TOP_N = 3


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
