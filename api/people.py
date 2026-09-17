"""People API — the Relationship OS surfaces.

Reads and writes 07-People notes through pipeline/relationships.py (the schema
lives there, once). Three things this module is careful about:

1. **Nothing sends.** A draft comes back as text plus the person's raw channel
   values. The cockpit turns those into a chat or mail deep link in the
   BROWSER, and the human presses send. No message-delivery URL is ever
   constructed server-side (CLAUDE.md §4, enforced by api/tests/test_no_send.py).
2. **No generic-voice drafts.** Without _System/my-voice.md the draft endpoint
   refuses in plain English rather than writing something that doesn't sound
   like the owner.
3. **No invented facts.** The draft prompt is leashed to the interaction log;
   when the log is empty the draft is told to say less, honestly.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

from pipeline import draftlint, greene, ledger, llm, merge, relationships, touchlog
from pipeline import queue as queue_mod

from . import held as held_mod
from .notes import git_commit_vault

log = logging.getLogger("api")

VOICE_FILE = "_System/my-voice.md"
PDL_URL = "https://api.peopledatalabs.com/v5/person/enrich"
PDL_TIMEOUT = 10


# ---- reading -------------------------------------------------------------------

def summary(person: relationships.Person, today: date) -> dict:
    return {
        "id": person.id,
        "name": person.name,
        "dex_id": person.dex_id,
        "dex_deeplink": person.dex_deeplink,
        "relationship": person.relationship,   # kept as a string (P4) — legacy callers
        "company": person.company,
        "warmth_stage": person.warmth_stage,
        "status": person.status,
        "cadence_days": person.effective_cadence,
        "last_contact": person.last_contact.isoformat() if person.last_contact else None,
        "days_since_contact": person.days_since_contact(today),
        "going_cold": person.going_cold(today),
        "warmup_due": relationships.warmup_due(person, today),
        "commitment_due": relationships.commitment_due(person, today),
        "channels": person.channels,
        "next_action": person.next_action(),
        "sample": person.sample,
        "file": person.path.name,
        # v2.2 (SCHEMA-REFERENCE.md §7)
        "tier": person.tier,
        "relationships": person.relationships,
        "known_for": person.known_for,
        "status_computed": ledger.status_for(person, today),
        "quiet_until": person.quiet_until.isoformat() if person.quiet_until else None,
        "list_of_20": person.list_of_20,
    }


def list_people(vault_path: Path, today: date | None = None) -> list[dict]:
    today = today or date.today()
    people = relationships.load_people(vault_path)
    ranked = relationships.rank(people, today)
    return [summary(p, today) for p in ranked]


def _next_action_view(person: relationships.Person, action: relationships.NextAction,
                      today: date) -> str:
    """Which queue view (if any) this Next action line would surface in —
    the same classification `queue.build` uses, so the person page and the
    Today strip never disagree about what a line means. A closed action, or
    one that doesn't currently qualify for any view, is ""."""
    if action.closed:
        return ""
    matches = queue_mod._items_for_action(person, action, today)
    return matches[0][0] if matches else ""


