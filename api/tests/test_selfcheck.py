"""Startup self-check (Pass 5): structural problems refuse the boot with a
numbered plain-English list; soft conditions (no whisper binary, empty ntfy —
exactly what the standard test fixture has) never block it."""
from __future__ import annotations

import json

import pytest

from api.main import create_app

from .test_api import Server, env  # noqa: F401  (env is a fixture)


def test_refuses_on_missing_config(tmp_path):
    with pytest.raises(SystemExit) as exc:
        create_app(root=tmp_path)
    message = str(exc.value)
    assert "can't start" in message
    assert "1." in message
    assert "config.example.json" in message
    assert "Traceback" not in message and "Exception" not in message


def test_refuses_on_unparseable_config(tmp_path):
    (tmp_path / "config.json").write_text("{not json")
    with pytest.raises(SystemExit) as exc:
        create_app(root=tmp_path)
    assert "isn't valid JSON" in str(exc.value)


def test_refuses_on_missing_vault_dir(env):
    root, vault, _, _ = env
    config = json.loads((root / "config.json").read_text())
    config["vault_path"] = str(root / "nowhere")
    (root / "config.json").write_text(json.dumps(config))
    with pytest.raises(SystemExit) as exc:
        create_app(root=root)
    message = str(exc.value)
    assert "vault" in message and "1." in message


def test_numbered_list_covers_every_problem(env):
    root, _, _, _ = env
    config = json.loads((root / "config.json").read_text())
    config["vault_path"] = str(root / "nowhere")
    config["inbox_path"] = str(root / "also-nowhere")
    (root / "config.json").write_text(json.dumps(config))
    with pytest.raises(SystemExit) as exc:
        create_app(root=root)
    message = str(exc.value)
    assert "1." in message and "2." in message  # one numbered line per problem


def test_boots_with_empty_whisper_and_ntfy(env):
    # the standard fixture has empty whisper paths and no ntfy — these are
    # Integrations-card concerns, never boot failures
    root, _, _, _ = env
    app = create_app(root=root)
    assert app is not None


def test_selfcheck_endpoint_shape(env):
    root, _, _, _ = env
    with Server(root) as s:
        assert s.req("GET", "/api/selfcheck", token=None)[0] == 401  # auth required
        code, body = s.req("GET", "/api/selfcheck")
        assert code == 200 and body["ok"] is True
        assert body["problems"] == []
        by_id = {c["id"]: c for c in body["checks"]}
        # structural checks all pass on the fixture
        assert by_id["config"]["ok"] and by_id["path-vault"]["ok"] and by_id["events-db"]["ok"]
        # informational booleans present — and honest about the fixture's gaps
        assert by_id["auth-token"]["ok"] is True
        assert by_id["whisper"]["ok"] is False
        assert by_id["ntfy"]["ok"] is False


def test_backup_creates_real_vault_commit_and_db_copy(env):
    import sqlite3
    import subprocess
    from .test_api import _seed_events
    root, vault, _, _ = env
    (vault / "note.md").write_text("something uncommitted\n")
    _seed_events(root / "events.db", [
        {"timestamp": "2026-07-01T09:00:00", "file": "/in/a.m4a", "stage": "archive",
         "status": "ok"}])
    with Server(root) as s:
        code, body = s.req("GET", "/api/backup")
        assert code == 200 and body["last_backup"] is None  # nothing yet
        code, body = s.req("POST", "/api/backup")
        assert code == 200 and body["ok"] is True
        assert body["vault_committed"] is True and body["events_db_copied"] is True
        # a real commit landed in the vault repo
        logmsg = subprocess.run(["git", "-C", str(vault), "log", "-1", "--format=%s"],
                                capture_output=True, text=True).stdout.strip()
        assert logmsg == "api: manual backup"
        # the events.db copy is a real, openable sqlite database
        copies = list((root / "backups").glob("events-*.db"))
        assert len(copies) == 1
        conn = sqlite3.connect(copies[0])
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        conn.close()
        # and the status now reports it
        code, body = s.req("GET", "/api/backup")
        assert body["last_backup"] is not None and body["last_vault_commit"] is not None
