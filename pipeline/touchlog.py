"""Touch log v2 — SCHEMA-REFERENCE.md §7's `## Interaction log` grammar.

A "touch" is one dated, directional, typed line: what went out (a give, an
ask, a promise kept) or what came in (a reply, their give, a promise's
outcome). This module is the only place that writes or reads that grammar —
`record_touch` is the single writer every caller (proposals, meetings, the
composer) goes through, so the quiet rule and the give/ask dates can never
drift out of sync with what the log actually says.

Two format eras coexist in real vault files: the v2 line this module writes,
and the flat `- date (channel) — note` line every note had before it. Both
must parse without raising — a person's whole history lives in one file, and
a malformed line must never take the rest of the log down with it.

Nothing here sends anything to anyone (CLAUDE.md §4); it only prepares the
note text the caller writes and commits."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from . import merge, relationships

# SCHEMA-REFERENCE.md §7 — the touch type vocabularies.
OUT_TYPES = ("remember", "give_know", "give_who", "celebrate", "keep_promise", "keep_promise_late",
             "kind_truth", "thank", "invite", "ask", "presence")
IN_TYPES = ("reply", "give_theirs", "ask_theirs", "promise_kept", "promise_late", "promise_dropped", "other")
GIVE_TYPES = frozenset({"remember", "give_know", "give_who", "celebrate", "keep_promise",
                        "keep_promise_late", "kind_truth", "thank"})
# Types that never count against the payload/quiet limits — closing a
# promise or a thank-you is a response, not a new ask on someone's time.
FLOOR_EXEMPT = frozenset({"keep_promise", "keep_promise_late", "thank", "celebrate"})
# Incoming promise-outcome lines: never count as a reply for the quiet rule.
PROMISE_IN = frozenset({"promise_kept", "promise_late", "promise_dropped"})
QUIET_MULTIPLIER = 2          # extrapolation (A11.2)
QUIET_FALLBACK_DAYS = 30      # extrapolation (A11.2) — untiered/wide fallback


@dataclass
class Touch:
    """One parsed line of `## Interaction log`."""
    day: date
    direction: str
    channel: str
    touch_type: str
    summary: str
    greene: str
    requested: bool
    legacy: bool


# `channel` optionally matches nothing before its own middot, so format_line's
# own empty-channel output (two spaces between the dots) still parses (R2).
_V2 = re.compile(r"^- (\d{4}-\d{2}-\d{2}) · (out|in) · ([a-z]*) ?· ([a-z_]+) · (.*)$")
_LEGACY = re.compile(r"^- (\d{4}-\d{2}-\d{2})(?: \(([a-z]+)\))? — (.*)$")
_TAIL = re.compile(r"[ \t]*(?:· derived-from:: \[\[[^\]]*\]\] \([^)]*\))?[ \t]*(?:<!-- bc:[^>]*-->)?[ \t]*$")
_GREENE = re.compile(r"^greene:3\.\d{1,2}$")
_GREENE_CODE = re.compile(r"^3\.\d{1,2}$")


def format_line(day: date, direction: str, channel: str, touch_type: str, summary: str,
                greene: str = "", requested: bool = False) -> str:
    """The exact v2 line `record_touch` appends (marker not included — the
    caller adds it via `relationships.append_marked`)."""
    channel = (channel or "").lower().strip()
    summary = summary.replace("\n", " ").replace(" · ", ", ").strip()[:160]
    line = f"- {day.isoformat()} · {direction} · {channel} · {touch_type} · {summary}"
    if greene:
        line += f" · greene:{greene}"
    if requested:
        line += " · requested"
    return line


def parse_log(raw_section: str) -> list[Touch]:
    """`## Interaction log` (or any text containing such lines) → the Touches
    in it. Never raises — a line that matches neither grammar (a Handshake
    heading, a blank line, an unrecognised promise note) is silently
    dropped, not fatal (P3)."""
    touches: list[Touch] = []
    for raw_line in (raw_section or "").splitlines():
        line = _TAIL.sub("", raw_line.rstrip())

        m = _V2.match(line)
        if m:
            day_str, direction, channel, touch_type, tail = m.groups()
            try:
                day = date.fromisoformat(day_str)
            except ValueError:
                continue
            parts = tail.split(" · ")
            greene, requested = "", False
            while parts:
                last = parts[-1]
                if last == "requested":
                    requested = True
                    parts.pop()
                    continue
                if _GREENE.match(last):
                    greene = last[len("greene:"):]
                    parts.pop()
                    continue
                break
            summary = " · ".join(parts)
            touches.append(Touch(day=day, direction=direction, channel=channel,
                                 touch_type=touch_type, summary=summary,
                                 greene=greene, requested=requested, legacy=False))
            continue

        m = _LEGACY.match(line)
        if m:
            day_str, channel, note = m.groups()
            if note.startswith("I promised") or note.startswith("They promised"):
                continue
            try:
                day = date.fromisoformat(day_str)
            except ValueError:
                continue
            touches.append(Touch(day=day, direction="out", channel=channel or "",
                                 touch_type="other", summary=note,
                                 greene="", requested=False, legacy=True))

    return touches


# ---- frontmatter helpers (touchlog owns its own tiny read/write — no import
# from proposals, to avoid a cycle since proposals imports this module) -----

def _split_front(text: str) -> tuple[str, str]:
    head, sep, body = text.partition("\n---\n")
    return head + sep, body


def _front_value(head: str, key: str) -> str:
    for line in head.splitlines():
        if line.startswith(f"{key}:"):
            return line.partition(":")[2].strip().strip("\"'")
    return ""


def _forward(text: str, key: str, value: str, today: str) -> str:
    """Set a `forward`-kind frontmatter field via `merge.apply_field`, never
    moving it backwards (SCHEMA §7 Forward-only)."""
    head, _ = _split_front(text)
    current = _front_value(head, key)
    new_fm, _ = merge.apply_field({key: current}, key, value, source="cockpit",
                                  origin="ai", today=today)
    if new_fm[key] != current:
        text = relationships._replace_field(text, key, new_fm[key])
    return text


def record_touch(text: str, person, *, day: date, direction: str, channel: str, touch_type: str,
                 summary: str, greene: str = "", requested: bool = False,
                 close_key: str = "") -> str:
    """Append one v2 touch to `## Interaction log` and move the dates it
    implies forward: `last_contact` always, `last_give` for a give type,
    `last_ask` for `ask`; a cold/dormant person is revived (R14). Then the
    quiet rule (A11.2): the second unprompted, non-floor-exempt out touch in
    a row sets `quiet_until` 2x the person's cadence out.

    `close_key`, when given, makes the touch line double as a promise close
    (SCHEMA §7 R11) — the marker is `bc:close:{close_key}` instead of the
    usual content hash, so `close_promise` can call this and have one line
    both log the touch and close the Next action."""
    valid_types = OUT_TYPES if direction == "out" else IN_TYPES
    if touch_type not in valid_types:
        raise ValueError(f"{touch_type!r} is not a valid {direction} touch type")
    if greene and not _GREENE_CODE.match(greene):
        raise ValueError(f"{greene!r} is not a valid Greene code")

    line = format_line(day, direction, channel, touch_type, summary,
                       greene=greene, requested=requested)
    marker = f"<!-- bc:close:{close_key} -->" if close_key else relationships._marker(person.id, "touch", line)
    if marker in text:
        return text   # already recorded — idempotent

    text = relationships.append_marked(text, "Interaction log", line, marker)

    today_str = day.isoformat()
    text = _forward(text, "last_contact", today_str, today_str)
    if touch_type in GIVE_TYPES:
        text = _forward(text, "last_give", today_str, today_str)
    if touch_type == "ask":
        text = _forward(text, "last_ask", today_str, today_str)

    head, _ = _split_front(text)
    if _front_value(head, "status") in ("cold", "dormant"):
        text = relationships._replace_field(text, "status", "active")

    if direction == "out" and touch_type not in FLOOR_EXEMPT and not requested:
        _, body = _split_front(text)
        log_section = relationships._raw_sections(body).get("Interaction log", "")
        relevant = [t for t in parse_log(log_section) if not t.legacy and t.touch_type not in PROMISE_IN]
        if len(relevant) >= 2:
            last_two = relevant[-2:]
            if all(t.direction == "out" and t.touch_type not in FLOOR_EXEMPT and not t.requested
                   for t in last_two):
                cadence = person.effective_cadence if person.has_cadence else QUIET_FALLBACK_DAYS
                quiet_until = day + timedelta(days=QUIET_MULTIPLIER * cadence)
                text = _forward(text, "quiet_until", quiet_until.isoformat(), today_str)

    return text


def _action_text(person, key: str) -> str:
    for action in relationships.next_actions(person):
        if action.key == key:
            return action.text
    raise ValueError(f"{key!r} is not a known action key")


def close_promise(text: str, person, *, key: str, result: str, side: str, day: date) -> str:
    """Close a promise from either side (SCHEMA §7 R11). `side="theirs"`
    just logs what happened — it is THEIR reliability, not ours, so it is
    never a touch. `side="mine"` kept/late IS a touch (`record_touch` with
    `close_key`); dropped only marks the Next action, since dropping a
    promise isn't something to log as if it were a good deed."""
    action_text = _action_text(person, key)
    marker = f"<!-- bc:close:{key} -->"

    if side == "theirs":
        if result not in ("kept", "late", "dropped"):
            raise ValueError(f"{result!r} is not a valid theirs promise result")
        line = format_line(day, "in", "", f"promise_{result}", action_text)
        return relationships.append_marked(text, "Interaction log", line, marker)

    if side == "mine":
        if result == "kept":
            return record_touch(text, person, day=day, direction="out", channel="",
                                touch_type="keep_promise", summary=action_text, close_key=key)
        if result == "late":
            return record_touch(text, person, day=day, direction="out", channel="",
                                touch_type="keep_promise_late", summary=action_text, close_key=key)
        if result == "dropped":
            line = f"- {day.isoformat()} · dropped: {action_text}"
            return relationships.append_marked(text, "Next action", line, marker)
        raise ValueError(f"{result!r} is not a valid mine promise result")

    raise ValueError(f"{side!r} is not a valid side")
