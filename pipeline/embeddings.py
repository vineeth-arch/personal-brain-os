"""Semantic search index (Pass I, I1). embeddings.db is DISPOSABLE — delete
it and lose nothing, the vault stays the only source of truth (CLAUDE.md §1).
Rebuilt incrementally by embeddings_tick; a cold rebuild just costs one full
re-embed of the vault on the next tick, nothing is ever lost either way.

OpenAI's text-embedding-3-small via a stdlib urllib POST — same "own minimal
HTTP call, no openai/requests dependency" pattern as
pipeline/transcribe.py::OpenAITranscriber and pipeline/dex.py (CLAUDE.md §7).
Cosine similarity is computed in plain Python: a linear scan over every row,
fine at personal-vault scale (comfortably under 50k notes) — there is no ANN
index here, and there should not be one at this scale."""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import urllib.error
import urllib.request
from array import array
from datetime import date
from pathlib import Path
from typing import Callable

log = logging.getLogger("pipeline")

MODEL = "text-embedding-3-small"
ENDPOINT = "https://api.openai.com/v1/embeddings"
BATCH_SIZE = 64
EXCERPT_CHARS = 1500
_EXCLUDED_FOLDERS = {"raw", "_System"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    title TEXT NOT NULL,
    vector BLOB NOT NULL
);
"""


def api_key() -> str:
    """Env only, never config.json (CLAUDE.md §7)."""
    return os.environ.get("OPENAI_API_KEY", "")


def configured() -> bool:
    return bool(api_key())


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _default_post(texts: list[str]) -> list[list[float]] | None:
    key = api_key()
    if not key:
        return None
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps({"model": MODEL, "input": texts}).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log.info("embeddings request failed: %s", e)
        return None
    try:
        return [item["embedding"] for item in data["data"]]
    except (KeyError, TypeError):
        return None


def _read_note(path: Path) -> tuple[dict, str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.startswith("---\n"):
        return None
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return None
    fm: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, parts[2]


def _title(path: Path, fm: dict) -> str:
    t = fm.get("title", "").strip()
    return t if t else path.stem


def _to_bytes(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def _from_bytes(blob: bytes) -> array:
    a = array("f")
    a.frombytes(blob)
    return a


def _cosine(a, b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def reindex(vault: Path, db_path: Path, *,
           post: Callable[[list[str]], list[list[float]] | None] | None = None) -> dict | None:
    """Incremental — a file already indexed at its current mtime is skipped
    entirely (no re-embed, no API call); a new or mtime-changed file gets
    (re-)embedded; a row whose file has vanished from the vault is deleted.
    Returns {"added": n, "removed": m}, or None immediately when there's no
    OPENAI_API_KEY at all — the caller (embeddings_tick) is the one that
    decides whether/how often to log that, not this function."""
    if not configured():
        return None
    post = post or _default_post

    conn = _connect(db_path)
    try:
        existing = {row[0]: (row[1], row[2]) for row in
                   conn.execute("SELECT id, path, mtime_ns FROM embeddings")}
        seen_ids: set[str] = set()
        pending: list[tuple[str, str, int, str, str]] = []

        for path in sorted(Path(vault).rglob("*.md")):
            rel_parts = path.relative_to(vault).parts
            if not rel_parts or rel_parts[0] in _EXCLUDED_FOLDERS:
                continue
            if any(part.startswith(".") for part in rel_parts):
                continue
            parsed = _read_note(path)
            if parsed is None:
                continue
            fm, body = parsed
            note_id = fm.get("id", "")
            if not note_id:
                continue
            seen_ids.add(note_id)
            mtime_ns = path.stat().st_mtime_ns
            if existing.get(note_id) == (str(path), mtime_ns):
                continue
            title = _title(path, fm)
            text = f"{title}\n\n{body[:EXCERPT_CHARS]}"
            pending.append((note_id, str(path), mtime_ns, title, text))

        added = 0
        for i in range(0, len(pending), BATCH_SIZE):
            batch = pending[i:i + BATCH_SIZE]
            vectors = post([b[4] for b in batch])
            if vectors is None or len(vectors) != len(batch):
                log.info("embeddings batch failed or size mismatch — skipping this batch")
                continue
            for (note_id, path_str, mtime_ns, title, _text), vector in zip(batch, vectors):
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings (id, path, mtime_ns, title, vector) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (note_id, path_str, mtime_ns, title, _to_bytes(vector)))
                added += 1
        conn.commit()

        removed = 0
        for stale_id in set(existing) - seen_ids:
            conn.execute("DELETE FROM embeddings WHERE id = ?", (stale_id,))
            removed += 1
        conn.commit()
    finally:
        conn.close()

    return {"added": added, "removed": removed}


def embed_text(text: str,
               post: Callable[[list[str]], list[list[float]] | None] | None = None) -> list[float] | None:
    """One-off embed for a search query or a related-note lookup — same
    endpoint/model/key as reindex, just a batch of one. None when there's no
    key or the call fails; every caller already treats None as "fall back
    to the non-semantic path", never as an error to surface."""
    if not configured():
        return None
    post = post or _default_post
    vectors = post([text])
    return vectors[0] if vectors else None


def query(db_path: Path, q_vector: list[float], k: int) -> list[tuple[str, str, str, float]]:
    """(id, title, path, score) for the top-k cosine matches, best first.
    Empty list when the db doesn't exist yet or holds nothing — this is the
    normal keyless/cold-start case, not an error."""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id, title, path, vector FROM embeddings").fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    scored = [(row[0], row[1], row[2], _cosine(_from_bytes(row[3]), q_vector)) for row in rows]
    scored.sort(key=lambda r: r[3], reverse=True)
    return scored[:k]


def embeddings_tick(config, events) -> None:
    """Registered in watcher.run_loop, after drain_tick. Silent no-op (at
    most one quiet events row per day) when there's no OPENAI_API_KEY — a
    cockpit with no embeddings configured pays nothing for this tick beyond
    one cheap env check. Never raises — same "a tick may fail, the loop may
    not" contract as every other tick in this package."""
    if not configured():
        today_key = f"embed-nokey-{date.today().isoformat()}"
        if not events.reminder_fired(today_key):
            events.log(str(config.vault_path), "embed", "ok", message="skipped: no key")
            events.mark_reminder(today_key)
        return
    db_path = events.db_path.with_name("embeddings.db")
    try:
        result = reindex(Path(config.vault_path), db_path)
    except Exception:
        log.exception("embeddings tick failed — retrying at the next poll")
        return
    if result and (result["added"] or result["removed"]):
        events.log(str(config.vault_path), "embed", "ok",
                  message=f"added={result['added']} removed={result['removed']}")
