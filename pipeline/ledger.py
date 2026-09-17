"""Ledger — the read side of the v2.2 Relationship Doctrine (SCHEMA-
REFERENCE.md §7): gives/asks/received counts, their reliability record, the
contact floor, the quiet rule, whether an ask is allowed, and the two
give/ask ratio flags. Pure functions over `list[Touch]` and `Person`
objects — no I/O, no filesystem writes; `advance_statuses` hands back the
`(path, new_text)` pairs for a later task to write and commit."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import relationships, touchlog

COLD_X, DORMANT_X = 1.5, 3.0                                    # extrapolation (A4)
FLOOR_DAYS = {"inner": 0, "core": 10, "active": 30, "": 0}      # extrapolation (A11.2); "" untiered (P6)
NEW_RELATIONSHIP_DAYS = 90                                       # extrapolation (A11.2)
ASK_MIN_GIVES, ASK_MIN_DAYS = 3, 90                             # extrapolation (A6)
ONE_WAY_GIVES = 8                                                # extrapolation (A6)

# Narrower than the general "real touch" filter above: an `in · other` touch
# (e.g. reputation_signal — someone talking ABOUT the owner, not a reply FROM
# the quiet person) is not evidence the quiet person themselves replied, so it
# must not lift quiet. Only these in-touch types count as lifting/preventing
# quiet in `is_quiet`.
QUIET_LIFTING_TYPES = frozenset({"reply", "ask_theirs", "give_theirs"})


def _real(touches: list[touchlog.Touch]) -> list[touchlog.Touch]:
    """Touches that can ever count as a give, an ask or something received:
    legacy lines and promise-outcome lines are neither (PROMISE_IN is their
    own reliability read, never a touch that moves the ledger)."""
    return [t for t in touches if not t.legacy and t.touch_type not in touchlog.PROMISE_IN]


def counts(touches: list[touchlog.Touch], today: date) -> dict:
    """gives/asks/received over the trailing 90 and 180 days. `received` is
    an incoming `give_theirs` — the only in-touch that is a give from them."""
    result = {"gives90": 0, "asks90": 0, "received90": 0,
             "gives180": 0, "asks180": 0, "received180": 0}
    for t in _real(touches):
        age = (today - t.day).days
        if age < 0 or age > 180:
            continue
        if t.direction == "out" and t.touch_type in touchlog.GIVE_TYPES:
            result["gives180"] += 1
            if age <= 90:
                result["gives90"] += 1
        elif t.direction == "out" and t.touch_type == "ask":
            result["asks180"] += 1
            if age <= 90:
                result["asks90"] += 1
        elif t.direction == "in" and t.touch_type == "give_theirs":
            result["received180"] += 1
            if age <= 90:
                result["received90"] += 1
    return result


def reliability(touches: list[touchlog.Touch]) -> dict:
    """Their promise-keeping record, read only from incoming promise-outcome
    lines — never from a line we wrote ourselves (R11: ours is never their
    reliability)."""
    result = {"kept": 0, "late": 0, "dropped": 0}
    for t in touches:
        if t.legacy or t.direction != "in":
            continue
        if t.touch_type == "promise_kept":
            result["kept"] += 1
        elif t.touch_type == "promise_late":
            result["late"] += 1
        elif t.touch_type == "promise_dropped":
            result["dropped"] += 1
    return result


def reliability_line(r: dict) -> str:
    if not any(r.values()):
        return ""
    return f"their promises: {r['kept']} kept · {r['late']} late · {r['dropped']} dropped"


def status_for(person, today: date) -> str:
    """`dormant` never self-heals here (only a real touch revives it —
    touchlog.record_touch). Untiered/`wide`-without-cadence people and
    anyone never contacted keep whatever status the note already says —
    there's no cadence to measure them against."""
    if person.status == "dormant":
        return "dormant"
    if not person.has_cadence or person.last_contact is None:
        return person.status
    ratio = (today - person.last_contact).days / person.effective_cadence
    if ratio >= DORMANT_X:
        return "dormant"
    if ratio >= COLD_X:
        return "cold"
    return "active"


