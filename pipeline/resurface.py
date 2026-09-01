"""Hybrid resurfacing (Pass R, B6). Disposable state — the `resurface` table
in events.db only tracks what's been shown and when, so the picker doesn't
repeat itself too soon. Deleting events.db loses nothing durable: a fresh
resurface table just means every note becomes eligible again (CLAUDE.md §1).
The candidate scan below is intentionally self-contained (its own tiny
frontmatter reader) rather than importing api/notes.py — this package never
imports from api/, matching the existing convention (see
pipeline/watcher.py::drain_tick's lazy-import comment)."""
from __future__ import annotations

import random
import re
import sqlite3
from datetime import date
from pathlib import Path

from . import route

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_WIKILINK_ID_RE = re.compile(r"\[\[([\w-]+)\]\]")
EXCERPT_CHARS = 300
RESURFACE_FOLDERS = ("musing", "learning", "insight")


def _read_note(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Minimal flat-frontmatter reader — same shape as api/notes.py's, kept
    separate on purpose (see module docstring)."""
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    fm: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            fm[k.strip()] = v
    return fm, parts[2]


def _resolve_title(vault: Path, note_id: str) -> str | None:
    """Best-effort title lookup by immutable id, for resolving a stamped
    `related: [[id]]` link into a human title. Bounded, best-effort: any
    read/parse failure or a dangling id (the linked note was moved/deleted)
    just means no related_title — never an error."""
    for path in sorted(vault.rglob("*.md")):
        rel_parts = path.relative_to(vault).parts
        if not rel_parts or rel_parts[0] in ("raw", "_System"):
            continue
        text = _read_note(path)
        if not text:
            continue
        fm, _ = _parse_frontmatter(text)
        if fm.get("id") == note_id:
            return _DATE_PREFIX_RE.sub("", path.stem)
    return None


def _candidates(vault: Path) -> list[dict]:
    folders = [vault / route.TYPE_FOLDER[t] for t in RESURFACE_FOLDERS]
    out = []
    for folder in folders:
        if not folder.is_dir():
            continue
        for p in sorted(folder.glob("*.md")):
            text = _read_note(p)
            if not text or not text.startswith("---\n"):
                continue
            fm, body = _parse_frontmatter(text)
            note_id = fm.get("id", "")
            if not note_id:
                continue
            paragraph = next((seg.strip() for seg in body.split("\n\n") if seg.strip()), "")
            created_str = fm.get("created", "")
            related_title = None
            related_raw = fm.get("related", "")
            m = _WIKILINK_ID_RE.search(related_raw)
            if m:
                related_title = _resolve_title(vault, m.group(1))
            out.append({
                "id": note_id,
                "title": _DATE_PREFIX_RE.sub("", p.stem),
                "file": str(p.relative_to(vault)),
                "excerpt": paragraph[:EXCERPT_CHARS],
                "type": fm.get("type", "musing"),
                "created": created_str,
                "related_title": related_title,
            })
    return out


def pick(vault: Path, db_path: Path, k: int = 1, *,
        now: date | None = None, rng: random.Random | None = None) -> list[dict]:
    """Up to k candidates, weighted toward older notes, respecting a
    spaced-repetition cooldown that widens each time a note is shown again.
    Never picks a note whose response is 'archived'. Records last_shown/shows
    for whatever it NEWLY picks — this is what prevents a SINGLE call from
    picking a note twice when k>1.

    Idempotent within a calendar day: a note already stamped last_shown==now
    (by an earlier call today — GET /api/resurfaced is polled on window focus
    by the client, and the primary "Open in Obsidian" button itself triggers
    a focus round-trip) is returned as-is, WITHOUT re-stamping or re-rolling
    the random draw, ahead of any new picks. That makes repeated same-day
    calls return a stable set instead of replacing the cards out from under
    the user and burning through the eligible pool. Only a genuinely new pick
    (one not already shown today) advances state."""
    now = now or date.today()
    rng = rng or random.Random()
    candidates = _candidates(vault)
    if not candidates:
        return []

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS resurface ("
            "note_id TEXT PRIMARY KEY, last_shown TEXT, "
            "shows INTEGER NOT NULL DEFAULT 0, response TEXT)")
        rows = {
            row[0]: {"last_shown": row[1], "shows": row[2], "response": row[3]}
            for row in conn.execute("SELECT note_id, last_shown, shows, response FROM resurface")
        }

        # Notes already shown today go first, unchanged — archived still wins
        # even over "already shown today" (an archive tap on a displayed card
        # must stick immediately, not wait for tomorrow's cooldown reset).
        today_iso = now.isoformat()
        already_today_ids = {
            note_id for note_id, state in rows.items()
            if state["last_shown"] == today_iso and state["response"] != "archived"
        }
        # candidates is already in a deterministic scan order (folder, then
        # filename) — filter through it rather than iterating the set
        # directly, so which ones get kept when there are more than k is
        # stable rather than hash-order dependent
        picked: list[dict] = [c for c in candidates if c["id"] in already_today_ids][:k]

        remaining_k = k - len(picked)
        if remaining_k <= 0:
            return picked

        eligible = []
        for c in candidates:
            if c["id"] in already_today_ids:
                continue  # already in `picked` above
            state = rows.get(c["id"], {"last_shown": None, "shows": 0, "response": None})
            if state["response"] == "archived":
                continue
            if state["last_shown"]:
                last = date.fromisoformat(state["last_shown"])
                cooldown_days = 7 * (state["shows"] + 1)
                if (now - last).days < cooldown_days:
                    continue
            try:
                age_days = max(1, (now - date.fromisoformat(c["created"])).days)
            except ValueError:
                age_days = 1  # unparseable created date — still eligible, just unweighted
            eligible.append((c, state, age_days))

        if not eligible:
            return picked

        pool = list(eligible)
        for _ in range(min(remaining_k, len(pool))):
            weights = [age for _, _, age in pool]
            chosen = rng.choices(pool, weights=weights, k=1)[0]
            pool.remove(chosen)
            c, state, _age = chosen
            picked.append(c)
            new_shows = state["shows"] + 1
            conn.execute(
                "INSERT INTO resurface (note_id, last_shown, shows, response) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(note_id) DO UPDATE SET last_shown=excluded.last_shown, "
                "shows=excluded.shows",
                (c["id"], now.isoformat(), new_shows, state["response"]))
        conn.commit()
        return picked
    finally:
        conn.close()


def record_response(db_path: Path, note_id: str, action: str) -> None:
    """action: 'connect' | 'act' | 'archive'. 'connect' and 'act' just stamp
    the response value (informational — cooldown math above only special-cases
    'archived'); 'archive' is the one that removes a note from future picks."""
    response = {"connect": "connected", "act": "acted", "archive": "archived"}.get(action)
    if response is None:
        return
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS resurface ("
            "note_id TEXT PRIMARY KEY, last_shown TEXT, "
            "shows INTEGER NOT NULL DEFAULT 0, response TEXT)")
        conn.execute(
            "INSERT INTO resurface (note_id, last_shown, shows, response) "
            "VALUES (?, NULL, 0, ?) "
            "ON CONFLICT(note_id) DO UPDATE SET response=excluded.response",
            (note_id, response))
        conn.commit()
    finally:
        conn.close()