def detail(vault_path: Path, person_id: str, today: date | None = None,
          desk: bool = False) -> dict | None:
    """Everything the person page needs to read — nothing here writes to the
    vault (Task 8: read endpoints only). `desk` gates `energy`, which is
    never shown on a screen someone else might glance at (SCHEMA §7 /
    Global Constraints: "energy never rendered as a word" outside the desk
    view)."""
    today = today or date.today()
    person = relationships.find_person(vault_path, person_id)
    if not person:
        return None

    touches = touchlog.parse_log(person.raw_sections.get("Interaction log", ""))
    reliab = ledger.reliability(touches)

    working_together = None
    if person.commercial:
        working_together = {
            "conversation_stage": person.conversation_stage,
            "buyer_role": person.buyer_role,
            "fit": person.fit,
            "no_economic_buyer": person.buyer_role not in ("", "economic"),
        }

    quiet = None
    if person.quiet_until and ledger.is_quiet(person, touches, today):
        quiet = {
            "until": person.quiet_until.isoformat(),
            "line": f"Quiet until {person.quiet_until.day} {person.quiet_until.strftime('%b')}: "
                    "two messages unanswered. Nothing to do.",
        }

    next_action_rows = [
        {"due": a.due.isoformat() if a.due else None, "text": a.text, "key": a.key,
         "closed": a.closed, "view": _next_action_view(person, a, today)}
        for a in relationships.next_actions(person)
    ]

    recent_touches = sorted(touches, key=lambda t: t.day, reverse=True)[:20]
    touch_rows = [
        {"day": t.day.isoformat(), "direction": t.direction, "channel": t.channel,
         "touch_type": t.touch_type, "summary": t.summary, "greene": t.greene,
         "requested": t.requested, "legacy": t.legacy}
        for t in recent_touches
    ]

    result = {
        **summary(person, today),
        "context": person.sections.get("Context", ""),
        "needs": person.sections.get("Needs", ""),
        "facts": person.sections.get("Facts", ""),
        "interpretations": person.sections.get("Interpretations", ""),
        "interaction_log": person.interaction_log(),
        "known_for": person.known_for,
        "recall_trigger": person.recall_trigger,
        "language": person.language,
        "preferred_channel": person.preferred_channel(),
        "commercial": person.commercial,
        "dates": person.dates,
        "working_together": working_together,
        "ledger": ledger.counts(touches, today),
        "reliability": reliab,
        "reliability_line": ledger.reliability_line(reliab),
        "flags": ledger.flags(touches, today),
        "inside_floor": ledger.inside_floor(person, touches, today),
        "quiet": quiet,
        "next_actions": next_action_rows,
        "touches": touch_rows,
        "current_state": person.sections.get("Current state", ""),
        "future_state": person.sections.get("Future state", ""),
        "can_help": person.sections.get("Can help with", ""),
        "how_they_communicate": person.sections.get("How they communicate", ""),
        "updates": person.sections.get("Updates", ""),
        "reads": greene.reads(person, touches),
        "presets": greene.presets("", person.relationships),
    }
    if desk:
        result["energy"] = person.energy
    return result


# ---- today strip / queues -------------------------------------------------------

def today_queue(vault_path: Path, today: date | None = None,
                now: datetime | None = None) -> dict:
    """The Today screen's people surfaces: the five-item strip plus the raw
    seven views behind it, and how full each tier is against its cap — held
    drafts come from api/held.py, so a ready one surfaces in owe_reply."""
    now = now or datetime.now()
    today = today or now.date()
    people = relationships.load_people(vault_path)
    held_items = held_mod.list_items(Path(vault_path), now)
    held = [queue_mod.Held(i.person_id, i.touch_type, i.ready) for i in held_items]
    queues = queue_mod.build(people, today, held=held)
    strip_items, overflow = queue_mod.strip(queues)
    tiers = {tier: {"count": sum(1 for p in people if p.tier == tier),
                    "cap": relationships.TIER_CAP[tier]}
             for tier in ("inner", "core", "active")}
    untiered = sum(1 for p in people if p.tier == "")
    return {
        "strip": [queue_mod.to_dict(i) for i in strip_items],
        "overflow": overflow,
        "queues": {view: [queue_mod.to_dict(i) for i in items] for view, items in queues.items()},
        "labels": queue_mod.LABELS,
        "tiers": tiers,
        "untiered": untiered,
    }


# ---- greene panel ----------------------------------------------------------------

