"""Google Contacts write-back (People API) — the phone half of Pass D.

Why this exists: the iPhone reads the Google account's contacts natively, so
updating a Google contact's notes field is how a vault summary reaches the
phone without iCloud and without CardDAV.

Three hard rules this module keeps:

1. **Update-only.** It never creates a contact. If no existing contact matches
   the person's email or phone, that is a plain-English 404 — the address book
   is never populated from the vault.
2. **Append-only within the field.** It reuses pipeline/dex.py's marker merge,
   so anything the owner typed into the contact's notes survives verbatim; only
   the BRAIN-OS block is replaced.
3. **Profile data, never a message.** This module writes a biography field. It
   has no send path, and api/tests/test_no_send.py fails the build if one
   appears here.

Auth rides the existing api/google.py OAuth flow (same client, same refresh
token). Pass D added the contacts scope to that flow, so an account linked
earlier needs one re-consent — which this module reports rather than crashing.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import date

from pipeline import dex

from . import google

log = logging.getLogger("api")

PEOPLE_BASE = "https://people.googleapis.com/v1"
READ_MASK = "names,emailAddresses,phoneNumbers,biographies,metadata"
SEARCH_FIELDS = "names,emailAddresses,phoneNumbers,biographies"


def _reconsent_error() -> google.GoogleError:
    return google.GoogleError(
        409, "Google Contacts isn't connected yet.",
        "This cockpit's Google link was made before it could update contacts, "
        "so Google hasn't granted the contacts permission.",
        "Open Integrations, press Disconnect, then Connect Google again — it's "
        "a one-time re-consent and Gmail/Calendar keep working meanwhile.")


def require_scope(config) -> None:
    if not google.has_contacts_scope(config):
        raise _reconsent_error()


def configured(config) -> bool:
    return google.configured() and google.connected(config) and google.has_contacts_scope(config)


# ---- finding the contact ---------------------------------------------------------

def _search(token: str, query: str, *, fetch=None) -> list[dict]:
    url = f"{PEOPLE_BASE}/people:searchContacts?" + urllib.parse.urlencode(
        {"query": query, "readMask": SEARCH_FIELDS, "pageSize": 10})
    if fetch is not None:
        payload = fetch("GET", url, None)
    else:
        payload = google._get(token, url)
    return [r.get("person") or {} for r in (payload.get("results") or [])]


def _warm_up(token: str, *, fetch=None) -> None:
    """The People API's documented quirk: searchContacts needs one warm-up
    request before it will return results for a fresh access token. Failing to
    warm up is not an error — the real search below is what matters."""
    try:
        _search(token, "", fetch=fetch)
    except Exception:
        log.info("contacts search warm-up failed (continuing)")


def _matches(person_value: str, candidates: list[str]) -> bool:
    """Emails compare case-insensitively; phone numbers compare on digits only,
    so "+971 50 000 0000" and "+971500000000" are the same person."""
    wanted = person_value.strip().lower()
    wanted_digits = "".join(c for c in wanted if c.isdigit())
    for candidate in candidates:
        value = (candidate or "").strip().lower()
        if not value:
            continue
        if value == wanted:
            return True
        if wanted_digits and len(wanted_digits) >= 7:
            digits = "".join(c for c in value if c.isdigit())
            # compare the last 9 digits: country-code prefixes vary by how the
            # number was saved, the subscriber number does not
            if digits and digits[-9:] == wanted_digits[-9:]:
                return True
    return False


def find_contact(config, token_cache: dict, person, *, fetch=None) -> dict | None:
    """The Google contact matching this person's email or phone — None if the
    address book has nobody matching. Never creates anything."""
    require_scope(config)
    token = "" if fetch is not None else google.access_token(config, token_cache)
    _warm_up(token, fetch=fetch)

    email = person.channels.get("email", "")
    phone = person.channels.get("whatsapp", "") or person.channels.get("phone", "")

    for query, field, getter in (
        (email, "email", lambda c: [e.get("value", "") for e in c.get("emailAddresses") or []]),
        (phone, "phone", lambda c: [p.get("value", "") for p in c.get("phoneNumbers") or []]),
    ):
        if not query:
            continue
        for candidate in _search(token, query, fetch=fetch):
            if _matches(query, getter(candidate)):
                log.info("matched google contact by %s", field)
                return candidate
    return None


def contact_name(contact: dict) -> str:
    names = contact.get("names") or []
    return (names[0].get("displayName") if names else "") or "(unnamed contact)"


def biography(contact: dict) -> str:
    bios = contact.get("biographies") or []
    return str(bios[0].get("value") or "") if bios else ""


# ---- the write -------------------------------------------------------------------

def push_biography(config, token_cache: dict, person, summary: str, *,
                   today: date | None = None, dry_run: bool = False,
                   fetch=None) -> dict:
    """Merge `summary` into the matched contact's notes field.

    dry_run returns exactly what WOULD be written (what the preview shows).
    A person with no matching contact raises rather than creating one.
    """
    today = today or date.today()
    contact = find_contact(config, token_cache, person, fetch=fetch)
    if contact is None:
        raise google.GoogleError(
            404, "No matching contact in your Google account.",
            f"Nothing in the address book matches {person.name}'s email or phone, "
            "and this app never creates contacts.",
            "Add them to Google Contacts once (or correct the channels on their "
            "note), then push again.")

    existing = biography(contact)
    merged = dex.merge(existing, summary, today)
    resource = contact.get("resourceName", "")
    etag = (contact.get("etag")
            or (contact.get("metadata") or {}).get("sources", [{}])[0].get("etag", ""))
    payload = {"etag": etag, "biographies": [{"value": merged, "contentType": "TEXT_PLAIN"}]}
    result = {
        "resource_name": resource,
        "contact_name": contact_name(contact),
        "payload": payload,
        "replaced": dex.owned_section(existing),
        "dry_run": dry_run,
    }
    if dry_run:
        return result

    url = (f"{PEOPLE_BASE}/{resource}:updateContact?"
           + urllib.parse.urlencode({"updatePersonFields": "biographies"}))
    if fetch is not None:
        fetch("PATCH", url, payload)
    else:
        token = google.access_token(config, token_cache)
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            method="PATCH")
        google._call(req)
    result["pushed"] = True
    return result
