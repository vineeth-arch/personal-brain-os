"""Pass H1 — watcher.sync_vault() wiring: a quiet no-op when VAULT_GIT_REMOTE
isn't set (most deploys), one events.db row (stage=vault_sync) when it is.
The actual git mechanics are pipeline/vaultsync.py's own test suite — this
only proves the watcher calls it and logs the result honestly."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import vaultsync, watcher
from pipeline.events import EventLog


def config(vault_path: Path):
    return SimpleNamespace(vault_path=vault_path, raw={})


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VAULT_GIT_REMOTE", raising=False)


def _vault_sync_rows(events: EventLog) -> list[tuple[str, str]]:
    cur = events.conn.execute("SELECT status, message FROM events WHERE stage='vault_sync'")
    return cur.fetchall()


def test_sync_vault_is_a_noop_when_not_configured(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    events = EventLog(tmp_path / "events.db", vault)
    watcher.sync_vault(config(vault), events)
    assert _vault_sync_rows(events) == []
    events.close()


def test_sync_vault_logs_an_ok_result(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_GIT_REMOTE", "https://example.com/vault.git")
    vault = tmp_path / "vault"
    vault.mkdir()
    events = EventLog(tmp_path / "events.db", vault)

    monkeypatch.setattr(vaultsync, "sync",
                        lambda v, cfg: vaultsync.SyncResult("ok", "Vault synced.", ahead=0, behind=0))
    watcher.sync_vault(config(vault), events)

    rows = _vault_sync_rows(events)
    assert len(rows) == 1
    status, message = rows[0]
    assert status == "ok" and "status=ok" in message
    events.close()


def test_sync_vault_logs_a_conflict_as_failed_but_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_GIT_REMOTE", "https://example.com/vault.git")
    vault = tmp_path / "vault"
    vault.mkdir()
    events = EventLog(tmp_path / "events.db", vault)

    monkeypatch.setattr(
        vaultsync, "sync",
        lambda v, cfg: vaultsync.SyncResult("conflict", "The vault and the remote both changed the same note."))
    watcher.sync_vault(config(vault), events)   # must not raise

    rows = _vault_sync_rows(events)
    assert len(rows) == 1
    status, message = rows[0]
    assert status == "failed" and "status=conflict" in message
    assert "both changed the same note" in message
    events.close()
