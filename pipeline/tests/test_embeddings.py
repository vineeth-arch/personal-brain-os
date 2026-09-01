"""Task I1: the semantic search index. embeddings.db is disposable cache
(CLAUDE.md §1) — these tests defend the properties that make that true:

- reindex() only re-embeds a file that's new or whose mtime changed, never
  one already indexed at its current mtime (the whole point of the cache);
- a file that vanishes from the vault has its row deleted, not orphaned;
- cosine ranking is correct on known vectors, not just "didn't crash";
- api/notes.hybrid_search never returns the same note twice even when both
  the substring scan and the semantic query would have found it;
- everything degrades to silent no-ops with no OPENAI_API_KEY — no network
  call, no exception, no more than one quiet events row per day;
- pipeline/related.py's semantic-first upgrade prefers a real semantic hit
  over a substring-only candidate, and is byte-for-byte unchanged from Pass R
  when `embeddings_db` isn't passed at all.

No conftest.py — fakes and fixtures are local to this file, matching this
repo's established style (api/tests/test_push.py's FakeDex call-log pattern
for the stubbed HTTP call; pipeline/tests/test_related.py's `_note` vault
seeding; pipeline/tests/test_drain.py's once-a-day reminder assertion
shape)."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from api import notes
from pipeline import embeddings, related
from pipeline.events import EventLog


def _note(vault: Path, folder: str, note_id: str, slug: str,
         body: str = "Some content.\n\nMore.", title: str | None = None) -> Path:
    d = vault / folder
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"2026-01-01-{slug}.md"
    title_line = f"title: {title}\n" if title else ""
    path.write_text(
        f"---\nid: {note_id}\ntype: musing\ncreated: 2026-01-01\nstatus: active\n"
        f"{title_line}---\n\n{body}\n", encoding="utf-8")
    return path


def _seed_row(db_path: Path, note_id: str, path: str, title: str, vector: list[float]) -> None:
    conn = embeddings._connect(db_path)
    conn.execute(
        "INSERT INTO embeddings (id, path, mtime_ns, title, vector) VALUES (?, ?, ?, ?, ?)",
        (note_id, path, 1, title, embeddings._to_bytes(vector)))
    conn.commit()
    conn.close()


class FakePost:
    """Stands in for the network. Records every batch it's asked to embed —
    a re-run that unnecessarily re-embeds an already-current file fails this
    loudly, the same way FakeDex (api/tests/test_push.py) catches an
    unwanted write."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        base = sum(len(c) for c in self.calls[:-1])
        return [[float(base + i + 1), 0.0, 0.0] for i in range(len(texts))]


# ---- 1. incremental reindex: only changed files are re-embedded ------------

