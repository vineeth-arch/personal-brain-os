"""Full-text search over the vault, and answering questions from it.

## Why a second database, and why this doesn't break CLAUDE.md §1

§1 says the app never stores note content in its own database — if the database
is deleted, no knowledge is lost. This module builds an FTS5 index that does
hold note text, so the rule deserves a direct answer rather than a silence.

The index lives in its own file, `search.db`, never in `events.db`, and it is
**derived and disposable**: every row is a copy of a vault file, it is rebuilt
from the vault by `rebuild()` at any time, `POST /api/query/reindex` exposes
that rebuild to the user, and it is excluded from backups on purpose. Delete
`search.db` and you lose a cache, not a sentence — which is exactly the test §1
sets. Nothing ever reads it as truth: answers cite note ids, and the UI opens
the real file in Obsidian.

## Why answers are so tightly leashed

The model only ever sees excerpts retrieved from the vault, is told to answer
from them alone, and must cite the ids it used. A citation naming an id that
wasn't retrieved means the model went somewhere the vault didn't — that answer
is discarded, retried once with a stricter prompt, and then abandoned in favour
of saying so. "I couldn't find a confident answer in your notes" is a useful
sentence; a fluent answer sourced from the model's own memory, presented as if
it came from your notes, is worse than nothing.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from . import llm, notefm, route

log = logging.getLogger("pipeline")

SCHEMA_VERSION = 1
DEFAULT_K = 8
MAX_TERMS = 12
SNIPPET_TOKENS = 40
EXCERPT_CHARS = 600

# Every folder the pipeline writes notes to, plus wiki/. Deliberately NOT
# indexed: raw/ (user-managed, the pipeline never reads it), _System/ (status
# artifacts — pure noise in an answer), and 00-Inbox (those notes haven't
# passed the review gate, so an answer must not lean on them yet).
def indexed_folders() -> list[str]:
    return sorted(set(route.TYPE_FOLDER.values()))


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_CITE_RE = re.compile(r"\[\[(\d{14})\]\]")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS notes (
    id     TEXT PRIMARY KEY,
    path   TEXT NOT NULL,
    type   TEXT,
    folder TEXT,
    title  TEXT,
    mtime  REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(id UNINDEXED, title, body);
"""

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")


@dataclass
class Hit:
    id: str
    title: str
    path: str
    folder: str
    type: str
    snippet: str
    excerpt: str


def connect(db_path: Path, readonly: bool = False) -> sqlite3.Connection:
    """A fresh connection per call. The watcher writes on its own thread and API
    handlers run in a threadpool, so no connection is ever shared (the same
    discipline api/service.py follows for events.db)."""
    if readonly and Path(db_path).exists():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    else:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=5)
        conn.executescript(_SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.Error:
        return 0
    return int(row["value"]) if row else 0


def iter_notes(vault: Path) -> Iterator[tuple[str, Path, dict, str, float]]:
    """(id, path, frontmatter, body, mtime) for every indexable note. Notes
    without an id are skipped — an answer has to be able to cite something."""
    vault = Path(vault)
    for folder in indexed_folders():
        directory = vault / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                text = path.read_text()
                mtime = path.stat().st_mtime
            except OSError:
                continue
            fm, body = notefm.parse_frontmatter(text)
            note_id = fm.get("id", "").strip()
            if not note_id:
                continue
            yield note_id, path, fm, body, mtime


def _title(path: Path, fm: dict) -> str:
    return fm.get("title") or fm.get("name") or fm.get("claim") or \
        _DATE_PREFIX_RE.sub("", path.stem)


# Frontmatter fields that carry actual content rather than bookkeeping. A
# decision's `claim` and a person's `company` are the substance of those notes —
# leaving them out would make the two most structured note types the least
# findable ones.
_INDEXED_FIELDS = ("claim", "name", "company", "relationship", "outside_view",
                   "statement", "description", "resource_type")


def _searchable_body(fm: dict, body: str) -> str:
    fields = [str(fm[key]) for key in _INDEXED_FIELDS if fm.get(key)]
    return ("\n".join(fields) + "\n\n" + body.strip()).strip() if fields else body.strip()


def _upsert(conn: sqlite3.Connection, vault: Path, note_id: str, path: Path,
            fm: dict, body: str, mtime: float) -> None:
    title = _title(path, fm)
    conn.execute("DELETE FROM notes_fts WHERE id = ?", (note_id,))
    conn.execute(
        "INSERT OR REPLACE INTO notes (id, path, type, folder, title, mtime)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (note_id, str(path.relative_to(vault)), fm.get("type", ""),
         path.parent.name, title, mtime))
    conn.execute("INSERT INTO notes_fts (id, title, body) VALUES (?, ?, ?)",
                 (note_id, title, _searchable_body(fm, body)))