def greene_situations(vault_path: Path) -> dict:
    """The 18 Greene situations from the vault's copy of greene-helper.md,
    seeding it from the repo's seed the first time it's missing (R28) and
    committing only that first write."""
    text, wrote = greene.ensure(Path(vault_path))
    if wrote:
        git_commit_vault(Path(vault_path), "api: seeded greene-helper.md")
    situations = greene.parse(text)
    return {"situations": [
        {"code": s.code, "title": s.title, "happening": s.happening,
         "trap": s.trap, "move": s.move, "line": s.line}
        for s in situations
    ]}


# ---- draft linter ----------------------------------------------------------------

def lint_draft(vault_path: Path, text: str, *, channel: str = "whatsapp",
               person_id: str = "", touch_type: str = "",
               today: date | None = None) -> dict:
    """Run the A9 linter with the person's own tier and quiet state when
    `person_id` resolves to a real note; an unknown or blank id lints with
    the neutral defaults (blank tier, not quiet)."""
    tier, quiet = "", False
    if person_id:
        person = relationships.find_person(vault_path, person_id)
        if person:
            today = today or date.today()
            touches = touchlog.parse_log(person.raw_sections.get("Interaction log", ""))
            tier = person.tier
            quiet = ledger.is_quiet(person, touches, today)
    return draftlint.lint(text, channel=channel, tier=tier, touch_type=touch_type, quiet=quiet)


# ---- my voice ------------------------------------------------------------------

def voice_path(vault_path: Path) -> Path:
    return Path(vault_path) / VOICE_FILE


def voice_status(vault_path: Path) -> dict:
    path = voice_path(vault_path)
    exists = path.is_file() and bool(path.read_text(encoding="utf-8").strip())
    return {"exists": exists, "file": VOICE_FILE,
            "samples": _count_samples(path.read_text(encoding="utf-8")) if exists else 0}


def _count_samples(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.startswith("### Sample "))


