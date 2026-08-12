"""Dex → vault sync (pull-only).

Dex is the engagement engine; the vault is the record. Data moves one way:
Dex contacts become or update person notes in 07-People, and nothing is ever
written back to Dex. That direction is the whole point of the split — drop Dex
tomorrow and the markdown archive still stands on its own.

What a sync may touch on an existing note is deliberately narrow: `dex_id`,
`dex_deeplink`, `last_contact`, and one dated line appended to the interaction
log. Prose the owner wrote is never edited, never reformatted, never
reordered — an import that can rewrite your own words about a person isn't a
sync, it's a liability. New notes it creates carry `origin: ai` + `source: dex`
so the provenance firewall (SCHEMA §1) stays honest, and the vault is committed
*before* the batch lands so the whole import is one `git revert` away
(CLAUDE.md §3).

The key is env-only (`DEX_API_KEY`); config.json holds the base URL and the
deeplink prefix. Every HTTP detail sits behind `_fetch` and `_extract_page` so
the exact Dex response shape can be pinned down on the target machine without
touching the sync logic — and so the tests run with no network at all.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import notefm, people, relationships
from .errors import StageError

log = logging.getLogger("pipeline")

ENV_KEY = "DEX_API_KEY"
DEFAULT_BASE_URL = "https://api.getdex.com/api/rest"
DEFAULT_DEEPLINK_BASE = "https://getdex.com/contacts/"
TIMEOUT = 15
PAGE_LIMIT = 100
MAX_PAGES = 50          # a runaway-pagination backstop, not a real bound

# ponytail: Dex authenticates with a Hasura-style header. If your account's API
# docs name a different header or a Bearer token, this constant and _auth_header
# are the only two places to change.
_AUTH_HEADER = "x-hasura-dex-api-key"

SYNC_HOUR = 3           # nightly sync runs at/after 03:00 local time


@dataclass
class Contact:
    dex_id: str
    name: str
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    last_seen: str | None = None        # YYYY-MM-DD
    company: str = ""
    linkedin: str = ""

    def deeplink(self, base: str) -> str:
        return f"{base.rstrip('/')}/{self.dex_id}"


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0

    @property
    def changed(self) -> int:
        return self.created + self.updated

    def as_dict(self) -> dict:
        return {"created": self.created, "updated": self.updated,
                "unchanged": self.unchanged, "skipped": self.skipped}


# ---- HTTP ---------------------------------------------------------------------

def _fetch(url: str, headers: dict, timeout: int = TIMEOUT) -> bytes:
    """The single network call. Injected in tests; never called there."""
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _api_key() -> str:
    key = os.environ.get(ENV_KEY)
    if not key:
        raise StageError(
            "Couldn't sync your contacts from Dex.",
            f"{ENV_KEY} isn't set in the environment the server runs in, so Dex "
            "won't accept the request.",
            f"export {ENV_KEY}=... in that shell and restart the server, then sync again.")
    return key


def _get_json(url: str, key: str, fetch) -> dict:
    try:
        raw = fetch(url, {_AUTH_HEADER: key, "Accept": "application/json"}, TIMEOUT)
    except urllib.error.HTTPError as e:
        # 408/429/5xx fix themselves; 401/403 and other 4xx don't.
        if e.code in (408, 429) or e.code >= 500:
            raise StageError(
                "Couldn't sync your contacts from Dex.",
                "Dex answered with a server error or a rate limit — it's busy, "
                "not broken.",
                "Nothing to fix — try the sync again in a few minutes.",
                transient=True) from e
        if e.code in (401, 403):
            raise StageError(
                "Couldn't sync your contacts from Dex.",
                f"Dex rejected the API key — the {ENV_KEY} value may be wrong or revoked.",
                "Check the key in your Dex account settings, re-export it, and "
                "restart the server.") from e
        raise StageError(
            "Couldn't sync your contacts from Dex.",
            f"Dex refused the request (HTTP {e.code}) — the configured dex.base_url "
            "may not match your account's API.",
            "Check dex.base_url in config.json against the Dex API docs.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise StageError(
            "Couldn't sync your contacts from Dex.",
            "Dex couldn't be reached at all (this machine's network is down, or "
            "the connection timed out).",
            "Check this machine's internet connection, then sync again.",
            transient=True) from e
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as e:
        raise StageError(
            "Couldn't sync your contacts from Dex.",
            "Dex answered with something that isn't the contact list the app expects.",
            "Check dex.base_url in config.json — it should point at the Dex REST API.") from e


def _extract_page(data: dict) -> tuple[list[dict], int | None]:
    """(contacts, next_offset). The ONLY function that knows Dex's envelope.

    ponytail: Dex has shipped both a bare list and a couple of wrapper keys over
    time, so all of the shapes it's been seen to return are accepted rather than
    betting on one. Verify against your account's API on the target machine —
    if it needs a change, it needs it only here."""
    if isinstance(data, list):
        rows = data
    else:
        rows = None
        for key in ("contacts", "data", "results", "items"):
            value = data.get(key)
            if isinstance(value, list):
                rows = value
                break
            if isinstance(value, dict) and isinstance(value.get("contacts"), list):
                rows = value["contacts"]
                break
        if rows is None:
            rows = []
    next_offset = None
    if isinstance(data, dict):
        for key in ("next_offset", "nextOffset", "offset"):
            if isinstance(data.get(key), int):
                next_offset = data[key]
                break
    return rows, next_offset


def _first_str(row: dict, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _string_list(row: dict, *keys: str) -> list[str]:
    """Dex returns contact channels as either plain strings or [{email: ...}]."""
    out: list[str] = []
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, str) and entry.strip():
                    out.append(entry.strip())
                elif isinstance(entry, dict):
                    inner = _first_str(entry, "email", "phone_number", "phone", "value")
                    if inner:
                        out.append(inner)
    return list(dict.fromkeys(out))


def _normalize(row: dict) -> Contact | None:
    """A Dex row → Contact, or None when it can't be identified or named.

    Both are hard requirements: without an id there's nothing stable to match
    on across syncs, and without a name there's no note to write."""
    if not isinstance(row, dict):
        return None
    dex_id = _first_str(row, "id", "contact_id", "uuid")
    first = _first_str(row, "first_name", "firstName", "given_name")
    last = _first_str(row, "last_name", "lastName", "family_name")
    name = _first_str(row, "full_name", "name", "display_name") or " ".join(
        p for p in (first, last) if p)
    if not dex_id or not name:
        return None
    last_seen = _first_str(row, "last_seen", "last_interaction", "last_contacted_at",
                           "last_interaction_date")
    return Contact(
        dex_id=dex_id,
        name=name,
        emails=_string_list(row, "emails", "email", "contact_emails"),
        phones=_string_list(row, "phones", "phone_number", "contact_phone_numbers"),
        last_seen=(relationships.parse_date(last_seen).isoformat()
                   if relationships.parse_date(last_seen) else None),
        company=_first_str(row, "company", "job_company_name", "organization"),
        linkedin=_first_str(row, "linkedin", "linkedin_url"),
    )


