"""Google read+draft integration (Gmail + Calendar), stdlib only.

Scope is deliberately narrow, per the constitution:
- READ: recent unread Gmail, next 7 days of Calendar events.
- DRAFT: create a Gmail draft the user sends themselves, in Gmail.
- There is NO send. Rule 4 (nothing auto-sends, ever) is enforced
  structurally: this module never touches Gmail's send endpoint, and a test
  pins that. Do not add one.

Auth is the OAuth authorization-code flow against the user's OWN Google Cloud
client (GO-LIVE.md §6): client id/secret come from the environment (keys are
env-only, rule 7), the long-lived refresh token is runtime state and lives in
config.json under "google.refresh_token". Short-lived access tokens are cached
in memory (app.state) and never persisted."""
from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

log = logging.getLogger("api")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
CONTACTS_SCOPE = "https://www.googleapis.com/auth/contacts"
SCOPES = " ".join([
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.readonly",
    # Pass D: update the notes field of contacts that ALREADY exist. The app
    # never creates a contact, and never reads the address book into the vault.
    CONTACTS_SCOPE,
])
TIMEOUT = 10
STATE_TTL_SECONDS = 600
INBOX_LIMIT = 10
EVENT_DAYS = 7


class GoogleError(Exception):
    def __init__(self, status: int, what: str, cause: str, todo: str):
        self.status = status
        self.envelope = {"what": what, "cause": cause, "todo": todo}
        super().__init__(what)


def _client() -> tuple[str, str]:
    cid = os.environ.get("GOOGLE_CLIENT_ID", "")
    secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not (cid and secret):
        raise GoogleError(
            503, "Google isn't set up on this server yet.",
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET aren't in the server's environment.",
            "Follow GO-LIVE.md §6 to create the OAuth client and put both values in .env.")
    return cid, secret


def configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def refresh_token(config) -> str:
    return str((config.raw.get("google") or {}).get("refresh_token") or "")


def connected(config) -> bool:
    return bool(refresh_token(config))


def granted_scopes(config) -> str:
    return str((config.raw.get("google") or {}).get("scopes") or "")


def has_contacts_scope(config) -> bool:
    """True only when the stored grant includes the contacts permission.

    An account linked before Pass D has a valid refresh token but no contacts
    scope — that is a normal state, and the honest answer is "reconnect once",
    not a failed API call."""
    return CONTACTS_SCOPE in granted_scopes(config)


# ---- OAuth flow ---------------------------------------------------------------

def begin_connect(states: dict, redirect_uri: str) -> str:
    """Mint a CSRF state, remember it (with the redirect_uri the token exchange
    must repeat verbatim), hand back the Google consent URL."""
    cid, _ = _client()  # raises the not-configured envelope early
    now = time.monotonic()
    for key in [k for k, (exp, _uri) in states.items() if exp < now]:
        del states[key]
    state = secrets.token_urlsafe(24)
    states[state] = (now + STATE_TTL_SECONDS, redirect_uri)
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",   # → refresh token
        "prompt": "consent",        # re-issue the refresh token on reconnect
        "state": state,
    })


