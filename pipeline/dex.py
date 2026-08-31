"""Dex write-back — pushing a profile summary into the owner's own CRM.

What this module is, and is not:

- It writes PROFILE DATA (a contact's description field) into Dex, the user's
  own contact manager. It delivers nothing to another person and builds no
  messaging URL — CLAUDE.md §4 is untouched, and api/tests/test_no_send.py
  pins that structurally.
- It is APPEND-ONLY with respect to anything a human wrote. The text between
  the BRAIN-OS markers is owned by this app and replaced wholesale on each
  push; every byte outside those markers is preserved exactly. `merge()` is a
  pure function so that guarantee is testable without a network.
- It writes nothing to the vault. The vault is the source of truth (CLAUDE.md
  §1); this pushes a derived summary OUT, and never reads Dex back into a note.

THE ON-MACHINE FIX: Dex's REST API isn't publicly documented, so the exact base
URL, auth header, and contact payload shape are assumptions until the owner
runs this against a real key. Every one of those assumptions lives in the two
blocks marked API-SHAPE ASSUMPTION below — fixing the real shape should be two
edits in this file and nothing anywhere else.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import date

log = logging.getLogger("api")

TIMEOUT = 10

# The app owns what is between these two lines and nothing else. They are HTML
# comments so they stay invisible in any client that renders the field.
MARKER_OPEN = "<!-- BRAIN-OS -->"
MARKER_CLOSE = "<!-- /BRAIN-OS -->"


class DexError(Exception):
    """A Dex failure carrying the three plain-English parts (CLAUDE.md §5)."""

    def __init__(self, status: int, what: str, cause: str, todo: str):
        self.status = status
        self.envelope = {"what": what, "cause": cause, "todo": todo}
        super().__init__(what)


# ---- configuration ---------------------------------------------------------------

def api_key() -> str:
    """Env only, never config.json (CLAUDE.md §7)."""
    return os.environ.get("DEX_API_KEY", "")


def configured() -> bool:
    return bool(api_key())


def _require_key() -> str:
    key = api_key()
    if not key:
        raise DexError(
            503, "Dex isn't set up on this server yet.",
            "DEX_API_KEY isn't in the server's environment, so there's nothing to push with.",
            "Add your Dex API key to the server's environment and restart the API — "
            "everything else on the People screen keeps working without it.")
    return key


# ---- the marker merge (the append-only guarantee) --------------------------------

def block(summary: str, today: date) -> str:
    """The marker-delimited block this app owns, stamped so a human reading the
    field in Dex knows what wrote it and when."""
    body = summary.strip()
    return (f"{MARKER_OPEN}\n{body}\n"
            f"· via Brain OS {today.isoformat()}\n{MARKER_CLOSE}")


def merge(existing: str, summary: str, today: date) -> str:
    """Existing description + our summary → the new description.

    The whole point of this function: text a human typed into Dex is never
    touched. If our block is already there it is replaced in place; if it
    isn't, it is appended after everything else. Pure — no network, no clock
    (the date is passed in), so the guarantee is testable.
    """
    new_block = block(summary, today)
    current = existing or ""
    start = current.find(MARKER_OPEN)
    if start == -1:
        head = current.rstrip()
        return f"{head}\n\n{new_block}" if head else new_block
    end = current.find(MARKER_CLOSE, start)
    if end == -1:
        # An opening marker with no close: treat the rest of the field as ours
        # rather than guessing where it should have ended.
        return current[:start] + new_block
    return current[:start] + new_block + current[end + len(MARKER_CLOSE):]


def owned_section(existing: str) -> str:
    """What this app currently owns in the field — "" when it owns nothing.
    Used by the preview so the human sees exactly what is being replaced."""
    start = (existing or "").find(MARKER_OPEN)
    if start == -1:
        return ""
    end = existing.find(MARKER_CLOSE, start)
    if end == -1:
        return existing[start:]
    return existing[start:end + len(MARKER_CLOSE)]


# ---- the API calls ---------------------------------------------------------------
#
# -- API-SHAPE ASSUMPTION 1: base URL + auth header ------------------------------
# Dex's REST surface sits behind Hasura and takes the API key as a custom
# header rather than a bearer token. If a real key comes back 401, THIS is the
# block to correct: the base URL and the one header dict.
DEX_BASE = "https://api.getdex.com/api/rest"


def _headers(key: str) -> dict[str, str]:
    return {"x-hasura-dex-api-key": key, "Content-Type": "application/json"}
# -- end API-SHAPE ASSUMPTION 1 ----------------------------------------------------


# -- API-SHAPE ASSUMPTION 2: contact read/write endpoints + payload shape ---------
# Read one contact, and update its description field. If the real API nests the
# contact differently, or names the notes field something other than
# "description", THIS is the block to correct.
def _read_url(dex_id: str) -> str:
    return f"{DEX_BASE}/contacts/{dex_id}"


def _update_request(dex_id: str, description: str, key: str) -> urllib.request.Request:
    payload = {"contact": {"description": description}}
    req = urllib.request.Request(
        f"{DEX_BASE}/contacts/{dex_id}",
        data=json.dumps(payload).encode(), headers=_headers(key), method="PUT")
    return req


def _description_of(contact_payload: dict) -> str:
    contact = contact_payload.get("contact")
    if isinstance(contact, list):           # some Hasura shapes return a list
        contact = contact[0] if contact else {}
    if not isinstance(contact, dict):
        contact = contact_payload
    return str(contact.get("description") or "")
# -- end API-SHAPE ASSUMPTION 2 ----------------------------------------------------


def _call(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        log.info("dex api call failed: %s %s", e.code, detail)
        if e.code in (401, 403):
            raise DexError(
                401, "Dex turned the request away.",
                "The Dex API key was rejected — it may have been rotated or revoked.",
                "Check DEX_API_KEY in the server's environment, then try again.")
        if e.code == 404:
            raise DexError(
                404, "That contact isn't in Dex.",
                "Dex has no contact with the id saved on this person's note.",
                "Open the person in Dex, copy its contact id into the note's "
                "dex_id field, then push again.")
        raise DexError(
            502, "Dex returned an error.",
            "The Dex API call didn't succeed — it may be a passing hiccup, or the "
            "API shape may have changed.",
            "Try again in a moment; if it keeps failing, check the two "
            "API-SHAPE ASSUMPTION blocks in pipeline/dex.py.")
    except DexError:
        raise
    except Exception as e:
        log.info("dex api call failed: %s", e)
        raise DexError(
            502, "Dex couldn't be reached.",
            "The network request to Dex failed.",
            "Check the server's internet connection and try again.")


def fetch_description(dex_id: str, *, fetch=None) -> str:
    """The contact's current description — what we merge into."""
    key = _require_key()
    if fetch is not None:
        return _description_of(fetch("GET", _read_url(dex_id), None))
    req = urllib.request.Request(_read_url(dex_id), headers=_headers(key))
    return _description_of(_call(req))