def fetch_contacts(config, fetch=_fetch) -> tuple[list[Contact], int]:
    """Every Dex contact, following pagination. Returns (contacts, skipped)."""
    key = _api_key()
    dex = config.raw.get("dex") or {}
    base = str(dex.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    contacts: list[Contact] = []
    seen: set[str] = set()
    skipped = 0
    offset = 0
    for _ in range(MAX_PAGES):
        url = f"{base}/contacts?" + urllib.parse.urlencode({"limit": PAGE_LIMIT,
                                                            "offset": offset})
        rows, next_offset = _extract_page(_get_json(url, key, fetch))
        if not rows:
            break
        for row in rows:
            contact = _normalize(row)
            if contact is None:
                skipped += 1
                continue
            if contact.dex_id in seen:
                continue
            seen.add(contact.dex_id)
            contacts.append(contact)
        if next_offset is not None and next_offset != offset:
            offset = next_offset
        elif len(rows) < PAGE_LIMIT:
            break
        else:
            offset += len(rows)
    return contacts, skipped


# ---- vault writes ---------------------------------------------------------------

def _git_commit(vault: Path, message: str) -> None:
    """Never raises: a git hiccup must not cost the user their sync."""
    try:
        inside = subprocess.run(["git", "-C", str(vault), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True)
        if inside.returncode != 0:
            log.info("vault is not a git repo — skipping commit (%s)", message)
            return
        subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(vault), "commit", "-m", message, "--allow-empty"],
                       check=True, capture_output=True)
    except Exception:
        log.exception("dex sync git commit failed (%s)", message)


def _channels_line(contact: Contact) -> str:
    email = contact.emails[0] if contact.emails else ""
    whatsapp = contact.phones[0] if contact.phones else ""
    return f"{{whatsapp: {whatsapp}, email: {email}, linkedin: {contact.linkedin}}}"


def _sync_log_line(day: str) -> str:
    return f"- {day} — last interaction, synced from Dex"


