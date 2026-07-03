"""Server-side health checks for the Integrations screen, plus the validated
config writer shared by PUT /api/config and POST /api/integrations/engine.

Checks are cached (60s) per app instance; ?fresh=1 re-runs them, and the two
mutating endpoints bust the cache so a card never contradicts the user's own
action. Key VALUES never leave this module — presence booleans only."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from pipeline import errors as perrors

log = logging.getLogger("api")

CACHE_SECONDS = 60
HEARTBEAT_LIMIT_MIN = 20
GIT_WARN_HOURS = 24
TEST_CALL_TIMEOUT = 5

ENGINES = ("whispercpp", "openai")

_iso = lambda dt: dt.isoformat(timespec="seconds")

LINK_DEFS = [
    ("dex", "Dex", "link", "Your personal CRM for people and relationships."),
    ("gmail", "Gmail", "mail", "Email inbox."),
    ("gcal", "Google Calendar", "calendar", "Your calendar."),
    ("caldiy", "cal.diy", "calendar", "Scheduling links."),
    ("n8n", "n8n", "server", "Your automation workflows."),
    ("zima", "ZimaOS admin", "server", "Home-server dashboard."),
    ("supabase", "Supabase", "database", "Outreach cockpit database console."),
]


class ConfigError(Exception):
    def __init__(self, what: str, cause: str, todo: str):
        self.envelope = {"what": what, "cause": cause, "todo": todo}
        super().__init__(what)


# ---- config read / validated write -------------------------------------------

def safe_config(config) -> dict:
    """The safe subset only. Presence booleans for keys — never values, never
    the auth token (CLAUDE.md §7)."""
    return {
        "engine": config.engine,
        "confidence_threshold": config.confidence_threshold,
        "ntfy_url": config.ntfy_url,
        "ntfy_topic": config.ntfy_topic,
        "keys": {
            "anthropic": bool(config.anthropic_key),
            "openai": bool(config.openai_key),
        },
    }


def write_config(config_path: Path, config, changes: dict) -> None:
    """Atomic, validated update of the safe settings. Unknown keys in
    config.json (links, api, paths) survive untouched."""
    engine = changes.get("engine")
    if engine is not None and engine not in ENGINES:
        raise ConfigError(
            "Couldn't switch the transcription engine.",
            "The request didn't name a known engine (whispercpp or openai).",
            "Pick one of the two engines and try again.",
        )
    if engine == "openai" and not config.openai_key:
        raise ConfigError(
            "Can't switch to cloud transcription.",
            "OPENAI_API_KEY is not set on the server, so the OpenAI engine can't run.",
            "export OPENAI_API_KEY=... in the server's shell, or stay on whispercpp.",
        )
    threshold = changes.get("confidence_threshold")
    if threshold is not None and not (0.0 <= float(threshold) <= 1.0):
        raise ConfigError(
            "Couldn't change the confidence threshold.",
            f"{threshold} is outside the valid range.",
            "Pick a value between 0 and 1 (0.7 is the tested default).",
        )

    raw = json.loads(config_path.read_text())
    if engine is not None:
        raw.setdefault("transcription", {})["engine"] = engine
    if threshold is not None:
        raw.setdefault("classification", {})["confidence_threshold"] = float(threshold)
    if changes.get("ntfy_topic") is not None:
        raw.setdefault("ntfy", {})["topic"] = str(changes["ntfy_topic"])
    if changes.get("ntfy_url") is not None:
        raw.setdefault("ntfy", {})["url"] = str(changes["ntfy_url"])

    fd, tmp = tempfile.mkstemp(dir=config_path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(raw, f, indent=2)
            f.write("\n")
        os.replace(tmp, config_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ---- individual checks ---------------------------------------------------------

def _check_whisper(config) -> dict:
    active = config.engine == "whispercpp"
    binary = Path(config.whispercpp_binary) if config.whispercpp_binary else None
    model = Path(config.whispercpp_model) if config.whispercpp_model else None
    card = {
        "id": "transcription-whispercpp", "group": "health",
        "name": "Transcription — whisper.cpp", "icon": "waveform",
        "description": "Turns your voice memos into text, all on this machine.",
        "meta": {"engine_active": active, "model": model.name if model else ""},
    }
    problems = []
    if not binary or not binary.exists():
        problems.append("the binary path is missing or wrong")
    elif not os.access(binary, os.X_OK):
        problems.append("the binary isn't executable")
    if not model or not model.exists():
        problems.append("the model path is missing or wrong")
    if problems:
        card.update(
            status="problem", badge="Not ready",
            detail="Local transcription can't run yet.",
            error={
                "what": "Local transcription can't run.",
                "cause": f"In config.json, {' and '.join(problems)}.",
                "todo": "Fix transcription.whispercpp.binary_path / model_path, then Recheck.",
            })
    else:
        card.update(
            status="ok",
            badge="Ready · active" if active else "Ready",
            detail="Local transcription is ready"
                   + (" and is the engine in use." if active else " (not the active engine)."),
        )
    return card


def _test_call_anthropic(key: str) -> bool:
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=TEST_CALL_TIMEOUT) as resp:
        return resp.status == 200


def _test_call_openai(key: str) -> bool:
    req = urllib.request.Request(
        "https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=TEST_CALL_TIMEOUT) as resp:
        return resp.status == 200


def _check_key_service(card_id: str, name: str, icon: str, description: str,
                       key: str | None, active: bool | None, fresh: bool,
                       test_fn, state: dict) -> dict:
    card = {"id": card_id, "group": "health", "name": name, "icon": icon,
            "description": description, "meta": {"key_present": bool(key)}}
    if active is not None:
        card["meta"]["engine_active"] = active
    if not key:
        card.update(
            status="warn" if active else "unknown", badge="No key",
            detail="The key isn't set in the server's environment.",
            error={
                "what": f"{name} has no API key.",
                "cause": "The environment variable isn't set in the server's shell.",
                "todo": "export the key in the shell that runs the API, then Recheck.",
            })
        return card
    # test-call only on an explicit fresh recheck — free list endpoints, short
    # timeout, all failures degrade gracefully to a cached result
    if fresh:
        try:
            state[card_id] = ("ok", _iso(datetime.now())) if test_fn(key) else ("failed", _iso(datetime.now()))
        except Exception as e:
            log.info("%s test call failed: %s", card_id, e)
            state[card_id] = ("failed", _iso(datetime.now()))
    tested = state.get(card_id)
    if tested and tested[0] == "ok":
        card.update(status="ok", badge="Ready" + (" · active" if active else ""),
                    detail=f"Key present; last test call succeeded ({tested[1][11:16]}).")
    elif tested and tested[0] == "failed":
        card.update(status="warn", badge="Key set · test failed",
                    detail="The key is set but the last test call didn't succeed.",
                    error={
                        "what": f"{name} didn't answer a test call.",
                        "cause": "The key may be wrong or expired, or the network is blocked.",
                        "todo": "Check the key and the server's connection, then Recheck.",
                    })
    else:
        card.update(status="ok" if active else "unknown",
                    badge=("In use" if active else "Key set · untested"),
                    detail="Key present. Press Recheck to run a live test call.")
    return card


def _check_ntfy(config, state: dict) -> dict:
    card = {"id": "ntfy", "group": "health", "name": "ntfy push", "icon": "bell",
            "description": "Sends one push to your phone when a capture fails.",
            "meta": {"topic": config.ntfy_topic}}
    if not (config.ntfy_url and config.ntfy_topic):
        card.update(status="warn", badge="Not configured",
                    detail="No push destination is set.",
                    error={
                        "what": "Failure pushes can't reach your phone.",
                        "cause": "ntfy.url / ntfy.topic are empty in config.json.",
                        "todo": "Fill them in and subscribe to the topic in the ntfy app.",
                    })
    elif state.get("ntfy_tested"):
        card.update(status="ok", badge="Delivered",
                    detail="Test push sent — your phone should have buzzed.")
    else:
        card.update(status="unknown", badge="Untested",
                    detail=f"Configured for topic “{config.ntfy_topic}”. "
                           "Send a test push to confirm your phone receives it.")
    return card


def _check_vault_sync(config) -> dict:
    card = {"id": "vault-sync", "group": "health", "name": "Vault sync", "icon": "folder-sync",
            "description": "Where notes live and where new captures land."}
    missing = [label for label, p in (("inbox", config.inbox_path), ("vault", config.vault_path))
               if not Path(p).is_dir()]
    if missing:
        card.update(status="problem", badge="Unreachable",
                    detail=f"The {' and '.join(missing)} folder can't be reached.",
                    error={
                        "what": "The pipeline can't reach its folders.",
                        "cause": f"The configured {' and '.join(missing)} path doesn't exist "
                                 "(disk unmounted, or Syncthing not running?).",
                        "todo": "Check the paths in config.json and that the sync share is mounted.",
                    })
        return card
    # depth <= 2 scan — _System/ files are touched every run, so this stays a
    # faithful activity signal without walking a huge vault
    latest = 0.0
    for base in (Path(config.inbox_path), Path(config.vault_path)):
        try:
            with os.scandir(base) as it:
                for entry in it:
                    latest = max(latest, entry.stat().st_mtime)
                    if entry.is_dir():
                        with os.scandir(entry.path) as sub:
                            for e2 in sub:
                                latest = max(latest, e2.stat().st_mtime)
        except OSError:
            pass
    minutes = int((time.time() - latest) / 60) if latest else -1
    card.update(status="ok", badge="Reachable",
                detail="Inbox and vault are both reachable."
                       + (f" Last file activity {minutes} minutes ago." if minutes >= 0 else ""),
                meta={"minutes_since_activity": minutes})
    return card


def _check_git(config) -> dict:
    card = {"id": "git", "group": "health", "name": "Vault git backup", "icon": "git",
            "description": "Keeps every change to your notes revertible."}
    vault = str(config.vault_path)
    try:
        porcelain = subprocess.run(["git", "-C", vault, "status", "--porcelain"],
                                   capture_output=True, text=True, timeout=10)
        if porcelain.returncode != 0:
            card.update(status="warn", badge="No repo",
                        detail="The vault isn't a git repository yet.",
                        error={
                            "what": "Vault changes aren't being backed up.",
                            "cause": "The vault folder isn't a git repository.",
                            "todo": "Run `git init` in the vault and make one clean commit.",
                        })
            return card
        dirty = bool(porcelain.stdout.strip())
        head = subprocess.run(["git", "-C", vault, "log", "-1", "--format=%ct"],
                              capture_output=True, text=True, timeout=10)
        commit_age_h = -1
        if head.returncode == 0 and head.stdout.strip():
            commit_age_h = int((time.time() - int(head.stdout.strip())) / 3600)
        card["meta"] = {"dirty": dirty, "commit_age_hours": commit_age_h}
        if dirty and (commit_age_h < 0 or commit_age_h > GIT_WARN_HOURS):
            card.update(status="warn", badge=f"Uncommitted {commit_age_h}h" if commit_age_h >= 0 else "Uncommitted",
                        detail="The vault has uncommitted changes older than a day.",
                        error={
                            "what": "Your vault has changes that haven't been backed up in over a day.",
                            "cause": "The vault git repo has uncommitted edits and the last commit is old.",
                            "todo": "Approve or run a pass (API writes commit automatically), or commit manually.",
                        })
        elif dirty:
            card.update(status="ok", badge="Changes pending",
                        detail=f"Uncommitted changes present; last commit {commit_age_h}h ago.")
        else:
            card.update(status="ok", badge="Clean",
                        detail=(f"Vault committed {commit_age_h}h ago. Nothing uncommitted."
                                if commit_age_h >= 0 else "Vault is clean."))
    except Exception:
        log.exception("git check failed")
        card.update(status="unknown", badge="Unknown",
                    detail="The git check itself failed — see the server log.")
    return card


def _check_watcher(heartbeat_path: Path) -> dict:
    card = {"id": "watcher", "group": "health", "name": "Watcher", "icon": "pulse",
            "description": "The background process that picks up new captures."}
    if not heartbeat_path.exists():
        card.update(status="problem", badge="Never ran",
                    detail="The watcher has never checked in.",
                    error={
                        "what": "The pipeline has never checked in.",
                        "cause": "The watcher process hasn't been started on this machine.",
                        "todo": "Start it: `python3 -m pipeline --loop`. Then press Recheck.",
                    })
        return card
    try:
        stamp = datetime.fromisoformat(heartbeat_path.read_text().strip())
        age_min = int((datetime.now() - stamp).total_seconds() / 60)
    except ValueError:
        age_min = -1
    card["meta"] = {"heartbeat_age_min": age_min}
    if age_min < 0 or age_min > HEARTBEAT_LIMIT_MIN:
        card.update(status="problem", badge=f"Stale {age_min}m" if age_min >= 0 else "Unreadable",
                    detail=f"The watcher last checked in {age_min} minutes ago." if age_min >= 0
                           else "The heartbeat file couldn't be read.",
                    error={
                        "what": f"The pipeline hasn't checked in for {age_min} minutes."
                                if age_min >= 0 else "The watcher heartbeat is unreadable.",
                        "cause": "The watcher process isn't running, or it crashed on the last pass.",
                        "todo": "Restart it: `python3 -m pipeline --loop`. Then press Recheck.",
                    })
    else:
        card.update(status="ok", badge=f"Live {age_min}m",
                    detail=f"The watcher checked in {age_min} minute{'s' if age_min != 1 else ''} ago.")
    return card


def _link_cards(config) -> list[dict]:
    cards = [{
        "id": "obsidian", "group": "link", "name": "Obsidian", "icon": "obsidian",
        "description": "Open your vault in Obsidian.", "status": "unknown", "badge": None,
        "url": f"obsidian://open?vault={Path(config.vault_path).name}",
    }]
    links = config.raw.get("links", {}) or {}
    for key, name, icon, description in LINK_DEFS:
        url = links.get(key)
        if url:
            cards.append({"id": key, "group": "link", "name": name, "icon": icon,
                          "description": description, "status": "unknown", "badge": None,
                          "url": url})
    return cards


# ---- assembly + cache -----------------------------------------------------------

def build_payload(config, heartbeat_path: Path, fresh: bool, state: dict) -> dict:
    cards = [
        _check_whisper(config),
        _check_key_service(
            "transcription-openai", "Transcription — OpenAI", "cloud",
            "Cloud fallback that sends audio to OpenAI to transcribe.",
            config.openai_key, config.engine == "openai", fresh, _test_call_openai, state),
        _check_key_service(
            "claude", "Claude API", "brain",
            "Classifies untagged captures into the right note type.",
            config.anthropic_key, None, fresh, _test_call_anthropic, state),
        _check_ntfy(config, state),
        _check_vault_sync(config),
        _check_git(config),
        _check_watcher(heartbeat_path),
    ]
    cards.extend(_link_cards(config))
    return {
        "engine": config.engine,
        "generated_at": _iso(datetime.now()),
        "fresh": fresh,
        "cards": cards,
    }


def get_integrations(app_state, config, heartbeat_path: Path, fresh: bool) -> dict:
    """60s cache per app instance; fresh=1 (and any mutation via bust_cache)
    re-runs every check."""
    cache = app_state.integrations_cache
    now = time.monotonic()
    if not fresh and cache.get("payload") and now - cache.get("ts", 0) < CACHE_SECONDS:
        return cache["payload"]
    payload = build_payload(config, heartbeat_path, fresh, app_state.integrations_state)
    cache["ts"] = now
    cache["payload"] = payload
    return payload


def bust_cache(app_state) -> None:
    app_state.integrations_cache.clear()


def send_test_push(config) -> None:
    """errors.ntfy silently no-ops when unconfigured — reject that case first
    so 'sent' is truthful."""
    if not (config.ntfy_url and config.ntfy_topic):
        raise ConfigError(
            "The test push didn't go out.",
            "ntfy has no url/topic set in config.json.",
            "Fill in ntfy.url and ntfy.topic, then try again.",
        )
    perrors.ntfy(config.ntfy_url, config.ntfy_topic,
                 "Test push from Brain Cockpit — your phone is reachable.",
                 title="Brain Cockpit — test")
