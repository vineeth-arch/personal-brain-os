"""EventLog.last_vault_sync_ok (Task 4): the build tracker's vault_sync_healthy
probe reads this to answer "is sync actually current" — ok and resolved both
count as healthy; failed/conflict/no-remote don't, and no row at all is None."""
from __future__ import annotations

from pipeline.events import EventLog


def test_last_vault_sync_ok_none_when_no_events(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    events = EventLog(tmp_path / "events.db", vault)
    assert events.last_vault_sync_ok() is None
    events.close()


def test_last_vault_sync_ok_ignores_failed_and_picks_latest_healthy(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    events = EventLog(tmp_path / "events.db", vault)

    events.log(str(vault), "vault_sync", "ok", message="status=ok ahead=0 behind=0")
    first = events.last_vault_sync_ok()
    assert first is not None

    events.log(str(vault), "vault_sync", "failed", message="status=conflict ahead=1 behind=1")
    assert events.last_vault_sync_ok() == first  # the failed row doesn't override it

    events.log(str(vault), "vault_sync", "resolved", message="status=resolved ahead=0 behind=0")
    second = events.last_vault_sync_ok()
    assert second is not None and second >= first
    events.close()