def test_incremental_reindex_only_embeds_new_or_changed_files(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    vault = tmp_path / "vault"
    p1 = _note(vault, "02-Musings", "n1", "one", body="Body for one.")
    p2 = _note(vault, "02-Musings", "n2", "two", body="Body for two.")
    p3 = _note(vault, "02-Musings", "n3", "three", body="Body for three.")
    db_path = tmp_path / "embeddings.db"
    fake = FakePost()

    result = embeddings.reindex(vault, db_path, post=fake)
    assert result == {"added": 3, "removed": 0}
    total_texts = sum(len(c) for c in fake.calls)
    assert total_texts == 3

    # Re-run with nothing changed: no file should be re-embedded at all.
    fake.calls.clear()
    result2 = embeddings.reindex(vault, db_path, post=fake)
    assert result2 == {"added": 0, "removed": 0}
    assert fake.calls == []

    # Bump exactly one file's mtime — only that file gets re-embedded.
    fake.calls.clear()
    new_ns = p2.stat().st_mtime_ns + 1_000_000_000
    os.utime(p2, ns=(new_ns, new_ns))
    result3 = embeddings.reindex(vault, db_path, post=fake)
    assert result3 == {"added": 1, "removed": 0}
    embedded_texts = [t for call in fake.calls for t in call]
    assert len(embedded_texts) == 1
    assert "Body for two." in embedded_texts[0]


# ---- 2. a file removed from the vault has its row deleted ------------------

def test_removed_file_deletes_its_embeddings_row(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    vault = tmp_path / "vault"
    p1 = _note(vault, "02-Musings", "n1", "one")
    p2 = _note(vault, "02-Musings", "n2", "two")
    _note(vault, "02-Musings", "n3", "three")
    db_path = tmp_path / "embeddings.db"
    fake = FakePost()
    embeddings.reindex(vault, db_path, post=fake)

    p2.unlink()
    result = embeddings.reindex(vault, db_path, post=fake)
    assert result["removed"] == 1

    conn = sqlite3.connect(db_path)
    rows = {r[0] for r in conn.execute("SELECT id FROM embeddings").fetchall()}
    conn.close()
    assert rows == {"n1", "n3"}


# ---- 3. cosine ranking on known vectors -------------------------------------

def test_query_ranks_by_cosine_similarity(tmp_path):
    db_path = tmp_path / "embeddings.db"
    _seed_row(db_path, "identical", "/v/identical.md", "Identical", [1.0, 0.0, 0.0])
    _seed_row(db_path, "orthogonal", "/v/orthogonal.md", "Orthogonal", [0.0, 1.0, 0.0])
    _seed_row(db_path, "opposite", "/v/opposite.md", "Opposite", [-1.0, 0.0, 0.0])

    results = embeddings.query(db_path, [1.0, 0.0, 0.0], k=3)
    ids_in_order = [r[0] for r in results]
    assert ids_in_order == ["identical", "orthogonal", "opposite"]

    scores = {r[0]: r[3] for r in results}
    assert scores["identical"] == pytest.approx(1.0)
    assert scores["orthogonal"] == pytest.approx(0.0, abs=1e-9)
    assert scores["opposite"] == pytest.approx(-1.0)


# ---- 4. hybrid_search never duplicates a note found both ways --------------

def test_hybrid_search_dedupes_a_note_matched_both_ways(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    vault = tmp_path / "vault"
    note_path = _note(vault, "02-Musings", "dup1", "warehouse-notes",
                      body="warehouse logistics plan", title="Warehouse notes")
    db_path = tmp_path / "embeddings.db"
    q_vector = [1.0, 0.0, 0.0]
    _seed_row(db_path, "dup1", str(note_path), "Warehouse notes", q_vector)
    monkeypatch.setattr(embeddings, "_default_post", lambda texts: [q_vector for _ in texts])

    results = notes.hybrid_search(vault, db_path, "warehouse", limit=5)
    matches = [r for r in results if r["id"] == "dup1"]
    assert len(matches) == 1, "a note the substring scan found must not reappear as a semantic hit"


# ---- 5. keyless: silent, no network, at most one events row per day --------

def test_reindex_is_none_and_makes_no_network_call_without_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    vault = tmp_path / "vault"
    _note(vault, "02-Musings", "n1", "one")
    db_path = tmp_path / "embeddings.db"
    fake = FakePost()

    result = embeddings.reindex(vault, db_path, post=fake)
    assert result is None
    assert fake.calls == []


def test_embeddings_tick_logs_at_most_once_per_day_when_keyless(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    events = EventLog(tmp_path / "events.db", vault)
    config = SimpleNamespace(vault_path=vault, raw={})

    embeddings.embeddings_tick(config, events)
    embeddings.embeddings_tick(config, events)   # second call, same "day" — must be a no-op

    assert events._count("stage='embed'") == 1
    events.close()


# ---- 6. related.find: semantic-first, substring-fallback -------------------

def test_related_find_prefers_a_real_semantic_hit_over_a_substring_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    vault = tmp_path / "vault"
    # a DIFFERENT note that would win the substring scan on its own —
    # shares the word "warehouse" with the query title.
    _note(vault, "02-Musings", "substring-candidate", "warehouse-notes",
         body="warehouse logistics plan")
    db_path = tmp_path / "embeddings.db"
    q_vector = [1.0, 0.0, 0.0]
    _seed_row(db_path, "semantic-target", "/v/semantic-target.md", "Semantic target note", q_vector)
    monkeypatch.setattr(embeddings, "_default_post", lambda texts: [q_vector for _ in texts])

    result = related.find(vault, "Warehouse logistics plan", "some body", "exclude-me",
                          embeddings_db=db_path)
    assert result == {"id": "semantic-target", "title": "Semantic target note"}, (
        "a real semantic hit must win — the substring-only candidate must never be returned")


def test_related_find_without_embeddings_db_matches_pass_r_behavior_unchanged(tmp_path):
    # Same scenario as test_related.py::test_shared_title_word_finds_the_other_note,
    # re-run here with the new kwarg simply omitted.
    vault = tmp_path / "vault"
    _note(vault, "02-Musings", "n1", "warehouse-visit-notes")
    result = related.find(vault, "Warehouse logistics plan", "no shared word here", "n2")
    assert result == {"id": "n1", "title": "warehouse-visit-notes"}


def test_related_find_below_similarity_floor_falls_through_to_substring(tmp_path, monkeypatch):
    """A weak semantic neighbor (cosine well below
    related._MIN_SEMANTIC_SIMILARITY) must never be returned just because
    it's the nearest thing in embedding space — this is the bug the
    similarity floor closes. With no substring match either, find() must
    come back None rather than the weak semantic hit."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    vault = tmp_path / "vault"
    db_path = tmp_path / "embeddings.db"
    q_vector = [1.0, 0.0, 0.0]
    # orthogonal to the query vector -> cosine 0.0, well below the 0.5 floor
    _seed_row(db_path, "weak-neighbor", "/v/weak-neighbor.md", "Unrelated note", [0.0, 1.0, 0.0])
    monkeypatch.setattr(embeddings, "_default_post", lambda texts: [q_vector for _ in texts])

    result = related.find(vault, "Warehouse logistics plan", "some body", "exclude-me",
                          embeddings_db=db_path)
    assert result is None, "a weak semantic hit below the floor must not be returned"


def test_related_find_below_similarity_floor_falls_through_to_real_substring_hit(tmp_path, monkeypatch):
    """Same weak-neighbor setup as above, but this time a real substring
    candidate exists in the vault — find() must fall through to it rather
    than settling for the weak semantic hit."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    vault = tmp_path / "vault"
    _note(vault, "02-Musings", "substring-candidate", "warehouse-notes",
         body="warehouse logistics plan")
    db_path = tmp_path / "embeddings.db"
    q_vector = [1.0, 0.0, 0.0]
    _seed_row(db_path, "weak-neighbor", "/v/weak-neighbor.md", "Unrelated note", [0.0, 1.0, 0.0])
    monkeypatch.setattr(embeddings, "_default_post", lambda texts: [q_vector for _ in texts])

    result = related.find(vault, "Warehouse logistics plan", "some body", "exclude-me",
                          embeddings_db=db_path)
    assert result == {"id": "substring-candidate", "title": "warehouse-notes"}