def rebuild(vault: Path, db_path: Path) -> int:
    """Throw the index away and build it again from the vault. This is always
    safe — the vault is the source, this file is a cache of it."""
    conn = connect(db_path)
    try:
        conn.execute("DELETE FROM notes")
        conn.execute("DELETE FROM notes_fts")
        count = 0
        for note_id, path, fm, body, mtime in iter_notes(vault):
            _upsert(conn, Path(vault), note_id, path, fm, body, mtime)
            count += 1
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                     (str(SCHEMA_VERSION),))
        conn.commit()
        return count
    finally:
        conn.close()


def reindex(vault: Path, db_path: Path) -> tuple[int, int]:
    """Bring the index up to date by mtime. Returns (changed, removed).
    A schema bump forces a full rebuild rather than leaving stale rows."""
    conn = connect(db_path)
    try:
        if _schema_version(conn) != SCHEMA_VERSION:
            conn.close()
            return rebuild(vault, db_path), 0
        known = {row["id"]: row["mtime"]
                 for row in conn.execute("SELECT id, mtime FROM notes")}
        seen: set[str] = set()
        changed = 0
        for note_id, path, fm, body, mtime in iter_notes(vault):
            seen.add(note_id)
            if known.get(note_id) == mtime:
                continue
            _upsert(conn, Path(vault), note_id, path, fm, body, mtime)
            changed += 1
        gone = [note_id for note_id in known if note_id not in seen]
        for note_id in gone:
            conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            conn.execute("DELETE FROM notes_fts WHERE id = ?", (note_id,))
        conn.commit()
        return changed, len(gone)
    finally:
        conn.close()


def tick(config, events, db_path: Path) -> None:
    """One incremental reindex per watcher pass. Never raises — search going
    stale is an inconvenience; the loop stopping is not."""
    try:
        changed, removed = reindex(Path(config.vault_path), db_path)
        if changed or removed:
            events.log("search", "search", "ok",
                       message=f"indexed={changed} removed={removed}")
    except Exception:
        log.exception("search reindex failed")


# ---- retrieval ---------------------------------------------------------------------

def sanitize_query(question: str) -> str:
    """A question → an FTS5 MATCH expression that can't be an injection.

    Every word is extracted and quoted, which turns FTS5's operators (AND, OR,
    NOT, NEAR, *, ^, :) into literal terms. OR-joining keeps recall on a
    natural-language question — an exact-phrase match would return nothing for
    most of them — and bm25 still ranks notes that hit several terms highest."""
    terms = _TOKEN_RE.findall(question or "")[:MAX_TERMS]
    return " OR ".join(f'"{t}"' for t in terms)


def top_k(db_path: Path, question: str, k: int = DEFAULT_K) -> list[Hit]:
    match = sanitize_query(question)
    if not match or not Path(db_path).exists():
        return []
    conn = connect(db_path, readonly=True)
    try:
        rows = conn.execute(
            "SELECT n.id, n.title, n.path, n.folder, n.type,"
            "       snippet(notes_fts, 2, '', '', ' … ', ?) AS snip,"
            "       substr(notes_fts.body, 1, ?) AS excerpt"
            " FROM notes_fts JOIN notes n ON n.id = notes_fts.id"
            " WHERE notes_fts MATCH ?"
            " ORDER BY bm25(notes_fts) LIMIT ?",
            (SNIPPET_TOKENS, EXCERPT_CHARS, match, k)).fetchall()
    except sqlite3.Error:
        log.exception("search query failed")
        return []
    finally:
        conn.close()
    return [Hit(id=r["id"], title=r["title"], path=r["path"], folder=r["folder"],
                type=r["type"] or "", snippet=(r["snip"] or "").strip(),
                excerpt=(r["excerpt"] or "").strip())
            for r in rows]


# ---- answering ----------------------------------------------------------------------

