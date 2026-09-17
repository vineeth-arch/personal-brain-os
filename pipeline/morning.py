"""Morning engine — the People section of the daily digest.

The relationship half of the 08:00 push: who has gone quiet past their cadence,
whose warm-up step is due, and what you told yourself you'd do today. It does
not get its own notification — it is folded into the existing unified digest in
todos.py, because two pushes in one morning is how a system starts getting
ignored.

Built on the v2.2 queue engine (`queue.build`/`queue.strip`), so the digest
is capped at the same five payload-carrying touches a morning as the Today
strip and never disagrees with it.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from . import dex, ledger, queue, relationships, touchlog

# Alias kept so nothing outside this module has to know the digest cap moved
# to the queue engine's own constant (SCHEMA-REFERENCE.md §7, A4): at most
# five payload-carrying touches a morning, digest included.
TOP_N = queue.DAILY_CAP

# The staged half of the Pass-D batch. It counts, it never pushes: CLAUDE.md §3
# forbids bulk-writing unreviewed content, so the morning push can only say
# "there are N to look at" and the human confirms each one in the cockpit.
PUSH_STAGE = "push"


def _when(person, today: date) -> str:
    days = person.days_since_contact(today)
    if days is None:
        return "never contacted"
    if days == 0:
        return "spoke today"
    if days == 1:
        return "1 day quiet"
    return f"{days} days quiet"


def _heartbeat(vault: Path, people: list, today: date) -> list:
    """The status-heartbeat pass: commit the vault before AND after writing
    any status changes `ledger.advance_statuses` finds, so the batch write is
    reviewable and revertible (CLAUDE.md §3) — then reload so the digest
    itself reads the just-written statuses."""
    pairs = ledger.advance_statuses(people, today)
    if not pairs:
        return people
    from api.notes import git_commit_vault
    git_commit_vault(vault, "morning: before status heartbeat")
    for path, text in pairs:
        path.write_text(text, encoding="utf-8")
    git_commit_vault(vault, f"morning: status heartbeat ({len(pairs)})")
    return relationships.load_people(vault)


def _cold_regardless_of_status(people: list, today: date) -> list:
    """Reconnect-shaped items for everyone going cold by cadence math, without
    `queue.build`'s own `status_for != "dormant"` gate. That gate exists so a
    person already flagged dormant elsewhere doesn't also get a routine
    reconnect nudge — but an untiered note's `status_for` can read "dormant"
    from cadence math alone while the note's own `status` field, and
    `ledger.advance_statuses` (R14), both leave it "active" forever, since a
    heartbeat never touches an untiered note. The digest still has to speak
    up about someone slipping away, tiered or not."""
    items = []
    for person in people:
        if not person.has_cadence or not person.going_cold(today):
            continue
        touches = touchlog.parse_log(person.raw_sections.get("Interaction log", ""))
        items.append(queue._reconnect_item(person, touches, today))
    items.sort(key=queue._sort_key)
    return items


def people_section(config, today: date, top_n: int = TOP_N) -> list[str]:
    """Digest lines for the people who need something, or [] when nobody does.

    Built on the same queue engine the Today strip and People screen use
    (`queue.build`/`queue.strip`), so the digest never disagrees with what
    the cockpit itself shows. When nothing in any view survives `strip()`
    but someone is going cold with nothing to say about it yet, that person
    still pushes (SCHEMA-REFERENCE.md §7): a going-cold person is reason
    enough on their own.
    """
    vault = config.vault_path
    people = relationships.load_people(vault)
    people = _heartbeat(vault, people, today)
    if not people:
        return []

    queues = queue.build(people, today, held=[])
    items, overflow = queue.strip(queues)

    if not items:
        fallback = queues["reconnect"] or _cold_regardless_of_status(people, today)
        if fallback:
            items = fallback[:top_n]
            overflow = max(0, len(fallback) - top_n)
    else:
        items = items[:top_n]

    if not items:
        return []

    by_id = {p.id: p for p in people}
    lines = ["People:"]
    for item in items:
        person = by_id.get(item.person_id)
        when = _when(person, today) if person is not None else "never contacted"
        lines.append(f"• {item.name} ({when}) · {queue.LABELS[item.queue]} · "
                    f"{item.payload or 'no payload yet'}")
    if overflow:
        lines.append(f"• …and {overflow} more on the People screen")
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
