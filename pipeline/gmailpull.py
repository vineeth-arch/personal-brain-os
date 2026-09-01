"""Gmail "brain" label pull (Pass E, E4) — opt-in, readonly, one note per
labeled email, filed straight to 04-Resources (no LLM classify — deterministic
by design, since a labeled email is already a human decision, not a guess).

Everything the API layer needs to reach Gmail (OAuth token refresh, the
GOOGLE_CLIENT_ID/SECRET dance) lives in api/google.py — this module never
imports it at module level (see pipeline/watcher.py::drain_tick's docstring
for why: no pipeline→api coupling at import time). gmail_tick, the one
function here that needs a real token or a git commit, reaches into api/
via a lazy import inside its own body, exactly like drain_tick does.

Everything else here is pure: it takes an already-authenticated
fetch: Callable[[str], dict] (URL in, parsed JSON out — always a GET, there
is no way to express a write through this signature) and never mutates
Gmail. Readonly is structural, not a promise."""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import route
from .events import EventLog

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
DEFAULT_LABEL = "brain"
MAX_MESSAGES = 20

_ID_RE = re.compile(r"^id:\s*(\d+)\s*$", re.MULTILINE)


def _existing_note_ids(vault_path: Path) -> set[str]:
    """Every `id:` already claimed by a note in 04-Resources — mirrors
    relationships.create_person's `{p.id for p in load_people(...)}` id-
    collision guard, since resource notes have no equivalent loader."""
    ids = set()
    folder = Path(vault_path) / route.TYPE_FOLDER["resource"]
    if not folder.is_dir():
        return ids
    for path in folder.glob("*.md"):
        head = path.read_text(encoding="utf-8", errors="replace")[:400]
        m = _ID_RE.search(head)
        if m:
            ids.add(m.group(1))
    return ids


def _header(msg: dict, name: str) -> str:
    for h in (msg.get("payload") or {}).get("headers") or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _find_label_id(fetch: Callable[[str], dict], label: str) -> str | None:
    data = fetch(f"{GMAIL_BASE}/labels")
    for lbl in data.get("labels") or []:
        if lbl.get("name") == label:
            return lbl.get("id")
    return None


def _list_labeled_messages(fetch: Callable[[str], dict], label_id: str) -> list[str]:
    data = fetch(f"{GMAIL_BASE}/messages?" + urllib.parse.urlencode(
        {"labelIds": label_id, "maxResults": MAX_MESSAGES}))
    return [m["id"] for m in (data.get("messages") or []) if m.get("id")]


def _fetch_message(fetch: Callable[[str], dict], message_id: str) -> dict:
    msg = fetch(f"{GMAIL_BASE}/messages/{message_id}?" + urllib.parse.urlencode(
        {"format": "metadata",
         "metadataHeaders": ["From", "Subject", "Date"]}, doseq=True))
    return {
        "id": msg.get("id", message_id),
        "from": _header(msg, "From"),
        "subject": _header(msg, "Subject") or "(no subject)",
        "date": _header(msg, "Date"),
        "snippet": msg.get("snippet", ""),
    }


def _note_text(msg: dict, note_id: str, created: str) -> str:
    body = (
        f"From: {msg['from']} · {msg['date']}\n\n"
        f"{msg['snippet']}\n\n"
        f"https://mail.google.com/mail/u/0/#all/{msg['id']}\n"
    )
    return (
        "---\n"
        f"id: {note_id}\n"
        "type: resource\n"
        f"created: {created}\n"
        "source: gmail\n"
        "origin: ai\n"
        "status: inbox\n"
        "categories: []\n"
        "subjects: []\n"
        "tags: []\n"
        "duration_min:\n"
        "resource_type: article\n"
        f"title: {msg['subject']}\n"
        "cover:\n"
        f"source_url: https://mail.google.com/mail/u/0/#all/{msg['id']}\n"
        "archive_url:\n"
        "description:\n"
        "insight:\n"
        "rating:\n"
        f"captured: {created}\n"
        "consumed:\n"
        "---\n\n"
        f"{body}"
    )


def pull(vault_path: Path, events: EventLog, *, fetch: Callable[[str], dict],
         label: str = DEFAULT_LABEL, now: datetime | None = None) -> dict:
    """Pure pull logic — file one note per newly-labeled message, mark it
    ingested as it's written (so a crash mid-run is safe to retry: anything
    not yet marked simply gets re-attempted, and marking only ever happens
    right after a successful write). Returns {"filed": n, "label_found": bool}.
    Never commits — the caller (gmail_tick) owns that, matching every other
    pipeline proposer/writer in this codebase (split.py, resurface.py)."""
    now = now or datetime.now()
    label_id = _find_label_id(fetch, label)
    if label_id is None:
        return {"filed": 0, "label_found": False}

    message_ids = _list_labeled_messages(fetch, label_id)
    folder = Path(vault_path) / route.TYPE_FOLDER["resource"]
    folder.mkdir(parents=True, exist_ok=True)
    taken_ids = _existing_note_ids(vault_path)
    when = now
    filed = 0

    for message_id in message_ids:
        if events.gmail_ingested(message_id):
            continue
        msg = _fetch_message(fetch, message_id)

        # The id is the durable handle every link points at (SCHEMA §1), so
        # it has to be unique even for two messages pulled inside the same
        # second — step forward until no note in the vault claims it. Same
        # idiom as relationships.create_person's id-collision guard.
        while when.strftime("%Y%m%d%H%M%S") in taken_ids:
            when += timedelta(seconds=1)
        note_id = when.strftime("%Y%m%d%H%M%S")
        taken_ids.add(note_id)
        created = when.date().isoformat()

        text = _note_text(msg, note_id, created)
        base = f"{created}-{route._kebab(msg['subject'])}"
        path = folder / f"{base}.md"
        i = 1
        while path.exists():
            i += 1
            path = folder / f"{base}-{i}.md"
        path.write_text(text, encoding="utf-8")

        events.mark_gmail_ingested(message_id)
        filed += 1

    return {"filed": filed, "label_found": True}


def gmail_tick(config, events: EventLog, *, fetch: Callable[[str], dict] | None = None,
              token_cache: dict | None = None) -> None:
    """Registered in watcher.run_loop. Silent no-op unless Google is
    connected — a cockpit with no Google account linked pays nothing for
    this tick. Lazy imports: see this module's docstring and
    pipeline/watcher.py::drain_tick for why api/ is only ever reached from
    inside a function body here, never at module load. Never raises — same
    "a tick may fail, the loop may not" contract as every other tick."""
    if not config.raw.get("google", {}).get("refresh_token"):
        return
    label = config.raw.get("google", {}).get("pull_label") or DEFAULT_LABEL

    try:
        if fetch is None:
            from api import google as google_mod
            cache = token_cache if token_cache is not None else {}
            token = google_mod.access_token(config, cache)

            def fetch(url: str) -> dict:
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read())

        result = pull(Path(config.vault_path), events, fetch=fetch, label=label)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("gmail pull tick failed — retrying at the next poll")
        return

    if not result["label_found"]:
        events.log(str(config.vault_path), "gmail_pull", "ok",
                  message=f"label '{label}' not found in account")
        return

    if result["filed"] > 0:
        from api.notes import git_commit_vault
        git_commit_vault(Path(config.vault_path), f"gmail pull: {result['filed']} labeled emails filed")

    events.log(str(config.vault_path), "gmail_pull", "ok", message=f"filed={result['filed']}")
