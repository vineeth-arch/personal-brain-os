"""First-boot bootstrap — create a working config.json so a fresh install
boots instead of hitting the selfcheck refusal.

Two modes:
  --docker        non-interactive, container defaults (/vault, /inbox,
                  /data/archive, /data/failed, bundled whisper-cli). Used by
                  docker/entrypoint.sh on the very first `docker compose up`.
  (no flag)       interactive: asks for the vault and inbox paths, defaults
                  archive/failed to sibling folders next to the inbox.

Both modes mint a strong api.auth_token (printed ONCE — it is the password to
the cockpit), create the four folders, and never overwrite an existing
config.json. Stdlib only, per the constitution.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Container-path defaults, matching docker-compose.yml's volume mounts.
# vault/inbox are bind mounts (overridable for unusual layouts); archive and
# failed live inside the /data volume, so they hang off `root` — which IS
# /data in the container (BRAIN_COCKPIT_ROOT), and a temp dir under test.
DOCKER_VAULT = os.environ.get("COCKPIT_VAULT_DIR") or "/vault"
DOCKER_INBOX = os.environ.get("COCKPIT_INBOX_DIR") or "/inbox"
DOCKER_WHISPER_BINARY = "/usr/local/bin/whisper-cli"
WHISPER_MODEL_NAME = "ggml-small.en.bin"


def _example_config() -> dict:
    for candidate in (REPO_ROOT / "config.example.json",
                      Path("/app/config.example.json")):
        if candidate.exists():
            return json.loads(candidate.read_text())
    print("Could not find config.example.json — is the install incomplete?",
          file=sys.stderr)
    raise SystemExit(1)


def _write_config(root: Path, config: dict) -> Path:
    path = root / "config.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n")
    tmp.replace(path)
    return path


def _make_folders(config: dict) -> None:
    for key in ("vault_path", "inbox_path", "archive_path", "failed_path"):
        Path(config[key]).mkdir(parents=True, exist_ok=True)


def _ensure_vault_git(vault: str) -> None:
    """Make the vault a git repo (constitution rule 3: every AI batch write is
    committed first, so changes are reviewable). Best-effort — a vault synced
    from elsewhere may already be a repo, and no git at all is survivable
    (git_commit_vault skips quietly)."""
    if not shutil.which("git"):
        return
    try:
        inside = subprocess.run(
            ["git", "-C", vault, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True)
        if inside.returncode != 0:
            subprocess.run(["git", "-C", vault, "init", "-q"], check=True, capture_output=True)
        # commits need an identity; set a repo-local one only if none resolves
        ident = subprocess.run(["git", "-C", vault, "config", "user.email"],
                               capture_output=True, text=True)
        if not ident.stdout.strip():
            subprocess.run(["git", "-C", vault, "config", "user.email", "cockpit@localhost"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", vault, "config", "user.name", "Brain Cockpit"],
                           check=True, capture_output=True)
    except Exception as e:  # noqa: BLE001 — never block first boot on git
        print(f"note: couldn't set up git in the vault ({e}) — backups will be skipped.")


def bootstrap_docker(root: Path) -> Path:
    config = _example_config()
    model_path = root / "models" / WHISPER_MODEL_NAME
    config.update({
        "vault_path": DOCKER_VAULT,
        "inbox_path": DOCKER_INBOX,
        "archive_path": str(root / "archive"),
        "failed_path": str(root / "failed"),
    })
    # Prefer OpenAI cloud transcription when a key is already in the
    # container's environment (recommended for small VPS boxes — whisper.cpp
    # is CPU-bound and slow on 1-2 shared vCPUs); fall back to local
    # whisper.cpp so the boot never depends on a key being present. Either
    # engine is one click to switch later from Integrations.
    config["transcription"]["engine"] = (
        "openai" if os.environ.get("OPENAI_API_KEY") else "whispercpp"
    )
    config["transcription"]["whispercpp"] = {
        "binary_path": DOCKER_WHISPER_BINARY if Path(DOCKER_WHISPER_BINARY).exists() else "",
        # The model is NOT bundled (it's ~500 MB) — GO-LIVE.md has the one-line
        # download. Until then, audio captures park in failed/ with a clear error.
        "model_path": str(model_path),
    }
    token = secrets.token_urlsafe(32)
    config["api"]["auth_token"] = token
    _make_folders(config)
    _ensure_vault_git(config["vault_path"])
    model_path.parent.mkdir(parents=True, exist_ok=True)
    path = _write_config(root, config)
    print("First boot: generated a fresh config at", path)
    print(f"Vault: {config['vault_path']}   Inbox: {config['inbox_path']}"
          "   (mounted by docker-compose.yml)")
    print(f"Transcription engine: {config['transcription']['engine']}"
          + ("" if config["transcription"]["engine"] == "openai"
             else " (set OPENAI_API_KEY in .env for faster cloud transcription)"))
    print()
    print("=" * 62)
    print("  YOUR COCKPIT ACCESS TOKEN (paste it on the connect screen):")
    print(f"  {token}")
    print("=" * 62)
    print()
    print("It is also stored in config.json (api.auth_token) on the /data volume.")
    return path


def bootstrap_interactive(root: Path) -> Path:
    config = _example_config()

    def ask(prompt: str, default: str) -> str:
        answer = input(f"{prompt} [{default}]: ").strip()
        return answer or default

    home = Path.home()
    vault = ask("Path to your Obsidian vault", str(home / "Vault"))
    inbox = ask("Path to the capture inbox folder", str(home / "CaptureInbox"))
    inbox_parent = str(Path(inbox).parent)
    config["vault_path"] = vault
    config["inbox_path"] = inbox
    config["archive_path"] = ask("Archive folder (processed captures)",
                                 str(Path(inbox_parent) / "CaptureArchive"))
    config["failed_path"] = ask("Failed folder (captures that errored)",
                                str(Path(inbox_parent) / "CaptureFailed"))

    whisper = shutil.which("whisper-cli") or ""
    config["transcription"]["whispercpp"]["binary_path"] = \
        ask("whisper.cpp binary (empty to skip audio for now)", whisper)

    token = secrets.token_urlsafe(32)
    config["api"]["auth_token"] = token
    _make_folders(config)
    _ensure_vault_git(config["vault_path"])
    path = _write_config(root, config)
    print()
    print("Wrote", path, "and created the four folders.")
    print("Your cockpit access token (also in config.json):")
    print(f"  {token}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docker", action="store_true",
                        help="non-interactive container defaults")
    parser.add_argument("--root", default=os.environ.get("BRAIN_COCKPIT_ROOT", str(REPO_ROOT)),
                        help="where config.json lives (default: $BRAIN_COCKPIT_ROOT or repo root)")
    args = parser.parse_args()

    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    if (root / "config.json").exists():
        print(f"{root / 'config.json'} already exists — leaving it alone.")
        return 0

    if args.docker:
        bootstrap_docker(root)
    else:
        bootstrap_interactive(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
