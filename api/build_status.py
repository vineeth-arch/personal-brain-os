"""Build tracker probes (Pass B): reality IS the checklist — no manual
checkboxes anywhere. checks.json is the manifest; every probe inspects the
actual system (files, config, binaries, git, the vault, events.db) and each
unfinished item carries one plain-English next_action.

env_var_set reports booleans only — key VALUES never leave the server."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
from pathlib import Path

log = logging.getLogger("api")

PROBE_TIMEOUT = 5


def _dotted(raw: dict, field: str):
    node = raw
    for part in field.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _probe_file_exists(root: Path, item: dict, *_):
    p = root / item["path"]
    return p.exists(), (f"{item['path']} exists." if p.exists() else f"{item['path']} isn't there yet.")


def _probe_endpoint_ok(root: Path, item: dict, *_):
    # this code runs inside the API — answering at all IS the probe
    return True, "You're reading this through the running API."


def _probe_config_field_set(root: Path, item: dict, config, _db):
    if config is None:
        return False, "config.json doesn't exist yet."
    value = _dotted(config.raw, item["field"])
    ok = bool(value)
    return ok, (f"{item['field']} is set." if ok else f"{item['field']} is empty in config.json.")


def _probe_binary_runs(root: Path, item: dict, config, _db):
    if config is None:
        return False, "config.json doesn't exist yet."
    binary = _dotted(config.raw, item["config_field"]) or ""
    if not binary:
        return False, "No binary path in config.json yet."
    try:
        proc = subprocess.run([binary, "--help"], capture_output=True, timeout=PROBE_TIMEOUT)
        ok = proc.returncode == 0
        return ok, ("The binary runs." if ok else f"The binary exited with code {proc.returncode}.")
    except FileNotFoundError:
        return False, f"{binary} doesn't exist on this machine."
    except Exception as e:
        return False, f"The binary couldn't be run ({type(e).__name__})."


def _probe_env_var_set(root: Path, item: dict, *_):
    names = item.get("any_of") or [item["name"]]
    set_names = [n for n in names if os.environ.get(n)]
    ok = bool(set_names)
    return ok, (f"{', '.join(set_names)} set." if ok else "None of the keys are in the environment.")


def _probe_git_log_contains(root: Path, item: dict, config, _db):
    if config is None:
        return False, "config.json doesn't exist yet."
    try:
        out = subprocess.run(
            ["git", "-C", str(config.vault_path), "log", "--format=%s"],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT)
        ok = out.returncode == 0 and item["needle"] in out.stdout
        return ok, ("Found in the vault's git history." if ok
                    else "Nothing matching in the vault's git history yet.")
    except Exception:
        return False, "The vault's git history couldn't be read."


def _probe_vault_query(root: Path, item: dict, config, db_path: Path):
    if config is None:
        return False, "config.json doesn't exist yet."
    query = item["query"]
    if query == "capture_inbox_empty":
        inbox = Path(config.inbox_path)
        if not inbox.is_dir():
            return False, "The capture inbox folder doesn't exist yet."
        n = sum(1 for p in inbox.iterdir() if p.is_file() and not p.name.startswith("."))
        return n == 0, ("The capture inbox is empty." if n == 0
                        else f"{n} file(s) still waiting in the capture inbox.")
    if query == "inbox_max":
        folder = Path(config.vault_path) / "00-Inbox"
        n = len(list(folder.glob("*.md"))) if folder.is_dir() else 0
        ok = n <= item.get("max", 10)
        return ok, f"{n} note(s) in 00-Inbox."
    if query == "notes_min":
        vault = Path(config.vault_path)
        n = sum(1 for p in vault.rglob("*.md")
                if "_System" not in p.parts and "00-Inbox" not in p.parts) if vault.is_dir() else 0
        ok = n >= item.get("min", 1)
        return ok, f"{n} note(s) in the vault."
    if query == "processed_min":
        n = _processed_count(db_path)
        ok = n >= item.get("min", 1)
        return ok, (f"{n} capture(s) fully processed." if n else "No capture has been processed yet.")
    if query == "clock_started":
        inbox_ok, _ = _probe_vault_query(root, {"query": "capture_inbox_empty"}, config, db_path)
        processed = _processed_count(db_path)
        ok = inbox_ok and processed >= 1
        return ok, ("The 30-day clock is running." if ok
                    else "Waiting on: " + " and ".join(
                        ([] if inbox_ok else ["an empty capture inbox"])
                        + ([] if processed else ["the first processed capture"])) + ".")
    return False, f"Unknown vault query '{query}'."


def _processed_count(db_path: Path) -> int:
    if not Path(db_path).exists():
        return 0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return conn.execute(
                "SELECT COUNT(DISTINCT file) FROM events WHERE stage='archive' AND status='ok'"
            ).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


_PROBES = {
    "file_exists": _probe_file_exists,
    "endpoint_ok": _probe_endpoint_ok,
    "config_field_set": _probe_config_field_set,
    "binary_runs": _probe_binary_runs,
    "env_var_set": _probe_env_var_set,
    "git_log_contains": _probe_git_log_contains,
    "vault_query": _probe_vault_query,
}


def run_probes(root: Path, config, db_path: Path) -> list[dict]:
    manifest = json.loads((root / "checks.json").read_text())
    results = []
    for item in manifest["items"]:
        probe = _PROBES.get(item["type"])
        if probe is None:
            done, detail = False, f"Unknown probe type '{item['type']}'."
        else:
            try:
                done, detail = probe(root, item, config, db_path)
            except Exception:
                log.exception("probe %s failed", item["id"])
                done, detail = False, "The check itself failed — see the server log."
        results.append({
            "id": item["id"], "label": item["label"], "phase": item["phase"],
            "done": done, "detail": detail,
            "next_action": None if done else item["next_action"],
        })
    return results
