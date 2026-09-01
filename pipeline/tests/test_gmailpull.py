"""Pass E, task E4: Gmail "brain" label pull. Hermetic — `fetch` is injected
(URL in, parsed JSON out, always a GET), so no network and no real OAuth
token is needed. Follows this repo's established fake-fetch style (a small
class recording every URL it's called with, matching FakeDex/FakePeopleApi
in api/tests/test_push.py) and the git commit-count assertion convention
from pipeline/tests/test_drain.py."""
from __future__ import annotations

import json as json_mod
import re
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import gmailpull
from pipeline.events import EventLog

NOW = datetime(2026, 9, 1, 9, 0)


class FakeGmail:
    """Stands in for the network. Records every URL it's asked to GET, so a
    test can assert the call log — and, per this task's readonly guarantee,
    that every URL it ever saw was a labels/messages GET-shaped endpoint."""

    def __init__(self, labels: list[dict], messages: list[dict]):
        self.labels = labels
        self.messages = {m["id"]: m for m in messages}
        self.message_order = [m["id"] for m in messages]
        self.calls: list[str] = []

    def __call__(self, url: str) -> dict:
        self.calls.append(url)
        if url == f"{gmailpull.GMAIL_BASE}/labels":
            return {"labels": self.labels}
        if url.startswith(f"{gmailpull.GMAIL_BASE}/messages/"):
            message_id = url.split(f"{gmailpull.GMAIL_BASE}/messages/", 1)[1].split("?", 1)[0]
            msg = self.messages[message_id]
            return {
                "id": message_id,
                "snippet": msg.get("snippet", ""),
                "payload": {"headers": [
                    {"name": "From", "value": msg.get("from", "")},
                    {"name": "Subject", "value": msg.get("subject", "")},
                    {"name": "Date", "value": msg.get("date", "")},
                ]},
            }
        if url.startswith(f"{gmailpull.GMAIL_BASE}/messages?"):
            return {"messages": [{"id": mid} for mid in self.message_order]}
        raise AssertionError(f"unexpected fetch: {url}")


def _fake(label="brain", label_id="Label_1", messages=None):
    messages = messages if messages is not None else [
        {"id": "msg1", "from": "a@example.com", "subject": "Great read on gardening",
         "date": "Mon, 1 Sep 2026 09:00:00 +0000", "snippet": "check this out"},
        {"id": "msg2", "from": "b@example.com", "subject": "Interesting piece on cooking",
         "date": "Mon, 1 Sep 2026 10:00:00 +0000", "snippet": "worth a read"},
    ]
    return FakeGmail([{"id": label_id, "name": label}], messages)


def make_config(vault: Path, **google) -> SimpleNamespace:
    return SimpleNamespace(vault_path=vault, raw={"google": google} if google else {})


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(vault), *args], check=True,
                          capture_output=True, text=True).stdout


def _init_git_vault(vault: Path) -> None:
    _git(vault, "init", "-q")
    _git(vault, "config", "user.email", "t@example.com")
    _git(vault, "config", "user.name", "Test")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "--allow-empty", "-m", "initial vault state")


def _commit_count(vault: Path) -> int:
    return len(_git(vault, "log", "--oneline").splitlines())


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture
def events(tmp_path, vault):
    log = EventLog(tmp_path / "events.db", vault)
    yield log
    log.close()


# ---- 1. dedupe across two runs ----------------------------------------------

def test_dedupe_across_two_runs_files_each_message_once(vault, events):
    fake = _fake()
    result1 = gmailpull.pull(vault, events, fetch=fake, now=NOW)
    assert result1 == {"filed": 2, "label_found": True}

    result2 = gmailpull.pull(vault, events, fetch=fake, now=NOW)
    assert result2 == {"filed": 0, "label_found": True}

    files = list((vault / "04-Resources").glob("*.md"))
    assert len(files) == 2


# ---- 2. note frontmatter is schema-correct -----------------------------------

