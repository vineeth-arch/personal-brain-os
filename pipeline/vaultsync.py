"""Pass H1 — vault git-sync: the island fix (F1).

A Railway deploy's vault lives at `/data/vault`, which no Obsidian ever
opens and whose git repo has no remote — captures land there and stay there.
This module pushes/pulls the vault's own git history (never note *content*
through any other channel) to a private GitHub repo, so the same notes exist
on every machine.

Config is env-first, because the Railway volume's config.json can't be
edited remotely but service variables can:
    VAULT_GIT_REMOTE   https URL of the private repo (required to sync)
    VAULT_GIT_TOKEN    fine-grained PAT, env-only (CLAUDE.md §7)
    VAULT_GIT_BRANCH   defaults to "main"
`config.json`'s `vault_sync: {remote, branch}` block is the fallback for a
Mac/compose deploy that can edit its own config — the TOKEN is never read
from config.json, only ever from the environment.

The token is assembled into the fetch/push URL per invocation and passed as
a command ARGUMENT, never written to .git/config, so it never touches disk.
Sequence: fetch → rebase local onto the remote → push (never force). A
conflict aborts the rebase and leaves the vault exactly as it was — this
never leaves a vault half-migrated.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("pipeline")

GIT_TIMEOUT = 30
REMOTE_REF_TEMPLATE = "refs/remotes/vaultsync/{branch}"


@dataclass
class SyncResult:
    status: str            # ok | no-remote | not-a-repo | conflict | unrelated-histories | error
    detail: str = ""        # plain-English — goes in the event log and the Integrations card
    ahead: int = 0
    behind: int = 0


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def remote_config(config) -> tuple[str, str, str] | None:
    """(remote_url, token, branch), or None when sync isn't configured at
    all. Env wins for remote and branch INDEPENDENTLY — config.json's
    vault_sync block is each one's own fallback, not an all-or-nothing
    switch, so an env remote can still pick up a branch named in config.json.
    The token is ALWAYS env-only."""
    block = (getattr(config, "raw", {}) or {}).get("vault_sync") or {}
    remote = _env("VAULT_GIT_REMOTE") or str(block.get("remote") or "").strip()
    if not remote:
        return None
    branch = _env("VAULT_GIT_BRANCH") or str(block.get("branch") or "").strip() or "main"
    return remote, _env("VAULT_GIT_TOKEN"), branch


def _authed_url(remote: str, token: str) -> str:
    """The token folded into the URL as a fetch/push argument — never
    persisted. A remote with no token (a local file:// remote in tests, or
    one that's already authenticated some other way) is used unchanged."""
    if not token or "://" not in remote:
        return remote
    scheme, rest = remote.split("://", 1)
    if scheme not in ("http", "https"):
        return remote
    if "@" in rest.split("/", 1)[0]:
        return remote  # the URL already carries credentials — don't double them up
    return f"{scheme}://x-access-token:{token}@{rest}"


def _git(vault: Path, args: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", "-C", str(vault), *args],
                          capture_output=True, text=True, timeout=GIT_TIMEOUT, env=env)


def is_repo(vault: Path) -> bool:
    return _git(vault, ["rev-parse", "--is-inside-work-tree"]).returncode == 0


def ahead_behind(vault: Path, branch: str) -> tuple[int, int]:
    """(local-only, remote-only) commits vs the last-synced remote-tracking
    ref — cheap, no network. (0, 0) before the first successful sync."""
    ref = REMOTE_REF_TEMPLATE.format(branch=branch)
    exists = _git(vault, ["rev-parse", "--verify", "--quiet", ref])
    if exists.returncode != 0:
        return 0, 0
    r = _git(vault, ["rev-list", "--left-right", "--count", f"HEAD...{ref}"])
    parts = r.stdout.split()
    if r.returncode != 0 or len(parts) != 2:
        return 0, 0
    ahead, behind = parts
    return int(ahead), int(behind)


def _snapshot_local_changes(vault: Path) -> None:
    """Commit whatever is sitting uncommitted so the rebase below has a clean
    tree — same idea as watcher._git_commit_vault's pre-batch commit. A
    no-op (nothing to commit) is expected and silently ignored."""
    _git(vault, ["add", "-A"])
    _git(vault, ["commit", "-q", "-m", "vault sync: local snapshot"])


def sync(vault: Path, config) -> SyncResult:
    """One sync pass. Never raises — called from the watcher's --loop tick
    and --backlog batches, same never-abort contract as enrich.retry_pending."""
    try:
        return _sync(Path(vault), config)
    except Exception:
        log.exception("vault sync failed")
        return SyncResult("error", "The vault sync itself hit an unexpected error — see the server log.")


def _sync(vault: Path, config) -> SyncResult:
    cfg = remote_config(config)
    if cfg is None:
        return SyncResult("no-remote", "Vault sync isn't configured (no VAULT_GIT_REMOTE).")
    remote, token, branch = cfg
    if not is_repo(vault):
        return SyncResult("not-a-repo", "The vault isn't a git repository yet — run `git init` in it first.")

    url = _authed_url(remote, token)
    ref = REMOTE_REF_TEMPLATE.format(branch=branch)

    fetch = _git(vault, ["fetch", url, f"+{branch}:{ref}"])
    remote_is_empty = False
    if fetch.returncode != 0:
        combined = (fetch.stdout + fetch.stderr).lower()
        if "couldn't find remote ref" in combined or "couldn't find remote branch" in combined:
            # a brand new remote with nothing pushed yet — the very first
            # sync has nothing to rebase onto, just push straight to it
            remote_is_empty = True
        else:
            return SyncResult("error", f"Couldn't reach the vault's remote repo: {fetch.stderr.strip()[:300]}")

    _snapshot_local_changes(vault)

    if not remote_is_empty:
        # `git rebase` — unlike `git merge` — does NOT refuse unrelated
        # histories on its own; it just replays every local commit onto the
        # remote's, silently splicing two independently-started vaults
        # together. Check for a common ancestor ourselves, before rebasing.
        if _git(vault, ["merge-base", ref, "HEAD"]).returncode != 0:
            return SyncResult(
                "unrelated-histories",
                "This vault's git history and the remote's don't share a common start. Push "
                "THIS vault to the (empty) remote first, then clone it everywhere else — "
                "don't try to merge two independently-started histories.")

        rebase = _git(vault, ["rebase", ref])
        if rebase.returncode != 0:
            _git(vault, ["rebase", "--abort"])
            return SyncResult(
                "conflict",
                "The vault and the remote both changed the same note. The rebase was aborted "
                "and your vault is untouched — pull normally, resolve the conflict by hand "
                "(in Obsidian or git), then sync again.")

    push = _git(vault, ["push", url, f"HEAD:{branch}"])
    if push.returncode != 0:
        combined = (push.stdout + push.stderr).lower()
        if "non-fast-forward" in combined or "fetch first" in combined or "stale info" in combined:
            return SyncResult("conflict", "The remote moved again mid-sync — try once more.")
        return SyncResult("error", f"Couldn't push to the vault's remote repo: {push.stderr.strip()[:300]}")

    # the push landed exactly what we rebased onto — the tracking ref is
    # simply HEAD now, no need for a second fetch to know we're even
    _git(vault, ["update-ref", ref, "HEAD"])
    ahead, behind = ahead_behind(vault, branch)
    return SyncResult("ok", "Vault synced.", ahead=ahead, behind=behind)