def write_voice(vault_path: Path, samples: list[str]) -> dict:
    """Write _System/my-voice.md from messages the owner actually sent.

    The samples are stored verbatim — they ARE the style guide. Nothing here is
    AI-written, so the file carries origin: human."""
    cleaned = [s.strip() for s in samples if s and s.strip()]
    if not cleaned:
        raise ValueError("no samples")
    path = voice_path(vault_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ["---", "origin: human", "---", "",
            "# My voice", "",
            "Real messages I have sent. Drafts are written to sound like these —",
            "same length, same greetings, same punctuation, same bluntness.", ""]
    for i, sample in enumerate(cleaned, start=1):
        body += [f"### Sample {i}", "", sample, ""]
    path.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
    git_commit_vault(Path(vault_path), "api: wrote my-voice.md from pasted samples")
    return voice_status(vault_path)


# ---- drafting ------------------------------------------------------------------

def build_draft_prompt(person: relationships.Person, voice: str, channel: str) -> str:
    log_text = person.interaction_log()
    context = person.sections.get("Context", "").strip()
    needs = person.sections.get("Needs", "").strip()

    leash = (
        "Write ONLY from what is written above. Do not invent meetings, "
        "projects, mutual friends, or anything they said. If the interaction "
        "log is empty or thin, write a shorter and more general message rather "
        "than inventing a shared history — a vague honest note is fine, a "
        "confident wrong one is not."
    )
    return (
        "Here are messages I have actually sent. Match this voice exactly — "
        f"length, greeting, punctuation, formality:\n\n{voice}\n\n"
        f"---\nI want to reconnect with {person.name}"
        + (f" ({person.relationship}" + (f" at {person.company}" if person.company else "") + ")"
           if person.relationship or person.company else "")
        + f", over {channel}.\n\n"
        + (f"What I know about them:\n{context}\n\n" if context else "")
        + (f"What they need:\n{needs}\n\n" if needs else "")
        + (f"Our history:\n{log_text}\n\n" if log_text
           else "Our history: nothing logged yet — we have not spoken since I made this note.\n\n")
        + f"{leash}\n\nReturn only the message text, no preamble, no subject line."
    )


def _log_attempts(events, person_id: str, attempts: list) -> None:
    """Same shape the watcher writes for classify/extract calls, so a draft's
    provider attempts show up in GET /api/providers instead of being invisible
    there (D6). `events` is optional and never allowed to fail the request."""
    if not events:
        return
    try:
        fkey = f"people-draft:{person_id}"
        for att in attempts:
            conf_note = f" confidence={att.confidence:.2f}" if att.confidence is not None else ""
            events.log(fkey, "llm", "ok" if att.outcome == "served" else "failed",
                       message=f"provider={att.provider} outcome={att.outcome}" + conf_note)
    except Exception:
        log.exception("failed to log draft LLM attempts")


def draft(vault_path: Path, person_id: str, channel: str | None,
          config, *, router=None, priority: list[str] | None = None, events=None) -> dict | None:
    """A reconnection message in the owner's voice. Returns None for unknown id.

    Raises LookupError when the voice file is missing — the caller turns that
    into a plain-English refusal rather than drafting in a generic voice.
    """
    person = relationships.find_person(vault_path, person_id)
    if not person:
        return None
    status = voice_status(vault_path)
    if not status["exists"]:
        raise LookupError(VOICE_FILE)

    chosen = channel or person.preferred_channel(priority) or "whatsapp"
    voice = voice_path(vault_path).read_text(encoding="utf-8")
    prompt = build_draft_prompt(person, voice, chosen)
    text, provider, attempts = (router or llm.complete_text)(prompt, config)
    _log_attempts(events, person_id, attempts)
    if not text:
        return {"text": "", "channel": chosen, "channels": person.channels,
                "provider": None, "attempts": [a.__dict__ for a in attempts]}
    return {
        "text": text.strip(),
        "channel": chosen,
        # raw values only — the link is built in the browser (CLAUDE.md §4)
        "channels": person.channels,
        "provider": provider,
        "attempts": [a.__dict__ for a in attempts],
    }


# ---- profile summaries (Pass D — what gets pushed to Dex / Google Contacts) -----

def build_summary_prompt(person: relationships.Person) -> str:
    """A factual third-person profile summary, not a message.

    Same context loader as the draft prompt, different leash: a draft may be
    warm and vague, a CRM summary must be true. A thin interaction log produces
    a SHORTER summary — inventing a plausible history is the one failure mode
    that would poison the owner's own CRM.
    """
    log_text = person.interaction_log()
    context = person.sections.get("Context", "").strip()
    needs = person.sections.get("Needs", "").strip()
    next_action = person.next_action()

    return (
        f"Write a short profile summary of {person.name} for my own contact "
        "manager. Three or four short lines, in this order, one per line:\n"
        "1. who they are;\n"
        "2. the last real interaction;\n"
        "3. what is currently open between us;\n"
        "4. the next step, if there is one.\n\n"
        "---\n"
        + (f"What I know about them:\n{context}\n\n" if context else "")
        + (f"What they need:\n{needs}\n\n" if needs else "")
        + (f"Our history:\n{log_text}\n\n" if log_text
           else "Our history: nothing logged yet.\n\n")
        + (f"My next action:\n{next_action}\n\n" if next_action else "")
        + (f"Relationship: {person.relationship}\n" if person.relationship else "")
        + (f"Company: {person.company}\n" if person.company else "")
        + "\nUse ONLY what is written above. Do not invent meetings, projects, "
          "mutual friends, dates, or anything they said. If a line has nothing "
          "true to say, leave that line out entirely — a two-line honest "
          "summary is correct, a four-line invented one is not. Write plain "
          "sentences in the third person. Return only the summary text.")


def profile_summary(vault_path: Path, person_id: str, config, *, router=None
                    ) -> tuple[relationships.Person, str] | None:
    """(person, summary text) — None for an unknown id.

    Raises RuntimeError when no provider in the chain could write it, which the
    caller turns into the plain-English 502."""
    person = relationships.find_person(vault_path, person_id)
    if not person:
        return None
    prompt = build_summary_prompt(person)
    text, _provider, _attempts = (router or llm.complete_text)(prompt, config)
    if not text or not text.strip():
        raise RuntimeError("no provider could write the summary")
    return person, text.strip()


# ---- writers -------------------------------------------------------------------

def _reread(vault_path: Path, person_id: str, fallback: relationships.Person
            ) -> relationships.Person:
    """The person as they are on disk after a write, or the pre-write object.

    find_person can legitimately answer None — a rename or a sync deleting the
    file between the write and the re-read — and passing that into summary()
    turned a successful write into a 500. The write already happened, so the
    honest answer is the object we have."""
    found = relationships.find_person(vault_path, person_id)
    if found is None:
        log.warning("person %s vanished between write and re-read", person_id)
        return fallback
    return found


def create_target(vault_path: Path, name: str, channel_kind: str, channel_value: str,
                  today: date | None = None) -> dict:
    """Quick-add a warm-up target: one name, one channel, stage `identified`.

    Raises ValueError for blank/unknown input — the caller turns that into the
    plain-English refusal."""
    today = today or date.today()
    person = relationships.create_person(Path(vault_path), name, channel_kind, channel_value)
    if person is None:
        raise ValueError("the note was written but could not be read back")
    git_commit_vault(Path(vault_path), f"api: added target {person.name}")
    return summary(person, today)


def log_contact(vault_path: Path, person_id: str, note: str, channel: str = "",
                direction: str = "out", touch_type: str = "", greene: str = "",
                requested: bool = False, today: date | None = None) -> dict | None:
    """Log one typed touch (SCHEMA §7 `## Interaction log` v2 grammar) via
    `touchlog.record_touch` — the single writer, so the quiet rule and the
    give/ask dates never drift out of sync with what the log says.

    Raises ValueError (blank/unknown touch_type, or a bad Greene code) — the
    caller turns that into the 422 refusal. Returns None for an unknown id."""
    today = today or date.today()
    person = relationships.find_person(vault_path, person_id)
    if not person:
        return None
    text = person.path.read_text(encoding="utf-8")
    summary_text = note.strip() or "Reached out."
    new_text = touchlog.record_touch(text, person, day=today, direction=direction,
                                     channel=channel, touch_type=touch_type,
                                     summary=summary_text, greene=greene, requested=requested)
    person.path.write_text(new_text, encoding="utf-8")
    git_commit_vault(Path(vault_path), f"api: logged contact with {person.name}")
    updated = _reread(vault_path, person_id, person)
    suggest_stage = relationships.next_stage(person.warmth_stage) if direction == "out" else None
    return {**summary(updated, today), "suggest_stage": suggest_stage}


def close_promise(vault_path: Path, person_id: str, key: str, result: str, side: str,
                  today: date | None = None) -> dict | None:
    """Close a promise from either side (SCHEMA §7 R11) via
    `touchlog.close_promise`. Raises ValueError for an unknown key or an
    invalid result/side — the caller turns that into the 404 refusal (the
    promise isn't open anymore, from the human's point of view). Returns
    None for an unknown person id."""
    today = today or date.today()
    person = relationships.find_person(vault_path, person_id)
    if not person:
        return None
    text = person.path.read_text(encoding="utf-8")
    new_text = touchlog.close_promise(text, person, key=key, result=result, side=side, day=today)
    person.path.write_text(new_text, encoding="utf-8")
    git_commit_vault(Path(vault_path), f"api: closed a promise with {person.name}")
    return detail(vault_path, person_id, today)


# ---- owner edits (SCHEMA §7 cross-app merge table) ------------------------------

class OwnerFieldError(ValueError):
    """Raised when `owner_edit` is asked to write a field that isn't
    owner-editable. Carries `field` so the caller can build user-facing
    copy without parsing the exception message."""
    def __init__(self, field: str):
        self.field = field
        super().__init__(f"{field!r} is not an owner-editable field")


class OwnerValueError(ValueError):
    """Raised when `owner_edit` is given a value that isn't valid for an
    otherwise-editable field. Carries `field` and `value` so the caller can
    build user-facing copy without parsing the exception message."""
    def __init__(self, field: str, value: str):
        self.field = field
        self.value = value
        super().__init__(f"{value!r} is not a valid {field}")


OWNER_WRITABLE = frozenset(relationships.OWNER_ONLY) | {"dates", "how_they_communicate"}
_ENERGY_VALUES = {"gives", "neutral", "drains", ""}
_FIT_VALUES = {"ideal", "good", "poor", "unknown", ""}
_BUYER_ROLE_VALUES = {"economic", "influencer", "user", "gatekeeper", "unknown", ""}
_CONVERSATION_STAGE_VALUES = {"none", "probative", "qualifying", "value", "closing",
                              "delivering", "past", ""}
_BOOL_VALUES = {"true", "false"}
_MM_DD = re.compile(r"^\d{2}-\d{2}$")
_YYYY_MM_DD = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_owner_value(field: str, value: str) -> None:
    checks = {
        "tier": set(relationships.TIERS) | {""},
        "energy": _ENERGY_VALUES,
        "fit": _FIT_VALUES,
        "buyer_role": _BUYER_ROLE_VALUES,
        "conversation_stage": _CONVERSATION_STAGE_VALUES,
        "list_of_20": _BOOL_VALUES,
    }
    allowed = checks.get(field)
    if allowed is not None and value not in allowed:
        raise OwnerValueError(field, value)


def _validate_date_value(field: str, value: str) -> None:
    if value and not (_MM_DD.match(value) or _YYYY_MM_DD.match(value)):
        raise OwnerValueError(field, value)


def _format_dates(raw_value: str) -> str:
    """`{"birthday": "03-14", "anniversary": ""}` (JSON) → the inline map
    the note stores, always carrying both keys."""
    try:
        parsed = json.loads(raw_value)
    except (json.JSONDecodeError, TypeError) as e:
        raise OwnerValueError("dates", raw_value) from e
    if not isinstance(parsed, dict):
        raise OwnerValueError("dates", raw_value)
    birthday = (parsed.get("birthday") or "").strip()
    anniversary = (parsed.get("anniversary") or "").strip()
    _validate_date_value("dates", birthday)
    _validate_date_value("dates", anniversary)
    return f"{{birthday: {birthday}, anniversary: {anniversary}}}"


def _raw_frontmatter_field(text: str, key: str) -> str:
    head, _, _ = text.partition("\n---\n")
    for line in head.splitlines():
        if line.startswith(f"{key}:"):
            return line.partition(":")[2].strip()
    return ""


def owner_edit(vault_path: Path, person_id: str, field: str, value: str,
              today: date | None = None) -> dict | None:
    """A human setting an owner-only field directly (SCHEMA §7): tier,
    energy, known_for, recall_trigger, conversation_stage, buyer_role, fit,
    list_of_20, plus the two body-section fields `dates` and
    `how_they_communicate` that ride the same endpoint.

    Raises ValueError for an unknown field or an out-of-vocabulary value —
    the caller turns that into the 422 refusal. Returns None for an unknown
    person id. The response also carries a `warning` when this edit puts a
    tier over its cap or List of 20 over 20 — never blocking the edit, just
    naming the trade-off back to the owner."""
    if field not in OWNER_WRITABLE:
        raise OwnerFieldError(field)
    today = today or date.today()
    person = relationships.find_person(vault_path, person_id)
    if not person:
        return None

    text = person.path.read_text(encoding="utf-8")
    today_str = today.isoformat()

    if field == "how_they_communicate":
        line = f"- {today_str} · {value}"
        marker = relationships._marker(person.id, "how", line)
        text = relationships.append_marked(text, "How they communicate", line, marker)
    else:
        new_value = _format_dates(value) if field == "dates" else value
        if field != "dates":
            _validate_owner_value(field, value)
        current = _raw_frontmatter_field(text, field)
        fm, history_line = merge.owner_change({field: current}, field, new_value, today=today_str)
        text = relationships._replace_field(text, field, fm[field])
        marker = relationships._marker(person.id, "owner", field, new_value, today_str)
        text = relationships.append_marked(text, "Updates", history_line, marker)

    person.path.write_text(text, encoding="utf-8")
    git_commit_vault(Path(vault_path), f"api: {person.name} → {field}")

    warning = None
    if field == "tier" and value in relationships.TIER_CAP:
        people_now = relationships.load_people(vault_path)
        n = sum(1 for p in people_now if p.tier == value)
        cap = relationships.TIER_CAP[value]
        if n > cap:
            warning = f"{value} is {n} of {cap}. Who moves down a tier?"
    if field == "list_of_20" and value == "true":
        people_now = relationships.load_people(vault_path)
        n = sum(1 for p in people_now if p.list_of_20)
        if n > 20:
            warning = f"List of 20 has {n}. Who comes off?"

    result = detail(vault_path, person_id, today)
    return {**result, "warning": warning}


def set_stage(vault_path: Path, person_id: str, stage: str,
              today: date | None = None) -> dict | None:
    today = today or date.today()
    person = relationships.find_person(vault_path, person_id)
    if not person:
        return None
    person.path.write_text(relationships.set_warmth_stage(person, stage), encoding="utf-8")
    git_commit_vault(Path(vault_path), f"api: {person.name} → warmth {stage}")
    return summary(_reread(vault_path, person_id, person), today)


# ---- enrichment (People Data Labs) ----------------------------------------------

def pdl_configured() -> bool:
    return bool(os.environ.get("PDL_API_KEY"))


def _pdl_lookup(person: relationships.Person, fetch=None) -> tuple[dict, int | None]:
    params = []
    if person.channels.get("email"):
        params.append(("email", person.channels["email"]))
    elif person.channels.get("linkedin"):
        params.append(("profile", person.channels["linkedin"]))
    else:
        params.append(("name", person.name))
        if person.company:
            params.append(("company", person.company))
    query = urllib.parse.urlencode(params)
    url = f"{PDL_URL}?{query}"
    if fetch:
        return fetch(url)
    req = urllib.request.Request(url, headers={"X-Api-Key": os.environ["PDL_API_KEY"]})
    with urllib.request.urlopen(req, timeout=PDL_TIMEOUT) as resp:
        remaining = resp.headers.get("X-Rate-Limit-Remaining-Month")
        return json.loads(resp.read()), (int(remaining) if remaining else None)


def enrich(vault_path: Path, person_id: str, *, fetch=None,
           today: date | None = None) -> dict | None:
    """Append company/role facts under ## Context, flagged as AI-written."""
    today = today or date.today()
    person = relationships.find_person(vault_path, person_id)
    if not person:
        return None
    payload, remaining = _pdl_lookup(person, fetch)
    data = payload.get("data") or {}
    facts = []
    if data.get("job_title"):
        facts.append(data["job_title"])
    if data.get("job_company_name"):
        facts.append(f"at {data['job_company_name']}")
    if data.get("location_name"):
        facts.append(f"({data['location_name']})")
    if not facts:
        return {**summary(person, today), "enriched": False,
                "credits_remaining": remaining,
                "detail": "People Data Labs had no match for this person."}
    line = f"- {' '.join(facts)} <!-- origin: ai · people-data-labs -->"
    person.path.write_text(relationships.append_context(person, [line]), encoding="utf-8")
    git_commit_vault(Path(vault_path), f"api: enriched {person.name} (origin: ai)")
    return {**summary(_reread(vault_path, person_id, person), today),
            "enriched": True, "credits_remaining": remaining,
            "detail": " ".join(facts)}