def test_note_frontmatter_is_schema_correct(vault, events):
    fake = _fake(messages=[
        {"id": "msg1", "from": "a@example.com", "subject": "A Piece Worth Reading",
         "date": "Mon, 1 Sep 2026 09:00:00 +0000", "snippet": "hello"},
    ])
    gmailpull.pull(vault, events, fetch=fake, now=NOW)

    files = list((vault / "04-Resources").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")

    id_match = re.search(r"^id: (\d+)$", text, re.MULTILINE)
    assert id_match and re.match(r"^\d{14}$", id_match.group(1))
    assert "origin: ai" in text
    assert "source: gmail" in text
    assert "type: resource" in text
    assert "source_url: https://mail.google.com/mail/u/0/#all/msg1" in text
    assert "title: A Piece Worth Reading" in text


# ---- 3. label not found -> silent no-op --------------------------------------

def test_label_not_found_is_a_silent_no_op(vault, events):
    fake = _fake(label="other-label")
    result = gmailpull.pull(vault, events, fetch=fake, now=NOW)
    assert result == {"filed": 0, "label_found": False}
    assert not (vault / "04-Resources").exists() or not list((vault / "04-Resources").glob("*.md"))
    assert events.gmail_ingested("msg1") is False


# ---- 4. disconnected -> gmail_tick no-op, fetch never invoked ----------------

def test_disconnected_short_circuits_before_any_fetch(vault, events):
    def exploding_fetch(url: str) -> dict:
        raise AssertionError("fetch must never be called when Google isn't connected")

    config = make_config(vault)  # no "google" key at all
    gmailpull.gmail_tick(config, events, fetch=exploding_fetch)  # must not raise

    config_no_token = make_config(vault, refresh_token=None)
    gmailpull.gmail_tick(config_no_token, events, fetch=exploding_fetch)


# ---- 5. readonly — structural proof ------------------------------------------

def test_every_call_is_a_get_shaped_listing_or_read_endpoint(vault, events):
    fake = _fake()
    gmailpull.pull(vault, events, fetch=fake, now=NOW)
    gmailpull.pull(vault, events, fetch=fake, now=NOW)  # second run: dedupe path too

    allowed = re.compile(
        rf"^{re.escape(gmailpull.GMAIL_BASE)}/(labels|messages(\?|/))")
    assert fake.calls, "the fake should have been called at all"
    for url in fake.calls:
        assert allowed.match(url), f"non-GET-listing URL seen: {url}"


# ---- 6. gmail_tick end-to-end with a real EventLog and one commit -----------

def test_gmail_tick_end_to_end_files_and_commits_once(vault, events):
    _init_git_vault(vault)
    before = _commit_count(vault)

    fake = _fake()
    config = make_config(vault, refresh_token="x")
    gmailpull.gmail_tick(config, events, fetch=fake)

    files = list((vault / "04-Resources").glob("*.md"))
    assert len(files) == 2
    after = _commit_count(vault)
    assert after == before + 1

    log = _git(vault, "log", "-1", "--format=%s")
    assert log.strip() == "gmail pull: 2 labeled emails filed"


def test_gmail_tick_zero_filed_makes_no_commit(vault, events):
    _init_git_vault(vault)
    before = _commit_count(vault)

    fake = _fake(label="other-label")
    config = make_config(vault, refresh_token="x")
    gmailpull.gmail_tick(config, events, fetch=fake)

    assert _commit_count(vault) == before


# ---- 7. a real token_cache dict is reused across ticks -----------------------

def test_shared_token_cache_avoids_a_second_token_exchange(vault, events, monkeypatch):
    """The fix for Important #4: run_loop must hold one token_cache dict
    across ticks (not build a fresh empty one every call), or gmail_tick's
    own access_token() never finds a still-valid cached token and does a
    full refresh-token exchange every single poll. This exercises the REAL
    api.google.access_token() caching path (fetch=None, so gmail_tick builds
    its own urllib-based fetch) rather than the injected FakeGmail, since
    that's the only way to prove the token exchange itself is skipped on the
    second call. Follows this file's own established fake-fetch idiom, just
    one boundary lower: the network call being faked is Google's OAuth token
    endpoint (via api.google._token_request) instead of the Gmail API."""
    from api import google as google_mod

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")

    token_requests: list[dict] = []

    def fake_token_request(fields: dict) -> dict:
        token_requests.append(fields)
        if len(token_requests) > 1:
            raise AssertionError(
                "a second token exchange happened — the token_cache wasn't reused")
        return {"access_token": "tok-1", "expires_in": 3600}

    monkeypatch.setattr(google_mod, "_token_request", fake_token_request)

    # Fakes Google's Gmail HTTP boundary (gmail_tick's own internal fetch
    # closure, built only when fetch=None) — not FakeGmail, since exercising
    # the real access_token() path is the whole point of this test. Returns
    # "label not found" so pull() short-circuits after exactly one GET,
    # keeping the fake minimal, and records the Authorization header seen
    # each time so the SAME cached token is proven to have been reused.
    seen_auth_headers: list[str] = []

    class FakeResponse:
        def __init__(self, payload: dict):
            self._body = json_mod.dumps(payload).encode("utf-8")

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=10):
        seen_auth_headers.append(req.get_header("Authorization"))
        return FakeResponse({"labels": [{"id": "Label_1", "name": "not-brain"}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    config = make_config(vault, refresh_token="rt")
    token_cache: dict = {}

    gmailpull.gmail_tick(config, events, token_cache=token_cache)
    gmailpull.gmail_tick(config, events, token_cache=token_cache)

    assert len(token_requests) == 1, "the token exchange must only happen once"
    assert seen_auth_headers == ["Bearer tok-1", "Bearer tok-1"]
    assert token_cache["access_token"] == "tok-1"
