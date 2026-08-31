"""Pass 12 — Google read + draft.

The load-bearing test in this file is test_no_send_path_exists_anywhere: the
constitution says the app never sends anything (CLAUDE.md §4), and that has to
be structural, not a promise. Everything else covers the OAuth token dance and
the parsing, with urlopen mocked — no network, no Google account needed.
"""
from __future__ import annotations

import io
import json
import re
import time
import urllib.error
from pathlib import Path

import pytest

from api import google

REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeConfig:
    """Stands in for pipeline.config.Config — google only reads .raw."""

    def __init__(self, refresh: str | None = "refresh-token-abc"):
        self.raw = {"google": {"refresh_token": refresh}} if refresh else {}


@pytest.fixture
def google_client(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")


def _response(payload: dict):
    class R(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return R(json.dumps(payload).encode())


class Recorder:
    """Captures every URL google.py opens, so tests can assert on them.

    First argument answers every token refresh (however many happen); the rest
    answer the API calls in order, with the last one repeating. Tests then only
    have to describe the responses they care about.
    """

    def __init__(self, token_payload, *api_payloads):
        self.token_payload = token_payload
        self.api_payloads = list(api_payloads) or [{}]
        self.urls: list[str] = []
        self.bodies: list[bytes | None] = []

    def __call__(self, req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        self.urls.append(url)
        self.bodies.append(None if isinstance(req, str) else req.data)
        if url == google.TOKEN_URL:
            return _response(self.token_payload)
        payload = self.api_payloads[0]
        if len(self.api_payloads) > 1:
            self.api_payloads.pop(0)
        return _response(payload)


# ---- Rule 4: no send path, structurally ---------------------------------------

def test_no_send_path_exists_anywhere():
    """CLAUDE.md §4 — the app never auto-sends. Gmail's send API is
    users/me/messages/send; if that ever appears in the codebase, someone has
    built a send path and this build must fail. Drafts (users/me/drafts) are
    fine: the user sends them from Gmail themselves."""
    offenders = []
    pattern = re.compile(r"messages/send|/send\b|sendMessage", re.IGNORECASE)
    for folder in ("api", "pipeline", "web/src", "scripts"):
        for path in (REPO_ROOT / folder).rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"} or not path.is_file():
                continue
            # the guard tests spell the patterns out in order to forbid them
            if path.name in {"test_google.py", "test_no_send.py"}:
                continue
            for i, line in enumerate(path.read_text().splitlines(), start=1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{i}: {line.strip()}")
    assert not offenders, (
        "A Gmail send path appeared — CLAUDE.md §4 forbids the app sending "
        "anything. Offending lines:\n" + "\n".join(offenders))


def test_draft_posts_to_drafts_only(monkeypatch, google_client):
    rec = Recorder({"access_token": "at", "expires_in": 3600}, {"id": "draft-1"})
    monkeypatch.setattr(google.urllib.request, "urlopen", rec)

    out = google.create_draft(FakeConfig(), {}, "a@b.com", "Hello", "Body text")

    assert out["id"] == "draft-1"
    api_calls = [u for u in rec.urls if "gmail.googleapis.com" in u]
    assert api_calls == [f"{google.GMAIL_BASE}/drafts"]
    assert not any("send" in u for u in rec.urls)


# ---- token handling ------------------------------------------------------------

def test_access_token_refreshes_and_caches(monkeypatch, google_client):
    rec = Recorder({"access_token": "fresh-token", "expires_in": 3600})
    monkeypatch.setattr(google.urllib.request, "urlopen", rec)
    cache: dict = {}

    assert google.access_token(FakeConfig(), cache) == "fresh-token"
    assert len(rec.urls) == 1 and rec.urls[0] == google.TOKEN_URL
    # second call is served from cache — no second refresh
    assert google.access_token(FakeConfig(), cache) == "fresh-token"
    assert len(rec.urls) == 1


def test_expired_cache_triggers_one_refresh(monkeypatch, google_client):
    rec = Recorder({"access_token": "second", "expires_in": 3600})
    monkeypatch.setattr(google.urllib.request, "urlopen", rec)
    cache = {"access_token": "stale", "expires_at": time.time() - 1}

    assert google.access_token(FakeConfig(), cache) == "second"
    assert len(rec.urls) == 1


def test_not_connected_is_a_plain_english_409(google_client):
    with pytest.raises(google.GoogleError) as e:
        google.access_token(FakeConfig(refresh=None), {})
    assert e.value.status == 409
    assert set(e.value.envelope) == {"what", "cause", "todo"}
    assert "Connect Google" in e.value.envelope["todo"]


def test_unconfigured_server_is_a_plain_english_503(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    assert google.configured() is False
    with pytest.raises(google.GoogleError) as e:
        google.access_token(FakeConfig(), {})
    assert e.value.status == 503
    assert "GO-LIVE.md" in e.value.envelope["todo"]


def test_revoked_access_asks_for_a_reconnect(monkeypatch, google_client):
    def boom(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        if url == google.TOKEN_URL:
            return _response({"access_token": "at", "expires_in": 3600})
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(google.urllib.request, "urlopen", boom)
    with pytest.raises(google.GoogleError) as e:
        google.unread(FakeConfig(), {})
    # 502, not 401: 401 is reserved for the cockpit's own token, and reusing it
    # here logged the user out whenever Google's grant expired
    assert e.value.status == 502
    assert "Connect Google" in e.value.envelope["todo"]


# ---- OAuth state ----------------------------------------------------------------

def test_connect_state_is_single_use(monkeypatch, google_client, tmp_path):
    states: dict = {}
    url = google.begin_connect(states, "https://cockpit.example.com/api/google/callback")
    assert url.startswith(google.AUTH_URL)
    assert "access_type=offline" in url
    state = dict(x.split("=", 1) for x in url.split("?", 1)[1].split("&"))["state"]

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"vault_path": "/v"}))
    monkeypatch.setattr(google, "_token_request",
                        lambda fields: {"refresh_token": "new-refresh"})

    google.finish_connect(states, config_path, state, "auth-code")
    assert json.loads(config_path.read_text())["google"]["refresh_token"] == "new-refresh"

    # replaying the same state must fail — it was consumed
    with pytest.raises(google.GoogleError) as e:
        google.finish_connect(states, config_path, state, "auth-code")
    assert e.value.status == 400


def test_unknown_state_is_rejected(google_client, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    with pytest.raises(google.GoogleError):
        google.finish_connect({}, config_path, "forged-state", "code")


def test_finish_connect_preserves_the_rest_of_config(monkeypatch, google_client, tmp_path):
    """The refresh token is written into the live config.json — the paths,
    auth token and links around it must survive untouched."""
    config_path = tmp_path / "config.json"
    original = {"vault_path": "/vault", "api": {"auth_token": "keep-me"},
                "links": {"dex": "https://getdex.com/"}}
    config_path.write_text(json.dumps(original))
    states: dict = {}
    url = google.begin_connect(states, "https://x/api/google/callback")
    state = dict(x.split("=", 1) for x in url.split("?", 1)[1].split("&"))["state"]
    monkeypatch.setattr(google, "_token_request", lambda fields: {"refresh_token": "r"})

    google.finish_connect(states, config_path, state, "code")

    written = json.loads(config_path.read_text())
    assert written["api"]["auth_token"] == "keep-me"
    assert written["links"]["dex"] == "https://getdex.com/"
    assert written["vault_path"] == "/vault"


def test_disconnect_removes_the_token(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"vault_path": "/v", "google": {"refresh_token": "r"}}))
    cache = {"access_token": "at"}
    google.disconnect(config_path, cache)
    written = json.loads(config_path.read_text())
    assert "google" not in written
    assert written["vault_path"] == "/v"
    assert cache == {}


# ---- parsing --------------------------------------------------------------------

def test_unread_parses_headers_and_builds_links(monkeypatch, google_client):
    listing = {"messages": [{"id": "m1"}]}
    message = {
        "id": "m1", "snippet": "the preview text",
        "payload": {"headers": [
            {"name": "From", "value": "Jane Doe <jane@example.com>"},
            {"name": "Subject", "value": "Lunch?"},
            {"name": "Date", "value": "Wed, 12 Aug 2026 09:00:00 +0000"},
        ]},
    }
    calls = {"n": 0}

    def fake(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        if url == google.TOKEN_URL:
            return _response({"access_token": "at", "expires_in": 3600})
        calls["n"] += 1
        return _response(listing if calls["n"] == 1 else message)

    monkeypatch.setattr(google.urllib.request, "urlopen", fake)
    items = google.unread(FakeConfig(), {})

    assert len(items) == 1
    assert items[0]["subject"] == "Lunch?"
    assert items[0]["from"] == "Jane Doe <jane@example.com>"
    assert items[0]["snippet"] == "the preview text"
    assert items[0]["url"].endswith("/m1")


def test_subjectless_mail_gets_a_readable_placeholder(monkeypatch, google_client):
    def fake(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        if url == google.TOKEN_URL:
            return _response({"access_token": "at", "expires_in": 3600})
        if "/messages/m1" in url:
            return _response({"id": "m1", "snippet": "", "payload": {"headers": []}})
        return _response({"messages": [{"id": "m1"}]})

    monkeypatch.setattr(google.urllib.request, "urlopen", fake)
    assert google.unread(FakeConfig(), {})[0]["subject"] == "(no subject)"


def test_events_parse_timed_and_all_day(monkeypatch, google_client):
    payload = {"items": [
        {"id": "e1", "summary": "Standup",
         "start": {"dateTime": "2026-08-13T09:00:00Z"},
         "end": {"dateTime": "2026-08-13T09:15:00Z"},
         "htmlLink": "https://calendar.google.com/e1"},
        {"id": "e2", "summary": "Holiday",
         "start": {"date": "2026-08-15"}, "end": {"date": "2026-08-16"}},
    ]}
    rec = Recorder({"access_token": "at", "expires_in": 3600}, payload)
    monkeypatch.setattr(google.urllib.request, "urlopen", rec)

    items = google.events(FakeConfig(), {})
    assert [i["summary"] for i in items] == ["Standup", "Holiday"]
    assert items[0]["all_day"] is False and items[1]["all_day"] is True
    # single events, time-ordered, bounded window — the card shows a short list
    call = [u for u in rec.urls if "calendar" in u][0]
    assert "singleEvents=true" in call and "orderBy=startTime" in call


def test_empty_results_are_empty_lists_not_errors(monkeypatch, google_client):
    """An empty inbox / free week is a normal state, not a failure — the cards
    should read "0 unread", never an error."""
    rec = Recorder({"access_token": "at", "expires_in": 3600}, {})
    monkeypatch.setattr(google.urllib.request, "urlopen", rec)
    assert google.unread(FakeConfig(), {}) == []
    assert google.events(FakeConfig(), {}) == []


def test_revoked_refresh_token_is_a_plain_english_error(monkeypatch, google_client):
    """Google can answer a refresh with no access_token (access revoked from
    the Google account side). That must be a three-part error, not a crash."""
    rec = Recorder({"scope": "…", "token_type": "Bearer"})   # no access_token
    monkeypatch.setattr(google.urllib.request, "urlopen", rec)
    with pytest.raises(google.GoogleError) as e:
        google.unread(FakeConfig(), {})
    assert e.value.status == 502
    assert set(e.value.envelope) == {"what", "cause", "todo"}
    assert "Connect Google" in e.value.envelope["todo"]
