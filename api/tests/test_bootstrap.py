"""Pass 11 — first-boot bootstrap. A fresh server (empty /data volume) must
come up on its own: a config the selfcheck accepts, the four folders created,
a strong token minted once. These tests are the reason `docker compose up` on
a brand-new VPS doesn't end in the boot refusal."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from api import selfcheck

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap.py"


def _run(root: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Run bootstrap in --docker (non-interactive) mode against a temp root.

    COCKPIT_VAULT_DIR / COCKPIT_INBOX_DIR stand in for the container's bind
    mounts so the test never touches the real /vault and /inbox; archive and
    failed already hang off --root, as they do off /data in the container.
    """
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root),
           "COCKPIT_VAULT_DIR": str(root / "vault"),
           "COCKPIT_INBOX_DIR": str(root / "inbox")}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(BOOTSTRAP), "--docker", "--root", str(root)],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))


def test_first_boot_writes_a_usable_config(tmp_path):
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    config = json.loads((tmp_path / "config.json").read_text())

    # the four folders are configured AND created — the selfcheck refuses the
    # boot on any missing one, which is what this whole script exists to avoid
    for key in ("vault_path", "inbox_path", "archive_path", "failed_path"):
        assert Path(config[key]).is_dir(), f"{key} was configured but not created"
    assert config["archive_path"] == str(tmp_path / "archive")
    assert config["failed_path"] == str(tmp_path / "failed")

    # a strong token, minted (not the empty example value)
    token = config["api"]["auth_token"]
    assert len(token) >= 32 and token != ""

    # printed ONCE, clearly enough to copy out of `docker compose logs`
    assert "ACCESS TOKEN" in proc.stdout
    assert proc.stdout.count(token) == 1


def test_token_is_unique_per_boot(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _run(a)
    _run(b)
    ta = json.loads((a / "config.json").read_text())["api"]["auth_token"]
    tb = json.loads((b / "config.json").read_text())["api"]["auth_token"]
    assert ta != tb


def test_second_boot_never_overwrites_an_existing_config(tmp_path):
    _run(tmp_path)
    original = (tmp_path / "config.json").read_text()
    proc = _run(tmp_path)
    assert proc.returncode == 0
    # the token (i.e. the user's password) must survive a container restart
    assert (tmp_path / "config.json").read_text() == original
    assert "already exists" in proc.stdout


@pytest.mark.parametrize("key, expected", [("sk-test-key", "openai"), ("", "whispercpp")])
def test_engine_follows_the_openai_key(tmp_path, key, expected):
    """G2: cloud transcription when a key is present (fast on a small VPS),
    local whisper.cpp otherwise — a missing key must never block the boot."""
    _run(tmp_path, {"OPENAI_API_KEY": key} if key else {})
    config = json.loads((tmp_path / "config.json").read_text())
    assert config["transcription"]["engine"] == expected
    # whisper stays pre-wired either way, so the toggle works with no edits
    assert config["transcription"]["whispercpp"]["model_path"].endswith(".bin")


def test_generated_config_passes_the_boot_selfcheck(tmp_path):
    """The real gate: selfcheck.run() is what refuses the boot, so a
    freshly-bootstrapped root must satisfy it with no human edits at all."""
    _run(tmp_path)
    result = selfcheck.run(tmp_path)
    assert result["ok"], result["problems"]
    assert {c["id"]: c["ok"] for c in result["checks"]}["auth-token"] is True


def test_vault_is_git_initialised_for_reviewable_writes(tmp_path):
    """Constitution rule 3 — AI writes are committed so they stay revertible.
    git_commit_vault() skips quietly on a non-repo, so first boot sets it up."""
    _run(tmp_path)
    inside = subprocess.run(
        ["git", "-C", str(tmp_path / "vault"), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True)
    assert inside.stdout.strip() == "true"
