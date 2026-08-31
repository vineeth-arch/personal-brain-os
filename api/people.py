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
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from pipeline import llm, relationships

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
        "relationship": person.relationship,
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
    }


def list_people(vault_path: Path, today: date | None = None) -> list[dict]:
    today = today or date.today()
    people = relationships.load_people(vault_path)
    ranked = relationships.rank(people, today)
    return [summary(p, today) for p in ranked]


def detail(vault_path: Path, person_id: str, today: date | None = None) -> dict | None:
    today = today or date.today()
    person = relationships.find_person(vault_path, person_id)
    if not person:
        return None
    return {**summary(person, today),
            "context": person.sections.get("Context", ""),
            "needs": person.sections.get("Needs", ""),
            "interaction_log": person.interaction_log()}


# ---- my voice ------------------------------------------------------------------

def voice_path(vault_path: Path) -> Path:
    return Path(vault_path) / VOICE_FILE


def voice_status(vault_path: Path) -> dict:
    path = voice_path(vault_path)
    exists = path.is_file() and bool(path.read_text().strip())
    return {"exists": exists, "file": VOICE_FILE,
            "samples": _count_samples(path.read_text()) if exists else 0}


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
    path.write_text("\n".join(body).rstrip() + "\n")
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


def draft(vault_path: Path, person_id: str, channel: str | None,
          config, *, router=None, priority: list[str] | None = None) -> dict | None:
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
    voice = voice_path(vault_path).read_text()
    prompt = build_draft_prompt(person, voice, chosen)
    text, provider, attempts = (router or llm.complete_text)(prompt, config)
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

def create_target(vault_path: Path, name: str, channel_kind: str, channel_value: str,
                  today: date | None = None) -> dict:
    """Quick-add a warm-up target: one name, one channel, stage `identified`.

    Raises ValueError for blank/unknown input — the caller turns that into the
    plain-English refusal."""
    today = today or date.today()
    person = relationships.create_person(Path(vault_path), name, channel_kind, channel_value)
    git_commit_vault(Path(vault_path), f"api: added target {person.name}")
    return summary(person, today)


def log_contact(vault_path: Path, person_id: str, note: str, channel: str = "",
                today: date | None = None) -> dict | None:
    today = today or date.today()
    person = relationships.find_person(vault_path, person_id)
    if not person:
        return None
    person.path.write_text(relationships.log_contact(person, note, today, channel=channel))
    git_commit_vault(Path(vault_path), f"api: logged contact with {person.name}")
    updated = relationships.find_person(vault_path, person_id)
    return {**summary(updated, today),
            "suggest_stage": relationships.next_stage(person.warmth_stage)}


def set_stage(vault_path: Path, person_id: str, stage: str,
              today: date | None = None) -> dict | None:
    today = today or date.today()
    person = relationships.find_person(vault_path, person_id)
    if not person:
        return None
    person.path.write_text(relationships.set_warmth_stage(person, stage))
    git_commit_vault(Path(vault_path), f"api: {person.name} → warmth {stage}")
    return summary(relationships.find_person(vault_path, person_id), today)


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
    person.path.write_text(relationships.append_context(person, [line]))
    git_commit_vault(Path(vault_path), f"api: enriched {person.name} (origin: ai)")
    return {**summary(relationships.find_person(vault_path, person_id), today),
            "enriched": True, "credits_remaining": remaining,
            "detail": " ".join(facts)}
