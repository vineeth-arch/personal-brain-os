"""Pass H1 — POST /api/vault/sync and the Integrations "Vault git-sync" card.
Reuses the hermetic harness + git-initialised tmp vault from test_api.py.
The remote is a real local bare repo (file:// — no token, no network),
exactly like pipeline/tests/test_vaultsync.py's own fixtures."""
from __future__ import annotations

import subprocess

from api.tests.test_api import Server, env  # noqa: F401


def _bare_remote(tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=bare, check=True)
    return bare


def test_card_is_unconfigured_by_default(env, monkeypatch):
    root, _, _, _ = env
    monkeypatch.delenv("VAULT_GIT_REMOTE", raising=False)
    with Server(root) as s:
        _, body = s.req("GET", "/api/integrations")
        card = next(c for c in body["cards"] if c["id"] == "vault-git-sync")
        assert card["badge"] == "Not configured"
        assert card["status"] == "unknown"


def test_sync_route_refuses_when_unconfigured(env, monkeypatch):
    root, _, _, _ = env
    monkeypatch.delenv("VAULT_GIT_REMOTE", raising=False)
    with Server(root) as s:
        code, body = s.req("POST", "/api/vault/sync", {})
        assert code == 400 and set(body["error"]) == {"what", "cause", "todo"}


def test_sync_route_pushes_to_a_real_remote_and_updates_the_card(env, monkeypatch, tmp_path):
    root, vault, _, _ = env
    bare = _bare_remote(tmp_path)
    monkeypatch.setenv("VAULT_GIT_REMOTE", f"file://{bare}")
    monkeypatch.delenv("VAULT_GIT_TOKEN", raising=False)

    # the env fixture's vault is already a git repo but has no commit yet —
    # give it one so there is something to sync
    (vault / "note.md").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-qm", "seed"], check=True, capture_output=True)

    with Server(root) as s:
        code, body = s.req("POST", "/api/vault/sync", {})
        assert code == 200 and body["ok"] is True and body["status"] == "ok"
        assert body["ahead"] == 0 and body["behind"] == 0

        _, integ = s.req("GET", "/api/integrations")
        card = next(c for c in integ["cards"] if c["id"] == "vault-git-sync")
        assert card["status"] == "ok" and card["badge"] == "Up to date"
        assert card["meta"]["branch"] == "main"

    log = subprocess.run(["git", "--git-dir", str(bare), "log", "-1", "--format=%s", "main"],
                         capture_output=True, text=True)
    assert log.stdout.strip() == "seed"


def test_sync_route_reports_a_conflict_honestly(env, monkeypatch, tmp_path):
    root, vault, _, _ = env
    bare = _bare_remote(tmp_path)
    monkeypatch.setenv("VAULT_GIT_REMOTE", f"file://{bare}")
    monkeypatch.delenv("VAULT_GIT_TOKEN", raising=False)

    (vault / "shared.md").write_text("original", encoding="utf-8")
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-qm", "seed"], check=True, capture_output=True)

    with Server(root) as s:
        assert s.req("POST", "/api/vault/sync", {})[0] == 200  # first sync: clean push

        # a change lands on the remote from "elsewhere"...
        elsewhere = tmp_path / "elsewhere"
        subprocess.run(["git", "clone", "-q", f"file://{bare}", str(elsewhere)], check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=elsewhere, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=elsewhere, check=True)
        (elsewhere / "shared.md").write_text("changed remotely", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=elsewhere, check=True)
        subprocess.run(["git", "commit", "-qm", "remote edit"], cwd=elsewhere, check=True)
        subprocess.run(["git", "push", "-q", f"file://{bare}", "HEAD:main"], cwd=elsewhere, check=True)

        # ...while this vault ALSO changes the same file
        (vault / "shared.md").write_text("changed locally", encoding="utf-8")
        subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(vault), "commit", "-qm", "local edit"],
                       check=True, capture_output=True)

        code, body = s.req("POST", "/api/vault/sync", {})
        assert code == 200 and body["ok"] is False and body["status"] == "conflict"
        assert (vault / "shared.md").read_text(encoding="utf-8") == "changed locally", \
            "a failed sync must never touch the working tree"

        _, integ = s.req("GET", "/api/integrations")
        card = next(c for c in integ["cards"] if c["id"] == "vault-git-sync")
        assert card["status"] == "warn" and card["badge"] == "Sync failed"
