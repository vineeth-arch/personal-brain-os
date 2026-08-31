"""Pass H1 — pipeline/vaultsync.py. fetch/rebase/push exercised against a
REAL local bare repo (file:// remote — no token needed, no network); the
token-never-on-disk guarantee is tested separately on the pure URL builder,
per the plan's own split (a live https endpoint isn't needed to prove
either property)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import vaultsync


def _run(*args, cwd=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r


def _init_vault(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run("init", "-q", cwd=path)
    _run("config", "user.email", "t@t", cwd=path)
    _run("config", "user.name", "t", cwd=path)
    return path


def _clone(bare: Path, dest: Path) -> Path:
    _run("clone", "-q", f"file://{bare}", str(dest))
    _run("config", "user.email", "t@t", cwd=dest)
    _run("config", "user.name", "t", cwd=dest)
    return dest


def config(vault_sync: dict | None = None):
    return SimpleNamespace(raw={"vault_sync": vault_sync} if vault_sync is not None else {})


@pytest.fixture
def bare_remote(tmp_path):
    bare = tmp_path / "remote.git"
    _run("init", "-q", "--bare", str(bare))
    # pin the bare repo's default branch to "main" regardless of this
    # machine's git init.defaultBranch — otherwise a later `git clone` looks
    # for whatever HEAD happens to point at (often "master"), finds no such
    # ref (everything here pushes explicitly to "main"), and silently starts
    # an unrelated, unborn branch instead of checking "main" out.
    _run("symbolic-ref", "HEAD", "refs/heads/main", cwd=bare)
    return bare


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # every test starts with a clean slate regardless of the real shell env
    monkeypatch.delenv("VAULT_GIT_REMOTE", raising=False)
    monkeypatch.delenv("VAULT_GIT_TOKEN", raising=False)
    monkeypatch.delenv("VAULT_GIT_BRANCH", raising=False)


# ---- config resolution --------------------------------------------------------

def test_no_config_at_all_returns_none():
    assert vaultsync.remote_config(config()) is None


def test_env_remote_wins_over_config_json(monkeypatch):
    monkeypatch.setenv("VAULT_GIT_REMOTE", "https://env.example/repo.git")
    cfg = config({"remote": "https://config.example/repo.git", "branch": "trunk"})
    remote, _token, branch = vaultsync.remote_config(cfg)
    assert remote == "https://env.example/repo.git"
    assert branch == "trunk"  # env branch unset → config.json's is the fallback


def test_env_branch_wins_over_config_json_branch(monkeypatch):
    monkeypatch.setenv("VAULT_GIT_REMOTE", "https://x.example/repo.git")
    monkeypatch.setenv("VAULT_GIT_BRANCH", "prod")
    cfg = config({"remote": "https://ignored.example/repo.git", "branch": "trunk"})
    _remote, _token, branch = vaultsync.remote_config(cfg)
    assert branch == "prod"


def test_config_json_fallback_when_env_absent():
    cfg = config({"remote": "https://config.example/repo.git"})
    remote, _token, branch = vaultsync.remote_config(cfg)
    assert remote == "https://config.example/repo.git"
    assert branch == "main"  # default when nothing names one


def test_token_is_always_env_only_never_config_json(monkeypatch):
    monkeypatch.setenv("VAULT_GIT_TOKEN", "ghp_real_token")
    cfg = config({"remote": "https://x.example/repo.git", "token": "ghp_should_be_ignored"})
    _remote, token, _branch = vaultsync.remote_config(cfg)
    assert token == "ghp_real_token"


# ---- token URL builder (pure function — the "never touches disk" guarantee) ---

def test_authed_url_embeds_token_as_x_access_token():
    url = vaultsync._authed_url("https://github.com/me/vault.git", "ghp_secret123")
    assert url == "https://x-access-token:ghp_secret123@github.com/me/vault.git"


def test_authed_url_unchanged_without_a_token():
    assert vaultsync._authed_url("https://github.com/me/vault.git", "") == \
        "https://github.com/me/vault.git"


def test_authed_url_leaves_non_http_remotes_unchanged():
    assert vaultsync._authed_url("file:///tmp/bare.git", "secret") == "file:///tmp/bare.git"
    assert vaultsync._authed_url("git@github.com:me/vault.git", "secret") == \
        "git@github.com:me/vault.git"


def test_authed_url_does_not_double_up_existing_credentials():
    url = vaultsync._authed_url("https://user:pass@github.com/me/vault.git", "secret")
    assert url == "https://user:pass@github.com/me/vault.git"


# ---- sync against a real (local, bare) remote ---------------------------------

def test_sync_pushes_the_first_commit_to_an_empty_remote(tmp_path, monkeypatch, bare_remote):
    monkeypatch.setenv("VAULT_GIT_REMOTE", f"file://{bare_remote}")
    vault = _init_vault(tmp_path / "vault")
    (vault / "note.md").write_text("hello", encoding="utf-8")
    _run("add", "-A", cwd=vault)
    _run("commit", "-q", "-m", "first note", cwd=vault)

    result = vaultsync.sync(vault, config())
    assert result.status == "ok"
    assert result.ahead == 0 and result.behind == 0

    log = subprocess.run(["git", "--git-dir", str(bare_remote), "log", "-1", "--format=%s", "main"],
                         capture_output=True, text=True)
    assert log.stdout.strip() == "first note"


def test_sync_round_trips_a_commit_made_elsewhere(tmp_path, monkeypatch, bare_remote):
    monkeypatch.setenv("VAULT_GIT_REMOTE", f"file://{bare_remote}")

    vault_a = _init_vault(tmp_path / "vault-a")
    (vault_a / "a.md").write_text("from A", encoding="utf-8")
    _run("add", "-A", cwd=vault_a)
    _run("commit", "-q", "-m", "from A", cwd=vault_a)
    assert vaultsync.sync(vault_a, config()).status == "ok"

    vault_b = _clone(bare_remote, tmp_path / "vault-b")
    (vault_b / "b.md").write_text("from B", encoding="utf-8")
    _run("add", "-A", cwd=vault_b)
    _run("commit", "-q", "-m", "from B", cwd=vault_b)
    result = vaultsync.sync(vault_b, config())
    assert result.status == "ok"
    assert (vault_b / "a.md").exists(), "machine B never pulled A's note"
    assert (vault_b / "b.md").exists(), "machine B's own note didn't survive the rebase"

    # A syncs again and must pick up B's note too
    result_a2 = vaultsync.sync(vault_a, config())
    assert result_a2.status == "ok"
    assert (vault_a / "b.md").exists(), "machine A never pulled B's note on its next sync"


def test_conflict_aborts_rebase_and_leaves_vault_untouched(tmp_path, monkeypatch, bare_remote):
    monkeypatch.setenv("VAULT_GIT_REMOTE", f"file://{bare_remote}")

    vault_a = _init_vault(tmp_path / "vault-a")
    (vault_a / "shared.md").write_text("original", encoding="utf-8")
    _run("add", "-A", cwd=vault_a)
    _run("commit", "-q", "-m", "seed", cwd=vault_a)
    assert vaultsync.sync(vault_a, config()).status == "ok"

    vault_b = _clone(bare_remote, tmp_path / "vault-b")

    (vault_a / "shared.md").write_text("A's version", encoding="utf-8")
    _run("add", "-A", cwd=vault_a)
    _run("commit", "-q", "-m", "A edits", cwd=vault_a)
    assert vaultsync.sync(vault_a, config()).status == "ok"

    (vault_b / "shared.md").write_text("B's conflicting version", encoding="utf-8")
    _run("add", "-A", cwd=vault_b)
    _run("commit", "-q", "-m", "B edits", cwd=vault_b)

    result = vaultsync.sync(vault_b, config())
    assert result.status == "conflict"
    assert "resolve" in result.detail.lower() or "untouched" in result.detail.lower()

    status = subprocess.run(["git", "status", "--porcelain"], cwd=vault_b,
                            capture_output=True, text=True).stdout
    assert status.strip() == "", f"the rebase left the working tree dirty: {status!r}"
    assert (vault_b / "shared.md").read_text(encoding="utf-8") == "B's conflicting version"
    assert not (vault_b / ".git" / "rebase-merge").exists()
    assert not (vault_b / ".git" / "rebase-apply").exists()


def test_unrelated_histories_are_refused_with_guidance(tmp_path, monkeypatch, bare_remote):
    monkeypatch.setenv("VAULT_GIT_REMOTE", f"file://{bare_remote}")

    seed = _init_vault(tmp_path / "seed")
    (seed / "existing.md").write_text("already there", encoding="utf-8")
    _run("add", "-A", cwd=seed)
    _run("commit", "-q", "-m", "seed", cwd=seed)
    _run("push", "-q", f"file://{bare_remote}", "HEAD:main", cwd=seed)

    vault = _init_vault(tmp_path / "vault")  # never cloned from the remote
    (vault / "mine.md").write_text("my own history", encoding="utf-8")
    _run("add", "-A", cwd=vault)
    _run("commit", "-q", "-m", "my own start", cwd=vault)

    result = vaultsync.sync(vault, config())
    assert result.status == "unrelated-histories"
    assert "push" in result.detail.lower()
    assert (vault / "mine.md").read_text(encoding="utf-8") == "my own history"
    assert not (vault / "existing.md").exists()


def test_not_a_repo_is_reported_honestly(tmp_path, monkeypatch, bare_remote):
    monkeypatch.setenv("VAULT_GIT_REMOTE", f"file://{bare_remote}")
    vault = tmp_path / "vault"
    vault.mkdir()
    assert vaultsync.sync(vault, config()).status == "not-a-repo"


def test_sync_never_configures_a_persistent_remote(tmp_path, monkeypatch, bare_remote):
    """Never `git remote add` — the whole point is that a fetch/push URL
    carrying a token is a command argument, not something written to disk."""
    monkeypatch.setenv("VAULT_GIT_REMOTE", f"file://{bare_remote}")
    vault = _init_vault(tmp_path / "vault")
    (vault / "note.md").write_text("x", encoding="utf-8")
    _run("add", "-A", cwd=vault)
    _run("commit", "-q", "-m", "x", cwd=vault)
    assert vaultsync.sync(vault, config()).status == "ok"
    git_config = (vault / ".git" / "config").read_text(encoding="utf-8")
    assert "[remote" not in git_config, ".git/config gained a persisted remote"


def test_sync_never_raises_on_an_unexpected_internal_error(tmp_path, monkeypatch):
    monkeypatch.setattr(vaultsync, "_sync", lambda vault, config: (_ for _ in ()).throw(RuntimeError("boom")))
    result = vaultsync.sync(tmp_path, config())
    assert result.status == "error"


# ---- ahead_behind --------------------------------------------------------------

def test_ahead_behind_before_any_sync_is_zero(tmp_path):
    vault = _init_vault(tmp_path / "vault")
    assert vaultsync.ahead_behind(vault, "main") == (0, 0)


def test_ahead_behind_reflects_unsynced_local_commits(tmp_path, monkeypatch, bare_remote):
    monkeypatch.setenv("VAULT_GIT_REMOTE", f"file://{bare_remote}")
    vault = _init_vault(tmp_path / "vault")
    (vault / "note.md").write_text("x", encoding="utf-8")
    _run("add", "-A", cwd=vault)
    _run("commit", "-q", "-m", "x", cwd=vault)
    assert vaultsync.sync(vault, config()).status == "ok"

    (vault / "note2.md").write_text("y", encoding="utf-8")
    _run("add", "-A", cwd=vault)
    _run("commit", "-q", "-m", "not yet synced", cwd=vault)
    assert vaultsync.ahead_behind(vault, "main") == (1, 0)
