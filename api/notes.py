"""Vault note operations: review queue, approve/retype, capture, resurface.

Reads and writes follow SCHEMA-REFERENCE.md exactly — flat frontmatter as
written by pipeline/route.py, immutable id, locked vocabularies imported from
the pipeline (never re-declared here). Every vault write is followed by a git
commit with a descriptive message (CLAUDE.md §3 — revertible AI-era changes).
"""
from __future__ import annotations

import logging
import os
import random
import re
import sqlite3
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path

from pipeline import classify, route

log = logging.getLogger("api")

_CONFIDENCE_RE = re.compile(r"confidence=([0-9.]+)")
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")

EXCERPT_CHARS = 300


# ---- frontmatter ------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Flat top-level keys only (list values are indented and skipped), matching
    the format route.build_frontmatter writes."""
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    fm: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, parts[2]


def _restamp(text: str, new_type: str, new_status: str) -> str:
    """Rewrite only the column-0 type:/status: lines inside the frontmatter block."""
    head, sep, body = text.partition("\n---\n")
    lines = []
    for line in head.splitlines():
        if line.startswith("type:"):
            lines.append(f"type: {new_type}")
        elif line.startswith("status:"):
            lines.append(f"status: {new_status}")
        else:
            lines.append(line)
    return "\n".join(lines) + sep + body


# ---- git --------------------------------------------------------------------

def git_commit_vault(vault: Path, message: str) -> None:
    """Commit the vault after an API write. A git hiccup never fails the request
    (the watcher's never-abort precedent) — the write itself succeeded."""
    try:
        inside = subprocess.run(
            ["git", "-C", str(vault), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True)
        if inside.returncode != 0:
            log.info("vault is not a git repo — skipping commit (%s)", message)
            return
        subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(vault), "commit", "-m", message, "--allow-empty"],
                       check=True, capture_output=True)
    except Exception:
        log.exception("vault git commit failed (%s)", message)


# ---- review queue -----------------------------------------------------------

def _confidence_map(db_path: Path) -> dict[str, float]:
    """note filename → classifier confidence, joined from events.db.

    Route events log 'wrote <name>.md' (basenames, ', '-joined); the nearest
    preceding classify event on the same source file carries
    'type=X confidence=0.62 by=llm'. Later route events win.
    """
    if not db_path.exists():
        return {}
    out: dict[str, float] = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT r.message,"
                " (SELECT c.message FROM events c"
                "  WHERE c.file = r.file AND c.stage = 'classify' AND c.id < r.id"
                "  ORDER BY c.id DESC LIMIT 1)"
                " FROM events r WHERE r.stage = 'route' AND r.status = 'ok' ORDER BY r.id"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        log.exception("confidence join failed")
        return {}
    for route_msg, classify_msg in rows:
        if not route_msg or not classify_msg:
            continue
        m = _CONFIDENCE_RE.search(classify_msg)
        if not m:
            continue
        conf = float(m.group(1))
        for name in route_msg.removeprefix("wrote ").split(", "):
            out[name.strip()] = conf
    return out


def list_review(vault: Path, db_path: Path) -> list[dict]:
    inbox_dir = vault / route.INBOX_FOLDER
    if not inbox_dir.is_dir():
        return []
    confidences = _confidence_map(db_path)
    items = []
    for path in sorted(inbox_dir.glob("*.md")):
        fm, body = parse_frontmatter(path.read_text())
        if fm.get("status") != "needs-review":
            continue
        items.append({
            "id": fm.get("id", ""),
            "file": f"{route.INBOX_FOLDER}/{path.name}",
            "title": _DATE_PREFIX_RE.sub("", path.stem),
            "excerpt": body.strip()[:EXCERPT_CHARS],
            "suggested_type": fm.get("type", "musing"),
            "confidence": confidences.get(path.name, 0.5),
            "created": fm.get("created", ""),
        })
    return items


def count_review(vault: Path) -> int:
    """The Today badge must equal the triage queue length — same scan."""
    inbox_dir = vault / route.INBOX_FOLDER
    if not inbox_dir.is_dir():
        return 0
    n = 0
    for path in inbox_dir.glob("*.md"):
        fm, _ = parse_frontmatter(path.read_text())
        if fm.get("status") == "needs-review":
            n += 1
    return n


def approve(vault: Path, note_id: str, new_type: str) -> str:
    """Restamp type/status and move the note to its folder. Returns the
    vault-relative destination. Raises LookupError if the id isn't in review."""
    inbox_dir = vault / route.INBOX_FOLDER
    target: Path | None = None
    text = ""
    if inbox_dir.is_dir():
        for path in inbox_dir.glob("*.md"):
            text = path.read_text()
            fm, _ = parse_frontmatter(text)
            if fm.get("id") == note_id and fm.get("status") == "needs-review":
                target = path
                break
    if target is None:
        raise LookupError(note_id)

    new_status = route.STATUS_INITIAL.get(new_type, "active")
    dest_dir = vault / route.TYPE_FOLDER[new_type]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / target.name
    i = 1
    while dest.exists():
        i += 1
        dest = dest_dir / f"{target.stem}-{i}{target.suffix}"

    dest.write_text(_restamp(text, new_type, new_status))
    target.unlink()
    git_commit_vault(vault, f"api: filed {note_id} as {new_type}")
    return str(dest.relative_to(vault))


# ---- capture ----------------------------------------------------------------

def _slug(text: str) -> str:
    words = " ".join(text.split()[:6])
    slug = re.sub(r"[^\w\s-]", "", words.replace("#", "")).strip()
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug[:60] or "note"


def capture(inbox: Path, text: str, tag: str | None) -> str:
    """Write a text capture the intake stage will parse (source=manual, tag
    free-routes). Returns the id the pipeline will mint for this note —
    filename time is minute-precision, so seconds are always 00."""
    inbox.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d-%H%M")
    name = _slug(text)
    suffix = f" #{tag}" if tag else ""
    path = inbox / f"{stamp} {name}{suffix}.md"
    i = 1
    while path.exists():
        i += 1
        # collision suffix goes on the NAME portion — after the #tag it would
        # break the free tag-route (intake._NAME_RE binds the tag last)
        path = inbox / f"{stamp} {name}-{i}{suffix}.md"

    # atomic-ish write so a concurrently polling watcher never sees a half file
    fd, tmp = tempfile.mkstemp(dir=inbox, prefix=".capture-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text.rstrip() + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return now.strftime("%Y%m%d%H%M") + "00"


def valid_tag(tag: str | None) -> bool:
    return tag is None or tag in classify.TAG_TO_TYPE


# ---- resurface ----------------------------------------------------------------

def resurface(vault: Path) -> dict | None:
    """One deterministic pick per day from the wiki (musings/learnings/insights
    all live in 02-Wiki per route.TYPE_FOLDER)."""
    wiki = vault / route.TYPE_FOLDER["musing"]
    if not wiki.is_dir():
        return None
    candidates = sorted(p for p in wiki.glob("*.md") if p.read_text().startswith("---\n"))
    if not candidates:
        return None
    pick = random.Random(date.today().toordinal()).choice(candidates)
    fm, body = parse_frontmatter(pick.read_text())
    paragraph = next((p.strip() for p in body.split("\n\n") if p.strip()), "")
    return {
        "id": fm.get("id", ""),
        "title": _DATE_PREFIX_RE.sub("", pick.stem),
        "file": str(pick.relative_to(vault)),
        "excerpt": paragraph[:EXCERPT_CHARS],
        "type": fm.get("type", "musing"),
        "created": fm.get("created", ""),
    }
