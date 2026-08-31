"""The container layout: the STATE root and the APP root are different places.

This is the bug this file exists to prevent. The image sets
BRAIN_COCKPIT_ROOT=/data (a mounted volume) and WORKDIR=/app (the code), but
the watcher used to anchor events.db and the heartbeat to the CWD. So the API
read /data/events.db while the loop wrote /app/events.db, and the cockpit
reported "the pipeline has never checked in" forever, served no frontend, and
threw away the ingest de-dupe table on every restart.

Every assertion below is that split expressed as an executable check, using a
temp tree shaped like the image and a CWD that is neither root.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def container(tmp_path, monkeypatch):
    """A temp tree shaped like the image: code in app/, state in data/."""
    app_root, state_root = tmp_path / "app", tmp_path / "data"
    (app_root / "web" / "dist").mkdir(parents=True)
    (app_root / "web" / "dist" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (app_root / "checks.json").write_text(
        (REPO / "checks.json").read_text(encoding="utf-8"), encoding="utf-8")
    (app_root / "api").mkdir()
    (app_root / "api" / "main.py").write_text("# stand-in\n", encoding="utf-8")

    vault, inbox = tmp_path / "vault", tmp_path / "inbox"
    for d in (state_root, vault, inbox, state_root / "archive", state_root / "failed"):
        d.mkdir(parents=True, exist_ok=True)
    (state_root / "config.json").write_text(json.dumps({
        "vault_path": str(vault), "inbox_path": str(inbox),
        "archive_path": str(state_root / "archive"),
        "failed_path": str(state_root / "failed"),
        "api": {"auth_token": "t"},
    }), encoding="utf-8")

    # the CWD is neither root — exactly the condition that exposed the bug
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BRAIN_COCKPIT_ROOT", str(state_root))
    return app_root, state_root, vault


def test_watcher_state_follows_the_env_root_not_the_cwd(container):
    """The loop must write where the API reads, whatever the CWD is."""
    app_root, state_root, _ = container
    watcher = importlib.reload(importlib.import_module("pipeline.watcher"))
    try:
        assert watcher.DB_PATH == state_root / "events.db"
        assert watcher.HEARTBEAT_PATH == state_root / ".watcher-heartbeat"
        # the old behaviour: events.db dropped straight into the CWD (/app)
        assert watcher.DB_PATH.parent != Path.cwd()
        assert watcher.HEARTBEAT_PATH.parent != Path.cwd()
    finally:
        os.environ.pop("BRAIN_COCKPIT_ROOT", None)
        importlib.reload(watcher)


def test_watcher_falls_back_to_cwd_without_the_env_var(tmp_path, monkeypatch):
    """The launchd path is unchanged: no env var, WorkingDirectory == the repo."""
    monkeypatch.delenv("BRAIN_COCKPIT_ROOT", raising=False)
    watcher = importlib.reload(importlib.import_module("pipeline.watcher"))
    assert watcher.DB_PATH == Path("events.db")
    assert watcher.HEARTBEAT_PATH == Path(".watcher-heartbeat")


def test_api_state_paths_and_app_paths_resolve_to_different_roots(container):
    from api.main import create_app
    app_root, state_root, _ = container
    app = create_app(root=state_root, app_root=app_root)

    assert app.state.root == state_root
    assert app.state.app_root == app_root
    # the frontend ships with the code; looking for it on the state mount is
    # what made the container serve "the cockpit isn't built yet"
    assert (app_root / "web" / "dist" / "index.html").exists()
    assert not (state_root / "web").exists()
    # and it is actually mounted, rather than the no-frontend fallback route
    assert any(getattr(r, "name", "") == "app" for r in app.routes)


def test_api_reads_the_same_db_the_watcher_writes(container):
    from api.main import create_app
    from pipeline import watcher
    app_root, state_root, _ = container
    create_app(root=state_root, app_root=app_root)
    reloaded = importlib.reload(watcher)
    try:
        api_db = state_root / reloaded.DB_NAME
        assert api_db == reloaded.DB_PATH, "API and watcher must agree on events.db"
    finally:
        os.environ.pop("BRAIN_COCKPIT_ROOT", None)
        importlib.reload(reloaded)


def test_build_probes_read_code_from_the_app_root(container):
    from api import build_status
    app_root, state_root, _ = container
    config = type("C", (), {"raw": {}, "vault_path": str(state_root)})()
    ok, detail = build_status._probe_file_exists(
        app_root, {"path": "api/main.py"}, config, None)
    assert ok, detail
    missing, _ = build_status._probe_file_exists(
        state_root, {"path": "api/main.py"}, config, None)
    assert not missing, "the state mount holds no code — this is the container case"


def test_missing_checks_json_is_a_plain_english_error(tmp_path):
    from api import build_status
    with pytest.raises(build_status.ManifestError) as excinfo:
        build_status.run_probes(tmp_path, None, tmp_path / "events.db")
    envelope = excinfo.value.envelope
    assert set(envelope) == {"what", "cause", "todo"}
    assert "checks.json" in envelope["todo"]
    assert "Traceback" not in envelope["what"]
