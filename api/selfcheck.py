"""Startup self-check (Pass 5). Structural problems — a config that can't be
read, folders that aren't there or aren't writable, an events.db that can't
open — refuse the boot with a numbered plain-English list. Softer conditions
(no whisper binary yet, ntfy unconfigured, no model keys) are informational
booleans only: they belong to the Integrations cards and the Build screen,
never to a boot refusal."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from pipeline import config as config_mod


def run(root: Path) -> dict:
    """-> {ok, problems: [{what,cause,todo}], checks: [{id,label,ok,detail}]}"""
    root = Path(root)
    config_path = root / "config.json"
    problems: list[dict] = []
    checks: list[dict] = []

    def check(check_id: str, label: str, ok: bool, detail: str) -> None:
        checks.append({"id": check_id, "label": label, "ok": bool(ok), "detail": detail})

    # 1. config.json exists and parses
    config = None
    if not config_path.exists():
        problems.append({
            "what": "The server has no configuration yet.",
            "cause": f"{config_path} doesn't exist.",
            "todo": "Copy config.example.json to config.json and fill in the paths.",
        })
    else:
        try:
            config = config_mod.load(config_path)
        except (json.JSONDecodeError, ValueError):
            problems.append({
                "what": "The server configuration couldn't be read.",
                "cause": "config.json exists but isn't valid JSON.",
                "todo": "Fix config.json (compare with config.example.json), then start again.",
            })
        except KeyError as e:
            problems.append({
                "what": "The server configuration is incomplete.",
                "cause": f"config.json is missing the required {e} setting.",
                "todo": "Fill it in (compare with config.example.json), then start again.",
            })
        except Exception:
            problems.append({
                "what": "The server configuration couldn't be read.",
                "cause": "config.json exists but something in it is broken.",
                "todo": "Compare it with config.example.json and fix the difference.",
            })
    check("config", "config.json readable", config is not None,
          "Parsed." if config is not None else "Missing or unreadable.")

    # 2. the four folders exist and are writable
    if config is not None:
        for key, path in (("vault", config.vault_path), ("inbox", config.inbox_path),
                          ("archive", config.archive_path), ("failed", config.failed_path)):
            p = Path(path) if path else None
            ok = bool(p) and p.is_dir() and os.access(p, os.W_OK)
            check(f"path-{key}", f"{key} folder writable", ok,
                  str(p) if ok else f"{p} is missing or not writable.")
            if not ok:
                problems.append({
                    "what": f"The {key} folder can't be used.",
                    "cause": f"{key}_path in config.json points to {p}, which doesn't exist "
                             "or isn't writable (disk unmounted, or a typo in the path?).",
                    "todo": f"Create the folder or fix {key}_path in config.json, then start again.",
                })

    # 3. events.db opens (a missing db is fine — the first pipeline run creates it)
    db_path = root / "events.db"
    if db_path.exists():
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            try:
                conn.execute("SELECT 1 FROM sqlite_master")
            finally:
                conn.close()
            check("events-db", "events.db opens", True, "Opens fine.")
        except sqlite3.Error:
            check("events-db", "events.db opens", False, "Exists but won't open.")
            problems.append({
                "what": "The pipeline's event log can't be opened.",
                "cause": f"{db_path} exists but sqlite can't read it — it may be corrupt.",
                "todo": "Move events.db aside (it's disposable — no knowledge lives in it) "
                        "and start again; the pipeline recreates it.",
            })
    else:
        check("events-db", "events.db opens", True, "Not created yet — the first run makes it.")

    # 4. informational booleans (never boot failures) for the Build screen
    if config is not None:
        check("auth-token", "API access token set",
              bool((config.raw.get("api") or {}).get("auth_token")),
              "Set." if (config.raw.get("api") or {}).get("auth_token")
              else "Empty — every request will be rejected until it's set.")
        check("whisper", "whisper.cpp configured", bool(config.whispercpp_binary),
              "Binary path set." if config.whispercpp_binary
              else "No binary path yet — transcription can't run.")
        check("ntfy", "ntfy push configured", bool(config.ntfy_url and config.ntfy_topic),
              "Configured." if (config.ntfy_url and config.ntfy_topic)
              else "Not configured — no failure pushes.")
        check("model-key", "a classification model key present",
              any(os.environ.get(k) for k in
                  ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY")),
              "At least one key in the environment."
              if any(os.environ.get(k) for k in
                     ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"))
              else "No model key — untagged captures will park in triage.")

    return {"ok": not problems, "problems": problems, "checks": checks}


def refusal_message(problems: list[dict]) -> str:
    """The numbered plain-English list printed when the API refuses to start."""
    lines = ["Brain Cockpit can't start. Fix these first:"]
    for i, p in enumerate(problems, start=1):
        lines.append(f"  {i}. {p['what']} {p['cause']} → {p['todo']}")
    return "\n".join(lines)