def _apply_update(path: Path, contact: Contact, deeplink_base: str, today: date) -> bool:
    """Update the three app-owned fields on an existing note. Returns True when
    the file actually changed — an unchanged contact must not churn the vault or
    manufacture a git commit."""
    original = path.read_text()
    split = notefm.split_note(original)
    if split is None:
        return False
    fm_block, body = split
    fm, _ = notefm.parse_frontmatter(original)

    fm_block = notefm.stamp_field(fm_block, "dex_id", contact.dex_id)
    fm_block = notefm.stamp_field(fm_block, "dex_deeplink", contact.deeplink(deeplink_base))

    # last_contact only ever moves forward: Dex knowing about an older
    # interaction must not undo a contact the owner logged here today.
    current = relationships.parse_date(fm.get("last_contact"))
    incoming = relationships.parse_date(contact.last_seen)
    if incoming is not None and (current is None or incoming > current):
        fm_block = notefm.stamp_field(fm_block, "last_contact", incoming.isoformat())
        line = _sync_log_line(incoming.isoformat())
        if line not in body:                        # idempotent across re-syncs
            body = people.append_interaction(body, line)
        fm["last_contact"] = incoming.isoformat()

    fm_block = people.stamp_status(fm_block, fm, today)
    updated = notefm.compose_note(fm_block, body)
    if updated == original:
        return False
    path.write_text(updated)
    return True


def _create_note(vault: Path, contact: Contact, deeplink_base: str,
                 stamp: datetime) -> Path:
    folder = Path(vault) / people.PEOPLE_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    created = stamp.strftime("%Y-%m-%d")
    note = people.build_person_note(
        note_id=stamp.strftime("%Y%m%d%H%M%S"),
        name=contact.name,
        created=created,
        source="dex",
        origin="ai",            # the app wrote this note, not the owner
        fields={
            "company": contact.company,
            "channels": _channels_line(contact),
            "dex_id": contact.dex_id,
            "dex_deeplink": contact.deeplink(deeplink_base),
            "last_contact": contact.last_seen or "",
        })
    path = folder / people.note_filename(contact.name, created)
    i = 1
    while path.exists():
        i += 1
        path = folder / people.note_filename(f"{contact.name}-{i}", created)
    path.write_text(note)
    return path


def sync(config, events=None, fetch=_fetch, now=None) -> SyncResult:
    """Pull every Dex contact into 07-People. Fetch first, write second: if Dex
    can't be reached, the vault is never touched at all."""
    now = now or datetime.now()
    today = now.date()
    dex = config.raw.get("dex") or {}
    deeplink_base = str(dex.get("deeplink_base") or DEFAULT_DEEPLINK_BASE)
    vault = Path(config.vault_path)

    contacts, skipped = fetch_contacts(config, fetch)
    result = SyncResult(skipped=skipped)
    if not contacts:
        return result

    by_dex_id = {fm.get("dex_id"): path for path, fm, _ in people.person_notes(vault)
                 if fm.get("dex_id")}
    existing = [(c, by_dex_id[c.dex_id]) for c in contacts if c.dex_id in by_dex_id]
    fresh = [c for c in contacts if c.dex_id not in by_dex_id]

    # Commit BEFORE the batch write so the whole import is one revert away
    # (CLAUDE.md §3). Only when there's something that could change.
    if fresh or existing:
        _git_commit(vault, "pre-dex-sync")

    for contact, path in existing:
        if _apply_update(path, contact, deeplink_base, today):
            result.updated += 1
        else:
            result.unchanged += 1

    stamp = now
    for contact in fresh:
        _create_note(vault, contact, deeplink_base, stamp)
        stamp += timedelta(seconds=1)       # ids are timestamps and must be unique
        result.created += 1

    if result.changed:
        _git_commit(vault, f"dex: sync +{result.created} new, ~{result.updated} updated")
        if events is not None:
            events.log("dex", "dex", "ok",
                       message=f"created={result.created} updated={result.updated} "
                               f"unchanged={result.unchanged} skipped={result.skipped}")
    return result


# ---- nightly tick -----------------------------------------------------------------

def tick(config, events, now=None, fetch=_fetch) -> None:
    """One sync a day, at/after SYNC_HOUR, when dex.sync_daily is on. Never
    raises — the watcher loop keeps running whatever Dex does."""
    try:
        dex = config.raw.get("dex") or {}
        if not dex.get("sync_daily") or not os.environ.get(ENV_KEY):
            return
        now = now or datetime.now()
        if now.hour < SYNC_HOUR:
            return
        key = f"dex-sync-{now.date().isoformat()}"
        if events.reminder_fired(key):
            return
        events.mark_reminder(key)
        result = sync(config, events, fetch=fetch, now=now)
        log.info("dex nightly sync: %s", result.as_dict())
    except Exception:
        log.exception("dex nightly sync failed")