def push_description(person, summary: str, *, today: date | None = None,
                     dry_run: bool = False, fetch=None) -> dict:
    """Merge `summary` into the person's Dex description and write it back.

    `dry_run=True` reads the current description and returns exactly what WOULD
    be sent, without writing — this is what the cockpit's preview shows, so the
    human confirms the real payload rather than an approximation.

    `fetch(method, url, payload) -> dict` replaces the network in tests.
    """
    today = today or date.today()
    key = _require_key()
    if not getattr(person, "dex_id", ""):
        raise DexError(
            409, "This person isn't linked to a Dex contact yet.",
            "Their note in 07-People has no dex_id, so there's nothing to update.",
            "Open them in Dex, copy the contact id into the note's dex_id field, "
            "then push again.")

    existing = fetch_description(person.dex_id, fetch=fetch)
    merged = merge(existing, summary, today)
    payload = {"contact": {"description": merged}}
    result = {
        "dex_id": person.dex_id,
        "payload": payload,
        "replaced": owned_section(existing),
        "preserved": bool((existing or "").strip()) and owned_section(existing) != existing.strip(),
        "dry_run": dry_run,
    }
    if dry_run:
        return result
    if fetch is not None:
        fetch("PUT", f"{DEX_BASE}/contacts/{person.dex_id}", payload)
    else:
        _call(_update_request(person.dex_id, merged, key))
    result["pushed"] = True
    return result