def finish_connect(states: dict, config_path: Path, state: str, code: str) -> None:
    """Callback half: validate state, trade the code for tokens, persist the
    refresh token into config.json (atomic, everything else untouched)."""
    expiry, redirect_uri = states.pop(state, (0, "")) if state else (0, "")
    if expiry < time.monotonic():
        raise GoogleError(
            400, "The Google sign-in couldn't be completed.",
            "The sign-in link had expired or wasn't started from this cockpit.",
            "Go back to Integrations and press Connect Google again.")
    cid, csecret = _client()
    tokens = _token_request({
        "client_id": cid, "client_secret": csecret, "code": code,
        "grant_type": "authorization_code", "redirect_uri": redirect_uri,
    })
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise GoogleError(
            502, "Google didn't hand back a long-term connection.",
            "The token response had no refresh token (the client may be misconfigured).",
            "Remove the app at myaccount.google.com/permissions, then Connect Google again.")

    raw = json.loads(config_path.read_text())
    raw.setdefault("google", {})["refresh_token"] = refresh
    # What Google ACTUALLY granted, so "do we have the contacts permission?"
    # is answerable without a network call (and so a link made before Pass D
    # reports honestly that it needs one re-consent).
    raw["google"]["scopes"] = tokens.get("scope") or ""
    fd, tmp = tempfile.mkstemp(dir=config_path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(raw, f, indent=2)
            f.write("\n")
        os.replace(tmp, config_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def disconnect(config_path: Path, token_cache: dict) -> None:
    raw = json.loads(config_path.read_text())
    raw.pop("google", None)
    fd, tmp = tempfile.mkstemp(dir=config_path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(raw, f, indent=2)
            f.write("\n")
        os.replace(tmp, config_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    token_cache.clear()


def _token_request(fields: dict) -> dict:
    req = urllib.request.Request(
        TOKEN_URL, data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        log.info("google token request failed: %s %s", e.code, detail)
        raise GoogleError(
            502, "Google rejected the sign-in.",
            "The token exchange failed — the client secret may be wrong, or access was revoked.",
            "Check GOOGLE_CLIENT_ID/SECRET in .env, or reconnect from Integrations.")
    except Exception as e:
        log.info("google token request failed: %s", e)
        raise GoogleError(
            502, "Google couldn't be reached.",
            "The network request to Google's token endpoint failed.",
            "Check the server's internet connection and try again.")


def access_token(config, token_cache: dict) -> str:
    """Cached access token; refresh via the stored refresh token when expired."""
    if token_cache.get("access_token") and token_cache.get("expires_at", 0) > time.time() + 30:
        return token_cache["access_token"]
    refresh = refresh_token(config)
    if not refresh:
        raise GoogleError(
            409, "Google isn't connected yet.",
            "No Google account has been linked to this cockpit.",
            "Open Integrations and press Connect Google.")
    cid, csecret = _client()
    tokens = _token_request({
        "client_id": cid, "client_secret": csecret,
        "refresh_token": refresh, "grant_type": "refresh_token",
    })
    if not tokens.get("access_token"):
        raise GoogleError(
            502, "Google didn't hand back a usable connection.",
            "The refresh response came back without an access token — the link "
            "may have been revoked from your Google account.",
            "Open Integrations, press Disconnect, then Connect Google again.")
    token_cache["access_token"] = tokens["access_token"]
    token_cache["expires_at"] = time.time() + int(tokens.get("expires_in", 3600))
    return token_cache["access_token"]


# ---- API calls ----------------------------------------------------------------

def _get(token: str, url: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return _call(req)


def _call(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        log.info("google api call failed: %s %s", e.code, e.read().decode(errors="replace")[:200])
        if e.code in (401, 403):
            raise GoogleError(
                401, "Google turned the request away.",
                "The connection has expired or lost its permissions.",
                "Open Integrations and press Connect Google to re-link the account.")
        raise GoogleError(
            502, "Google returned an error.",
            "The Gmail/Calendar API call didn't succeed — it may be a passing hiccup.",
            "Try again in a moment.")
    except GoogleError:
        raise
    except Exception as e:
        log.info("google api call failed: %s", e)
        raise GoogleError(
            502, "Google couldn't be reached.",
            "The network request to Google failed.",
            "Check the server's internet connection and try again.")


def _header(msg: dict, name: str) -> str:
    for h in (msg.get("payload") or {}).get("headers") or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def unread(config, token_cache: dict) -> list[dict]:
    token = access_token(config, token_cache)
    listing = _get(token, f"{GMAIL_BASE}/messages?"
                          + urllib.parse.urlencode(
                              {"q": "in:inbox is:unread", "maxResults": INBOX_LIMIT}))
    items = []
    for stub in listing.get("messages") or []:
        msg = _get(token, f"{GMAIL_BASE}/messages/{stub['id']}?"
                          + urllib.parse.urlencode(
                              {"format": "metadata",
                               "metadataHeaders": ["From", "Subject", "Date"]}, doseq=True))
        items.append({
            "id": msg.get("id", ""),
            "from": _header(msg, "From"),
            "subject": _header(msg, "Subject") or "(no subject)",
            "date": _header(msg, "Date"),
            "snippet": msg.get("snippet", ""),
            "url": f"https://mail.google.com/mail/u/0/#inbox/{msg.get('id', '')}",
        })
    return items


def events(config, token_cache: dict) -> list[dict]:
    token = access_token(config, token_cache)
    now = datetime.now(timezone.utc)
    listing = _get(token, f"{CALENDAR_BASE}/calendars/primary/events?"
                          + urllib.parse.urlencode({
                              "timeMin": now.isoformat(),
                              "timeMax": (now + timedelta(days=EVENT_DAYS)).isoformat(),
                              "singleEvents": "true", "orderBy": "startTime",
                              "maxResults": 20,
                          }))
    items = []
    for ev in listing.get("items") or []:
        start = ev.get("start") or {}
        end = ev.get("end") or {}
        items.append({
            "id": ev.get("id", ""),
            "summary": ev.get("summary") or "(untitled)",
            "start": start.get("dateTime") or start.get("date") or "",
            "end": end.get("dateTime") or end.get("date") or "",
            "all_day": "date" in start,
            "location": ev.get("location") or "",
            "url": ev.get("htmlLink") or "https://calendar.google.com/",
        })
    return items


def create_draft(config, token_cache: dict, to: str, subject: str, text: str) -> dict:
    """Create a Gmail DRAFT. The user reviews and sends it in Gmail — the
    cockpit has no way to send (rule 4), which is why this posts to /drafts
    and nowhere else. Do not add a send path here."""
    token = access_token(config, token_cache)
    mime = MIMEText(text)
    mime["To"] = to
    mime["Subject"] = subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    req = urllib.request.Request(
        f"{GMAIL_BASE}/drafts",
        data=json.dumps({"message": {"raw": raw}}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    created = _call(req)
    draft_id = created.get("id", "")
    return {"id": draft_id, "url": "https://mail.google.com/mail/u/0/#drafts"}
