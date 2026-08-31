"""Pass D — pushing a person's profile summary OUT to Dex and Google Contacts.

The shape of this feature is set by two constitution rules:

- **§4, nothing sends.** These pushes write PROFILE DATA into the owner's own
  CRM and address book. Nothing is delivered to another human, and no messaging
  URL is ever built. api/tests/test_no_send.py pins that structurally for both
  target modules.
- **§3, never bulk-write unreviewed content.** So there is no auto-push, ever.
  The "batch" half of this module only *stages*: `queue()` computes who has
  moved on since their last push and the morning digest mentions the count.
  Every external write still goes through preview → human confirm, one person
  at a time — the confirm tap IS the review gate.

The preview and the push share one code path (`preview()` is the dry run of
`push()`), so what the human approves is the payload that gets sent, not an
approximation of it.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date
from pathlib import Path

from pipeline import dex, relationships

from . import google, google_contacts, people as people_mod

log = logging.getLogger("api")

TARGETS = ("dex", "contacts")
PUSH_STAGE = "push"


class PushError(Exception):
    """A push failure with the three plain-English parts (CLAUDE.md §5)."""

    def __init__(self, status: int, what: str, cause: str, todo: str):
        self.status = status
        self.envelope = {"what": what, "cause": cause, "todo": todo}
        super().__init__(what)


def _unknown_target(target: str) -> PushError:
    return PushError(
        400, "That's not somewhere the cockpit can push.",
        f"'{target}' isn't one of dex, contacts.",
        "Use one of the two push buttons on the person's card.")


# ---- what is configured ----------------------------------------------------------

def availability(config) -> dict:
    """Presence booleans for the buttons — never key values (CLAUDE.md §7)."""
    return {"dex": dex.configured(),
            "contacts_scope": google_contacts.configured(config)}


def _require_target(config, target: str) -> None:
    """Refuse early and in plain English when a target isn't set up. Called
    before any summary is generated so an unconfigured push costs nothing."""
    if target == "dex":
        if not dex.configured():
            raise PushError(
                503, "Dex isn't set up on this server yet.",
                "DEX_API_KEY isn't in the server's environment, so there's nothing "
                "to push with.",
                "Add your Dex API key to the server's environment and restart the "
                "API — everything else on the People screen keeps working without it.")
        return
    if not google.configured():
        raise PushError(
            503, "Google isn't set up on this server yet.",
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET aren't in the server's environment.",
            "Follow GO-LIVE.md §6 to create the OAuth client and put both values in .env.")
    if not google.connected(config):
        raise PushError(
            409, "Google isn't connected yet.",
            "No Google account has been linked to this cockpit.",
            "Open Integrations and press Connect Google.")
    try:
        google_contacts.require_scope(config)
    except google.GoogleError as e:
        raise PushError(e.status, **e.envelope)


# ---- preview + push --------------------------------------------------------------

def _summary_for(vault_path: Path, person_id: str, config, router=None):
    try:
        found = people_mod.profile_summary(vault_path, person_id, config, router=router)
    except RuntimeError:
        raise PushError(
            502, "No model could write the summary.",
            "Every provider in the chain failed or has no key set.",
            "Check the model keys in the server's shell, then try again.")
    if found is None:
        raise PushError(
            404, "That person isn't in the vault.",
            f"No note in 07-People has the id {person_id}.",
            "Refresh the People screen.")
    return found


def preview(vault_path: Path, person_id: str, target: str, config, token_cache: dict,
            *, router=None, today: date | None = None, fetch=None) -> dict:
    """Generate the summary and the exact payload, WITHOUT writing anything.

    This is what the human reads before confirming. It really is a dry run of
    the write below — same summary text, same merge, same payload."""
    today = today or date.today()
    if target not in TARGETS:
        raise _unknown_target(target)
    # Check the target is set up BEFORE spending a model call on a summary that
    # has nowhere to go — "Dex isn't configured" is the honest answer, and a
    # 502 from the model chain would have buried it.
    _require_target(config, target)
    person, text = _summary_for(vault_path, person_id, config, router)
    block = dex.block(text, today)

    if target == "dex":
        try:
            result = dex.push_description(person, text, today=today, dry_run=True, fetch=fetch)
        except dex.DexError as e:
            raise PushError(e.status, **e.envelope)
        return {"target": "dex", "person_id": person.id, "name": person.name,
                "summary": text, "block": block,
                "destination": f"Dex contact {person.dex_id} · description",
                "replaced": result["replaced"], "payload": result["payload"]}

    try:
        result = google_contacts.push_biography(
            config, token_cache, person, text, today=today, dry_run=True, fetch=fetch)
    except google.GoogleError as e:
        raise PushError(e.status, **e.envelope)
    return {"target": "contacts", "person_id": person.id, "name": person.name,
            "summary": text, "block": block,
            "destination": f"Google contact “{result['contact_name']}” · notes",
            "replaced": result["replaced"], "payload": result["payload"]}


def push(vault_path: Path, person_id: str, target: str, text: str, config,
         token_cache: dict, *, today: date | None = None, fetch=None,
         event_log=None) -> dict:
    """Write the CONFIRMED text out. `text` is what the human approved in the
    preview — this function never regenerates it, so what was shown is what is
    sent."""
    today = today or date.today()
    if target not in TARGETS:
        raise _unknown_target(target)
    _require_target(config, target)
    if not (text or "").strip():
        raise PushError(
            400, "There was nothing to push.",
            "The confirmed summary text arrived empty.",
            "Press the push button again to regenerate the preview.")
    person = relationships.find_person(vault_path, person_id)
    if not person:
        raise PushError(
            404, "That person isn't in the vault.",
            f"No note in 07-People has the id {person_id}.",
            "Refresh the People screen.")

    if target == "dex":
        try:
            result = dex.push_description(person, text, today=today, fetch=fetch)
        except dex.DexError as e:
            raise PushError(e.status, **e.envelope)
        changed = f"Dex contact {person.dex_id} · description"
    else:
        try:
            result = google_contacts.push_biography(
                config, token_cache, person, text, today=today, fetch=fetch)
        except google.GoogleError as e:
            raise PushError(e.status, **e.envelope)
        changed = f"Google contact “{result['contact_name']}” · notes"

    # The vault is untouched by a push (nothing was learned about the person),
    # so the durable record is one pipeline event — exactly what SQLite is for.
    if event_log is not None:
        event_log.log(str(person.path), PUSH_STAGE, "ok",
                      message=f"target={target} person={person.id}")
    return {"ok": True, "target": target, "changed": changed,
            "replaced": bool(result.get("replaced"))}


# ---- the review queue (the staged half of the nightly batch) ---------------------

def _last_pushes(db_path: Path) -> dict[tuple[str, str], str]:
    """{(person_id, target): timestamp} for the newest successful push each."""
    if not Path(db_path).exists():
        return {}
    out: dict[tuple[str, str], str] = {}
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
    for timestamp, message in rows:
        fields = dict(p.split("=", 1) for p in (message or "").split() if "=" in p)
        key = (fields.get("person", ""), fields.get("target", ""))
        if key[0] and key[1] and key not in out:
            out[key] = timestamp        # rows are newest-first
    return out


def _moved_on_since(person: relationships.Person, pushed_at: str | None) -> bool:
    """Has anything happened with this person since we last pushed them?

    "Anything" is deliberately cheap and honest: their last_contact date. A
    person never pushed is always staged; a person whose last contact predates
    the push has nothing new worth sending."""
    if pushed_at is None:
        return True
    if not person.last_contact:
        return False
    return person.last_contact.isoformat() >= pushed_at[:10]


def queue(vault_path: Path, db_path: Path, config, today: date | None = None) -> list[dict]:
    """Who is ready to be pushed, and where. Generates NO summaries — this is
    polled by the People screen and read by the morning digest, so it must stay
    cheap and must never call a model or an external API."""
    today = today or date.today()
    pushed = _last_pushes(db_path)
    available = availability(config)
    items = []
    for person in relationships.load_people(vault_path):
        if person.status == "dormant" or person.sample:
            continue
        targets = []
        if available["dex"] and person.dex_id:
            targets.append("dex")
        if available["contacts_scope"] and (
                person.channels.get("email") or person.channels.get("whatsapp")):
            targets.append("contacts")
        due = [t for t in targets if _moved_on_since(person, pushed.get((person.id, t)))]
        if not due:
            continue
        last = [pushed.get((person.id, t)) for t in targets if pushed.get((person.id, t))]
        items.append({**people_mod.summary(person, today),
                      "targets": due,
                      "last_pushed": max(last) if last else None})
    return items
