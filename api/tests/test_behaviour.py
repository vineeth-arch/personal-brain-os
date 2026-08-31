"""P3: things that worked, but wrongly and quietly."""
from __future__ import annotations

import io
import sqlite3
import urllib.error
from datetime import date
from pathlib import Path

import pytest

from api import google, service
from pipeline import dex
from pipeline.events import EventLog


# ---- 401 belongs to the cockpit's own token, nothing else --------------------

def _http_error(code):
    def boom(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        if url == google.TOKEN_URL:
            return io.BytesIO(b'{"access_token": "at", "expires_in": 3600}')
        raise urllib.error.HTTPError(url, code, "denied", {}, io.BytesIO(b"{}"))
    return boom


@pytest.mark.parametrize("code", [401, 403])
def test_google_auth_failure_is_not_a_401(code, monkeypatch):
    """A 401 tells the client ITS token is bad; the client then clears the
    token and logs the user out. Google's grant expiring must not do that."""
    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def boom(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        if url == google.TOKEN_URL:
            return Resp(b'{"access_token": "at", "expires_in": 3600}')
        raise urllib.error.HTTPError(url, code, "denied", {}, io.BytesIO(b"{}"))

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setattr(google.urllib.request, "urlopen", boom)
    config = type("C", (), {"raw": {"google": {"refresh_token": "rt"}}})()
    with pytest.raises(google.GoogleError) as e:
        google.unread(config, {})
    assert e.value.status == 502
    assert e.value.status != 401


@pytest.mark.parametrize("code", [401, 403])
def test_dex_auth_failure_is_not_a_401(code, monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.HTTPError("u", code, "denied", {}, io.BytesIO(b"{}"))
    monkeypatch.setattr(dex.urllib.request, "urlopen", boom)
    with pytest.raises(dex.DexError) as e:
        dex._call(dex.urllib.request.Request("https://x"))
    assert e.value.status == 502


# ---- the failed count is the same number everywhere -------------------------

def test_failed_count_clears_after_a_successful_retry(tmp_path):
    """PIPELINE-STATUS.md and the digest used to remember a failure forever,
    while GET /api/status had already cleared it."""
    db, vault = tmp_path / "events.db", tmp_path / "vault"
    vault.mkdir()
    log = EventLog(db, vault)
    log.log("/inbox/a.m4a", "pipeline", "failed", message="boom", plain_english_error="x")
    assert len(log._failed_latest()) == 1
    assert len(service.failed_items(db)) == 1

    log.log("/inbox/a.m4a", "archive", "ok")          # retried, succeeded
    assert log._failed_latest() == [], "the vault status file must forget it too"
    assert service.failed_items(db) == []
    assert log.digest_stats(date.today())["failed"] == 0
    log.close()


def test_a_still_failing_file_is_still_counted(tmp_path):
    db, vault = tmp_path / "events.db", tmp_path / "vault"
    vault.mkdir()
    log = EventLog(db, vault)
    log.log("/inbox/a.m4a", "transcribe", "ok")
    log.log("/inbox/a.m4a", "pipeline", "failed", message="boom")
    assert len(log._failed_latest()) == 1
    assert len(service.failed_items(db)) == 1
    log.close()


# ---- opening the event log must not write into the vault --------------------

def test_opening_the_event_log_does_not_create_system_in_the_vault(tmp_path):
    """The API builds one per push request; opening a log should not reach into
    the vault at all."""
    db, vault = tmp_path / "events.db", tmp_path / "vault"
    vault.mkdir()
    log = EventLog(db, vault)
    assert not (vault / "_System").exists()
    log.write_status(pending=0)                       # now it may
    assert (vault / "_System" / "PIPELINE-STATUS.md").exists()
    log.close()


# ---- the Apify token never appears in a URL ---------------------------------

def test_apify_token_travels_in_a_header_not_the_query_string(monkeypatch):
    from pipeline import enrich
    seen = {}

    def fake_fetch(url, data=None, timeout=10, headers=None):
        seen["url"] = url
        seen["headers"] = headers or {}
        return b'[{"caption": "c", "displayUrl": "https://x/i.jpg"}]'

    monkeypatch.setenv("APIFY_TOKEN", "s3cret")
    config = type("C", (), {"raw": {"apify": {"actor_id": "actor"}}})()
    enrich._enrich_instagram("https://instagram.com/p/x", config, fake_fetch)
    assert "s3cret" not in seen["url"]
    assert seen["headers"].get("Authorization") == "Bearer s3cret"
