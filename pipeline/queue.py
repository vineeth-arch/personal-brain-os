"""Queue engine — SCHEMA-REFERENCE.md §7's seven views and the payload rule
(A4, A11.2): what a person needs to see this morning, sorted and capped at
five payload-carrying touches. Pure functions over `Person`, `Touch` and
`Held` objects — no I/O; `build()` reads a person's already-parsed
`raw_sections`/`dates`/`tier` and the ledger's read side (`is_quiet`,
`inside_floor`, `status_for`) to decide what belongs where, and `strip()`
turns the seven views into the one thing a person actually acts on."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from . import ledger, relationships, touchlog

VIEWS = ("owe_reply", "promises", "ask_about", "celebrate", "follow_up", "waiting_on_them", "reconnect")
LABELS = {"owe_reply": "I owe a reply", "promises": "Promises I made", "ask_about": "Ask about",
          "celebrate": "Celebrate", "follow_up": "Follow up today", "waiting_on_them": "Waiting on them",
          "reconnect": "Reconnect"}
DAILY_CAP = 5                  # extrapolation (A4)
REPLY_FLAG_DAYS = 2  # extrapolation (A5 metrics: "replies owed older than 48 h" — 48h expressed as 2 days at date resolution)
CELEBRATE_WINDOW_DAYS = 7  # extrapolation (A5: "a date in `dates` within 7 days")
OWE_TYPES = frozenset({"reply", "ask_theirs"})                # R10
BYPASS_QUIET = frozenset({"owe_reply", "promises", "celebrate"})
STRIP_SKIP = frozenset({"waiting_on_them"})                   # R23
TIER_ORDER = {"inner": 0, "core": 1, "active": 2, "": 3, "wide": 4}

_MM_DD = re.compile(r"^\d{2}-\d{2}$")
_YYYY_MM_DD = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Item:
    person_id: str
    name: str
    tier: str
    queue: str
    touch_type: str
    payload: str
    source_key: str
    due: str | None
    channel: str
    flagged: bool = False
    held: bool = False


@dataclass
class Held:
    person_id: str
    touch_type: str
    ready: bool


def settled(action, person) -> bool:
    """A dated Next action is settled once contact has happened on or after
    its due date — logging that contact answers it, so it doesn't need a
    separate close marker to leave the queue."""
    return action.due is not None and person.last_contact is not None and person.last_contact >= action.due


def _prefix_touch_type(text: str) -> str:
    """The touch type a reconnect payload borrows from an open Next action's
    own view, independent of the due-date gating that governs whether the
    action would normally show there (reconnect fires regardless)."""
    if text.startswith("I promised:"):
        return "keep_promise"
    if text.startswith("Congratulate:") or text.startswith("Remember date:"):
        return "celebrate"
    if text.startswith("Thank:") or text.startswith("Report outcome:"):
        return "thank"
    if text.startswith("Intro check-in:"):
        return "give_who"
    return "remember"


def _parse_month_day(raw: str) -> tuple[int, int] | None:
    """`dates` values: `YYYY-MM-DD` or `MM-DD`; anything else is ignored
    (P19), never raised."""
    raw = (raw or "").strip()
    if _YYYY_MM_DD.match(raw):
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            return None
        return (d.month, d.day)
    if _MM_DD.match(raw):
        try:
            month, day = int(raw[:2]), int(raw[3:])
            date(2000, month, day)   # validate, using a leap year for Feb 29
        except ValueError:
            return None
        return (month, day)
    return None


def _next_occurrence(month_day: tuple[int, int], today: date) -> date | None:
    month, day = month_day
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        return None   # e.g. Feb 29 in a non-leap year — no occurrence this cycle
    if candidate < today:
        try:
            candidate = date(today.year + 1, month, day)
        except ValueError:
            return None
    return candidate


def _date_occurrence(raw: str, today: date) -> date | None:
    """The next occurrence of a `dates` entry, if it falls within the
    celebrate window; else None (parse failure or too far out)."""
    month_day = _parse_month_day(raw)
    if month_day is None:
        return None
    occurrence = _next_occurrence(month_day, today)
    if occurrence is None:
        return None
    delta = (occurrence - today).days
    return occurrence if 0 <= delta <= CELEBRATE_WINDOW_DAYS else None


def _celebrate_payload(key: str, occurrence: date) -> str:
    label = "Birthday" if key == "birthday" else "Anniversary" if key == "anniversary" else key
    return f"{label} on {occurrence.day} {occurrence.strftime('%b')}"


def _channel(person) -> str:
    return person.preferred_channel() or ""


def _items_for_action(person, action, today: date) -> list[tuple[str, "Item"]]:
    """One Next action line → zero or one `(view, Item)` pairs, per the v2.2
    classification table. `open` is `not action.closed`, checked first —
    once closed nothing else about the line matters."""
    if action.closed:
        return []
    text, due = action.text, action.due

    def item(queue: str, touch_type: str) -> tuple[str, Item]:
        return queue, Item(person_id=person.id, name=person.name, tier=person.tier,
                           queue=queue, touch_type=touch_type, payload=text,
                           source_key=action.key, due=due.isoformat() if due else None,
                           channel=_channel(person))

    if text.startswith("I promised:"):
        # R12 — no last_contact test; a later contact never hides a promise.
        if due is None or due <= today:
            return [item("promises", "keep_promise")]
        return []

    if text.startswith("Check in — they promised:"):
        if due is not None and due <= today and not settled(action, person):
            return [item("ask_about", "remember")]
        if due is None or due > today:
            return [item("waiting_on_them", "remember")]
        return []

    if text.startswith("Ask how it went:"):
        if due is not None and due <= today and not settled(action, person):
            return [item("ask_about", "remember")]
        return []

    if text.startswith("Congratulate:") or text.startswith("Remember date:"):
        if due is not None and due <= today and not settled(action, person):
            return [item("celebrate", "celebrate")]
        return []

    if text.startswith("Thank:") or text.startswith("Report outcome:"):
        if due is not None and due <= today and not settled(action, person):
            return [item("celebrate", "thank")]
        return []

    if text.startswith("Intro check-in:"):
        if due is not None and due <= today and not settled(action, person):
            return [item("follow_up", "give_who")]
        return []

    if action.dated_format:
        if due is not None and due <= today and not settled(action, person):
            return [item("follow_up", "remember")]
        return []

    # Undated non-`open` line (R22): don't silently vanish — surface it once
    # relationships.commitment_due says the note itself is flagging today.
    if relationships.commitment_due(person, today):
        return [item("follow_up", "remember")]
    return []


def _owe_reply_item(person, touches: list[touchlog.Touch], today: date,
                    helds: list[Held]) -> Item | None:
    result = None
    relevant = [t for t in touches if not t.legacy and t.touch_type not in touchlog.PROMISE_IN]
    if relevant:
        latest = max(relevant, key=lambda t: t.day)
        if latest.direction == "in" and latest.touch_type in OWE_TYPES:
            flagged = (today - latest.day).days >= REPLY_FLAG_DAYS and not helds
            result = Item(person_id=person.id, name=person.name, tier=person.tier,
                         queue="owe_reply", touch_type=latest.touch_type, payload=latest.summary,
                         source_key=f"touch:{latest.day.isoformat()}:{latest.touch_type}",
                         due=None, channel=latest.channel, flagged=flagged)

    ready = [h for h in helds if h.ready]
    if ready:
        held = ready[0]
        result = Item(person_id=person.id, name=person.name, tier=person.tier,
                     queue="owe_reply", touch_type=held.touch_type,
                     payload="Held draft: still want to send this?",
                     source_key=f"held:{held.touch_type}", due=None, channel=_channel(person),
                     flagged=False, held=True)
    return result


def payload_for(person, touches: list[touchlog.Touch], today: date) -> tuple[str, str, str] | None:
    """The reconnect payload rule, in priority order: (1) the oldest open
    Next action of any kind — reuse it rather than invent a new reason to
    reach out; (2) a Greene-matcher hook — deferred, not built; (3) an
    upcoming date; (4) an unthanked give from them; falling back to a bare
    `presence` touch for the inner tier (the only tier where pure contact,
    no payload, is the doctrine), and nothing at all otherwise. Returns
    `(touch_type, payload, source_key)` — the three fields `build()` still
    needs to finish the Item."""
    open_actions = [a for a in relationships.next_actions(person) if not a.closed]
    if open_actions:
        oldest = min(open_actions, key=lambda a: (a.due is None, a.due or date.min))
        return _prefix_touch_type(oldest.text), oldest.text, oldest.key

    best = None
    for key, raw in (person.dates or {}).items():
        occurrence = _date_occurrence(raw, today)
        if occurrence is None:
            continue
        delta = (occurrence - today).days
        if best is None or delta < best[0]:
            best = (delta, key, occurrence)
    if best is not None:
        _, key, occurrence = best
        return "celebrate", _celebrate_payload(key, occurrence), f"date:{key}"

    real = [t for t in touches if not t.legacy]
    give_theirs = [t for t in real if t.direction == "in" and t.touch_type == "give_theirs"]
    if give_theirs:
        latest_give = max(give_theirs, key=lambda t: t.day)
        thanked_since = any(t.direction == "out" and t.touch_type == "thank" and t.day > latest_give.day
                            for t in real)
        if not thanked_since:
            return "thank", latest_give.summary, f"touch:{latest_give.day.isoformat()}:thank"

    if person.tier == "inner":
        return "presence", "", ""
    return None


def _reconnect_item(person, touches: list[touchlog.Touch], today: date) -> Item:
    result = payload_for(person, touches, today)
    if result is None:
        touch_type, payload, source_key = "", "", "reconnect"
    else:
        touch_type, payload, source_key = result
        source_key = source_key or "reconnect"
    return Item(person_id=person.id, name=person.name, tier=person.tier, queue="reconnect",
               touch_type=touch_type, payload=payload, source_key=source_key,
               due=None, channel=_channel(person))


def _sort_key(item: Item):
    return (
        0 if item.held else 1,
        0 if item.flagged else 1,
        (item.due is None, item.due or ""),
        TIER_ORDER.get(item.tier, len(TIER_ORDER)),
        item.name.lower(),
    )


def build(people, today: date, held: "list[Held]" = ()) -> dict[str, list[Item]]:
    held_by_person: dict[str, list[Held]] = {}
    for h in held:
        held_by_person.setdefault(h.person_id, []).append(h)

    queues: dict[str, list[Item]] = {view: [] for view in VIEWS}

    for person in people:
        touches = touchlog.parse_log(person.raw_sections.get("Interaction log", ""))
        drop_non_bypass = ledger.is_quiet(person, touches, today) or ledger.inside_floor(person, touches, today)

        per_view: dict[str, list[Item]] = {view: [] for view in VIEWS}

        owe_item = _owe_reply_item(person, touches, today, held_by_person.get(person.id, []))
        if owe_item is not None:
            per_view["owe_reply"].append(owe_item)

        for action in relationships.next_actions(person):
            for view, view_item in _items_for_action(person, action, today):
                per_view[view].append(view_item)

        for key, raw in (person.dates or {}).items():
            occurrence = _date_occurrence(raw, today)
            if occurrence is None:
                continue
            per_view["celebrate"].append(Item(
                person_id=person.id, name=person.name, tier=person.tier, queue="celebrate",
                touch_type="celebrate", payload=_celebrate_payload(key, occurrence),
                source_key=f"date:{key}", due=None, channel=_channel(person)))

        if person.has_cadence and ledger.status_for(person, today) != "dormant" and person.going_cold(today):
            per_view["reconnect"].append(_reconnect_item(person, touches, today))

        for view in VIEWS:
            if view not in BYPASS_QUIET and drop_non_bypass:
                continue
            queues[view].extend(per_view[view])

    for view in VIEWS:
        queues[view].sort(key=_sort_key)
    return queues


def strip(queues: dict[str, list[Item]]) -> tuple[list[Item], int]:
    """The Today strip (R23): iterate the views in engine-priority order,
    skipping `waiting_on_them` entirely — it names what's coming, not what
    to do this morning. A payload-less reconnect item (nothing to reach out
    about, and not even a bare `presence` prompt) doesn't count either. One
    item per person, first view wins; stop at five; `overflow` is how many
    more distinct people would otherwise have qualified."""
    result: list[Item] = []
    seen: set[str] = set()
    overflow: set[str] = set()

    for view in VIEWS:
        if view in STRIP_SKIP:
            continue
        for candidate in queues.get(view, []):
            if view == "reconnect" and candidate.payload == "" and candidate.touch_type != "presence":
                continue
            if candidate.person_id in seen:
                continue
            if len(result) < DAILY_CAP:
                result.append(candidate)
                seen.add(candidate.person_id)
            else:
                overflow.add(candidate.person_id)

    return result, len(overflow)


def to_dict(item: Item) -> dict:
    return {
        "person_id": item.person_id,
        "name": item.name,
        "tier": item.tier,
        "queue": item.queue,
        "touch_type": item.touch_type,
        "payload": item.payload,
        "source_key": item.source_key,
        "due": item.due,
        "channel": item.channel,
        "flagged": item.flagged,
        "held": item.held,
    }