def build_prompt(question: str, hits: list[Hit], stricter: bool = False) -> str:
    excerpts = "\n\n".join(
        f"[id: {h.id}] {h.title}\n{h.excerpt}" for h in hits)
    ids = ", ".join(h.id for h in hits)
    strict_note = (
        "\nYou cited an id that was not in the excerpts. Cite ONLY these ids: "
        f"{ids}. If the excerpts do not answer the question, set confident to "
        "false and say so — that is a correct answer, not a failure.\n"
        if stricter else "")
    return (
        "Answer the question using ONLY the note excerpts below.\n"
        'Return ONLY JSON: {"answer": string, "cited_ids": [string], "confident": boolean}\n'
        "Rules: use nothing but the excerpts — no outside knowledge, no inference "
        "beyond what they say. Cite the notes you used inline as [[id]] using the "
        "exact ids given, and list the same ids in cited_ids. If the excerpts don't "
        "answer the question, set confident to false and say what's missing rather "
        "than filling the gap.\n"
        f"The only ids you may cite: {ids}.\n"
        f"{strict_note}"
        f"\nQuestion: {question}\n\nNote excerpts:\n{excerpts}\n")


def make_validator(retrieved: set[str]):
    """Rejects any answer that cites an id we didn't retrieve — inline or in
    cited_ids. That's the check that keeps a confident-sounding invention from
    reaching the screen wearing a citation."""
    def validate(data: object) -> str | None:
        if not isinstance(data, dict):
            return "not an object"
        answer = data.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            return "answer must be a non-empty string"
        cited = data.get("cited_ids")
        if not isinstance(cited, list) or not all(isinstance(c, str) for c in cited):
            return "cited_ids must be a list of strings"
        if not isinstance(data.get("confident"), bool):
            return "confident must be a boolean"
        unknown = [c for c in cited if c not in retrieved]
        if unknown:
            return f"cited ids not in the retrieved set: {', '.join(unknown)}"
        inline = set(_CITE_RE.findall(answer))
        stray = [c for c in inline if c not in retrieved]
        if stray:
            return f"inline citations not in the retrieved set: {', '.join(stray)}"
        if data["confident"] and not cited and not inline:
            return "a confident answer must cite at least one note"
        return None
    return validate


def _no_answer(hits: list[Hit], reason: str) -> dict:
    return {
        "found": False,
        "answer": None,
        "confident": False,
        "provider": None,
        "sources": [_source(h) for h in hits],
        "message": {
            "what": "I couldn't find a confident answer in your notes.",
            "cause": reason,
            "todo": "Try rephrasing the question, or open the notes below and read "
                    "them directly in Obsidian.",
        },
    }


def _source(hit: Hit) -> dict:
    return {"id": hit.id, "title": hit.title, "file": hit.path, "type": hit.type,
            "snippet": hit.snippet}


def answer(question: str, config, db_path: Path, router=None, k: int = None) -> dict:
    """Retrieve, then answer from what was retrieved — or say that you can't."""
    k = k or int(((getattr(config, "raw", None) or {}).get("query") or {}).get("k") or DEFAULT_K)
    hits = top_k(db_path, question, k)
    if not hits:
        return _no_answer(
            [], "Nothing in your notes matched those words. The search index may also "
                "be out of date if the watcher hasn't run since you wrote them.")

    retrieved = {h.id for h in hits}
    validate = make_validator(retrieved)
    call = router or (lambda prompt, cfg, validate_fn:
                      llm.complete_json(prompt, cfg, validate_fn, section="query"))

    data, provider, _attempts = call(build_prompt(question, hits), config, validate)
    if data is None:
        # One stricter retry, then stop. A second failure means the model can't
        # stay inside the excerpts for this question, and guessing is not the
        # fallback — saying so is.
        data, provider, _attempts = call(
            build_prompt(question, hits, stricter=True), config, validate)
    if data is None:
        return _no_answer(
            hits, "The notes below came back from the search, but no model could "
                  "answer from them without citing something that wasn't there.")
    if not data.get("confident"):
        return _no_answer(hits, data.get("answer") or
                          "The retrieved notes don't cover the question.")

    cited = set(data.get("cited_ids") or []) | set(_CITE_RE.findall(data["answer"]))
    return {
        "found": True,
        "answer": data["answer"],
        "confident": True,
        "provider": provider,
        "sources": [_source(h) for h in hits if h.id in cited] or
                   [_source(h) for h in hits],
        "message": None,
    }


def reindex_timed(vault: Path, db_path: Path) -> dict:
    t0 = time.monotonic()
    indexed = rebuild(vault, db_path)
    return {"ok": True, "indexed": indexed, "took_ms": int((time.monotonic() - t0) * 1000)}


def stats(db_path: Path) -> dict:
    if not Path(db_path).exists():
        return {"indexed": 0, "folders": indexed_folders()}
    conn = connect(db_path, readonly=True)
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM notes").fetchone()
        return {"indexed": row["n"], "folders": indexed_folders()}
    except sqlite3.Error:
        return {"indexed": 0, "folders": indexed_folders()}
    finally:
        conn.close()
