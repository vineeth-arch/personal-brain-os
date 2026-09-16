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

import hashlib
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pipeline import frontmatter, merge

log = logging.getLogger("pipeline")

GIT_TIMEOUT = 30
REMOTE_REF_TEMPLATE = "refs/remotes/vaultsync/{branch}"
RESOLVABLE_FOLDERS = {"07-People", "11-Companies", "12-Conversations"}
# 06-Todos is deliberately excluded — daily todo files have no frontmatter
# (pipeline/todos.py writes plain checkbox lines), so resolve_conflict's
# frontmatter.parse() would always bail there anyway; excluding it up
# front avoids implying a resolution path that doesn't exist.


def _synth_marker(text: str) -> str:
    """A stable marker for a line/suggestion that has no <!-- --> of its
    own (e.g. an owner's free-text edit, or a Fill-collision suggestion) —
    merge.append_line's idempotency check needs a non-empty marker or it's
    a silent no-op ("" is a substring of every string)."""
    return f"<!-- vs:{hashlib.sha1(text.encode()).hexdigest()[:10]} -->"


@dataclass
class SyncResult:
    status: str            # ok | resolved | no-remote | not-a-repo | conflict | unrelated-histories | error
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


def resolve_conflict(ours: str, theirs: str, base: str) -> str | None:
    """Three-way text resolve for one conflicted note, using C1's merge
    rules (pipeline/merge.py) instead of picking a side. Returns None when
    the file isn't resolvable this way (unparseable frontmatter on either
    side) — the caller aborts the rebase exactly as before in that case."""
    base_fm, base_body = frontmatter.parse(base)
    ours_fm, ours_body = frontmatter.parse(ours)
    theirs_fm, theirs_body = frontmatter.parse(theirs)
    if not ours_fm or not theirs_fm:
        return None

    base_lines = {ln for ln in base_body.splitlines() if ln.strip()}
    ours_lines = {ln for ln in ours_body.splitlines() if ln.strip()}
    theirs_lines = {ln for ln in theirs_body.splitlines() if ln.strip()}
    if not base_lines <= ours_lines or not base_lines <= theirs_lines:
        return None  # one side removed or modified an existing line — not safe to auto-merge

    today = date.today().isoformat()
    result_fm = dict(base_fm)
    suggestions: list[str] = []
    for key in set(ours_fm) | set(theirs_fm):
        ours_value = ours_fm.get(key)
        if ours_value not in (None, ""):
            result_fm, s = merge.apply_field(result_fm, key, ours_value,
                                              source="vault", origin="human", today=today)
            if s:
                suggestions.append(s)
        theirs_value = theirs_fm.get(key)
        if theirs_value not in (None, ""):
            result_fm, s = merge.apply_field(result_fm, key, theirs_value,
                                              source="vault", origin="human", today=today)
            if s:
                suggestions.append(s)

    result_body = ours_body
    for side_body in (theirs_body,):
        for line in side_body.splitlines():
            stripped = line.strip()
            if not stripped or stripped in base_body or stripped in result_body:
                continue
            marker_match = re.search(r"(<!--\s*\S+:\S+\s*-->)\s*$", stripped)
            marker = marker_match.group(1) if marker_match else _synth_marker(stripped)
            if marker in result_body:
                continue
            section = _section_for_line(side_body, line) or "Updates"
            text = stripped[: marker_match.start()].rstrip() if marker_match else stripped
            result_body = merge.append_line(result_body, section, text, marker)

    for suggestion in suggestions:
        marker = _synth_marker(suggestion)
        if marker not in result_body:
            result_body = merge.append_line(result_body, "Updates", suggestion, marker)

    return frontmatter.serialize(result_fm, result_body)


def _section_for_line(body: str, line: str) -> str | None:
    idx = body.find(line)
    if idx == -1:
        return None
    heading_idx = body.rfind("\n## ", 0, idx)
    if heading_idx == -1:
        return None
    heading_line = body[heading_idx + 1:body.index("\n", heading_idx + 1)]
    return heading_line.removeprefix("## ").strip()


def _try_resolve_conflicts(vault: Path) -> bool:
    """Attempt to auto-resolve every conflicted file via C1's merge rules.
    Returns True (rebase continued) only when EVERY conflicted path is
    under RESOLVABLE_FOLDERS and resolves cleanly — a single unresolvable
    file aborts the whole attempt, leaving the caller to `rebase --abort`
    exactly as before. Never partially applies."""
    listing = _git(vault, ["diff", "--name-only", "--diff-filter=U"])
    if listing.returncode != 0:
        return False
    conflicted = [line for line in listing.stdout.splitlines() if line.strip()]
    if not conflicted:
        return False
    if not all(Path(p).suffix == ".md" and Path(p).parts and Path(p).parts[0] in RESOLVABLE_FOLDERS
               for p in conflicted):
        return False

    resolutions: dict[str, str] = {}
    for rel_path in conflicted:
        base = _show_stage(vault, rel_path, 1)
        ours = _show_stage(vault, rel_path, 2)
        theirs = _show_stage(vault, rel_path, 3)
        if base is None or ours is None or theirs is None:
            return False
        resolved = resolve_conflict(ours, theirs, base)
        if resolved is None:
            return False
        resolutions[rel_path] = resolved

    for rel_path, content in resolutions.items():
        (vault / rel_path).write_text(content, encoding="utf-8")
        _git(vault, ["add", rel_path])
    continue_result = _git(vault, ["rebase", "--continue"])
    return continue_result.returncode == 0


def _show_stage(vault: Path, rel_path: str, stage: int) -> str | None:
    result = _git(vault, ["show", f":{stage}:{rel_path}"])
    return result.stdout if result.returncode == 0 else None


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

    was_resolved = False
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
            try:
                was_resolved = _try_resolve_conflicts(vault)
            except Exception:
                was_resolved = False
            if not was_resolved:
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
    status = "resolved" if was_resolved else "ok"
    detail = "Vault synced (auto-resolved a conflict)." if was_resolved else "Vault synced."
    return SyncResult(status, detail, ahead=ahead, behind=behind)