def inside_floor(person, touches: list[touchlog.Touch], today: date) -> bool:
    """The contact floor (SCHEMA §7 / A11.2): whether the tier's minimum
    contact cadence is currently being met. Exempt relationships (a new
    relationship still inside its 90-day grace window, a List of 20 target,
    `inner` tier, or untiered/legacy) never carry a floor at all — `wide` has
    no cap, so it is always considered inside it."""
    if person.created and (today - person.created).days < NEW_RELATIONSHIP_DAYS:
        return False
    if person.list_of_20:
        return False
    if person.tier in ("inner", ""):
        return False
    if person.tier == "wide":
        return True
    candidates = [t for t in touches
                 if t.direction == "out" and t.touch_type not in touchlog.FLOOR_EXEMPT
                 and not t.requested]
    if not candidates:
        return False
    latest = max(candidates, key=lambda t: t.day)
    return (today - latest.day).days <= FLOOR_DAYS.get(person.tier, 0)


def is_quiet(person, touches: list[touchlog.Touch], today: date) -> bool:
    """Still in a self-imposed quiet window, AND the last real touch was
    ours going out — a reply/ask/give FROM them lifts it immediately
    (QUIET_LIFTING_TYPES), but an `in · other` touch (e.g. reputation_signal,
    where someone else is talking about them, not they themselves) does not.
    A promise close never lifts or extends it either way (it isn't a new
    contact, in either direction)."""
    if not (person.quiet_until and person.quiet_until > today):
        return False
    relevant = _real(touches)
    if not relevant:
        return False
    latest = max(relevant, key=lambda t: t.day)
    return not (latest.direction == "in" and latest.touch_type in QUIET_LIFTING_TYPES)


def ask_allowed(person, touches: list[touchlog.Touch], today: date) -> bool:
    """Commercial relationships only; and only after enough gives since the
    last ask, given enough time since the last ask — the payload rule's ask
    side (A6)."""
    if not person.commercial:
        return False
    last_ask = person.last_ask
    gives_since = [t for t in _real(touches)
                  if t.direction == "out" and t.touch_type in touchlog.GIVE_TYPES
                  and (last_ask is None or t.day > last_ask)]
    if len(gives_since) < ASK_MIN_GIVES:
        return False
    if last_ask is not None and (today - last_ask).days < ASK_MIN_DAYS:
        return False
    return True


def flags(touches: list[touchlog.Touch], today: date) -> list[str]:
    """The two give/ask ratio warnings (A6): asking at least as often as
    giving, or giving a lot with nothing coming back either way."""
    c = counts(touches, today)
    out = []
    if c["asks180"] > 0 and c["asks180"] >= c["gives180"]:
        out.append("i_only_take")
    if c["gives180"] >= ONE_WAY_GIVES:
        has_in = any(t.direction == "in" and (today - t.day).days <= 180 for t in _real(touches))
        if not has_in:
            out.append("one_way_street")
    return out


def advance_statuses(people, today: date) -> list[tuple[Path, str]]:
    """The status-heartbeat pass: for anyone whose computed status differs
    from what the note says, the new frontmatter text (not yet written —
    the caller writes and commits). Untiered/legacy notes and `sample` test
    fixtures are skipped outright (R14) — a stage-based cadence of a few
    days would otherwise mark every one of them dormant the first time this
    ever runs."""
    changes: list[tuple[Path, str]] = []
    for person in people:
        if person.tier == "" or person.sample:
            continue
        new_status = status_for(person, today)
        if new_status == person.status:
            continue
        text = person.path.read_text(encoding="utf-8")
        changes.append((person.path, relationships._replace_field(text, "status", new_status)))
    return changes
