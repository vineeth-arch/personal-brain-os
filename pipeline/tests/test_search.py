"""/query (Pass 9): what gets indexed, incremental reindexing, query
sanitisation, and — the important one — that a fabricated citation never
reaches the answer."""
from __future__ import annotations

import sqlite3

import pytest

from pipeline import search


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    for folder in ("02-Musings", "03-Learnings", "07-People", "09-Decisions", "wiki",
                   "00-Inbox", "_System", "raw"):
        (v / folder).mkdir(parents=True)
    note(v, "02-Musings", "2026-01-01-loops.md", "20260101090000",
         "musing", "Thinking in loops",
         "The compiler analogy only works if you accept that loops are cheap.")
    note(v, "03-Learnings", "2026-02-01-sqlite.md", "20260201090000",
         "learning", "SQLite FTS5",
         "FTS5 ships with the standard library build, so full-text search needs no dependency.")
    note(v, "wiki", "2026-03-01-insight.md", "20260301090000",
         "insight", "Indexes are caches",
         "An index you can rebuild is a cache; an index you cannot is a database.")
    # decoys: none of these three may ever be indexed
    note(v, "00-Inbox", "2026-04-01-unreviewed.md", "20260401090000",
         "musing", "Not triaged yet", "This note has not passed the review gate.")
    (v / "_System" / "PIPELINE-STATUS.md").write_text("# Status\n\npending: 0\n")
    (v / "raw" / "source.md").write_text("A source document the pipeline never touches.\n")
    return v


def note(vault, folder, filename, note_id, ntype, title, body):
    (vault / folder / filename).write_text(
        f"---\nid: {note_id}\ntype: {ntype}\ntitle: {title}\ncreated: 2026-01-01\n"
        f"origin: human\nstatus: active\n---\n\n{body}\n")


def db(tmp_path):
    return tmp_path / "search.db"


# ---- indexing --------------------------------------------------------------------

def test_rebuild_indexes_pipeline_folders_only(vault, tmp_path):
    indexed = search.rebuild(vault, db(tmp_path))
    assert indexed == 3        # the two notes + the wiki insight; decoys excluded

    conn = search.connect(db(tmp_path), readonly=True)
    folders = {row["folder"] for row in conn.execute("SELECT folder FROM notes")}
    conn.close()
    assert folders == {"02-Musings", "03-Learnings", "wiki"}
    assert "00-Inbox" not in folders    # hasn't passed the review gate
    assert "_System" not in folders and "raw" not in folders


def test_notes_without_an_id_are_skipped(vault, tmp_path):
    (vault / "02-Musings" / "no-id.md").write_text("---\ntype: musing\n---\n\nOrphan.\n")
    assert search.rebuild(vault, db(tmp_path)) == 3      # unchanged — nothing to cite


def test_reindex_catches_edits_additions_and_deletions(vault, tmp_path):
    path = db(tmp_path)
    search.rebuild(vault, path)
    assert search.reindex(vault, path) == (0, 0)        # nothing changed

    edited = vault / "02-Musings" / "2026-01-01-loops.md"
    edited.write_text(edited.read_text() + "\nA later thought about recursion.\n")
    import os
    os.utime(edited, (edited.stat().st_atime, edited.stat().st_mtime + 10))
    assert search.reindex(vault, path) == (1, 0)
    assert any("recursion" in h.excerpt for h in search.top_k(path, "recursion"))

    note(vault, "09-Decisions", "2026-05-01-bet.md", "20260501090000",
         "decision", "A bet", "We ship before the conference.")
    assert search.reindex(vault, path) == (1, 0)

    edited.unlink()
    assert search.reindex(vault, path) == (0, 1)
    assert all(h.id != "20260101090000" for h in search.top_k(path, "loops"))


def test_a_schema_bump_rebuilds_rather_than_leaving_stale_rows(vault, tmp_path, monkeypatch):
    path = db(tmp_path)
    search.rebuild(vault, path)
    monkeypatch.setattr(search, "SCHEMA_VERSION", search.SCHEMA_VERSION + 1)
    changed, removed = search.reindex(vault, path)
    assert changed == 3 and removed == 0


def test_stats_on_a_missing_index_is_zero_not_an_error(tmp_path):
    assert search.stats(tmp_path / "nope.db")["indexed"] == 0


# ---- query sanitisation ---------------------------------------------------------

@pytest.mark.parametrize("question", [
    "what did I say about loops",
    'a AND b NOT c NEAR(x y)',
    "col:value ***^^^",
    '"unbalanced quote',
    "",
    "   ",
    "🙂 emoji only",
])
def test_sanitize_never_produces_a_syntax_error(vault, tmp_path, question):
    """Whatever someone types is treated as words, never as FTS5 syntax."""
    path = db(tmp_path)
    search.rebuild(vault, path)
    hits = search.top_k(path, question)    # must not raise sqlite3.OperationalError
    assert isinstance(hits, list)


def test_sanitize_quotes_each_term(vault):
    assert search.sanitize_query("loops AND cheap") == '"loops" OR "AND" OR "cheap"'
    assert search.sanitize_query("") == ""


def test_top_k_finds_the_right_note(vault, tmp_path):
    path = db(tmp_path)
    search.rebuild(vault, path)
    (top, *_rest) = search.top_k(path, "full-text search dependency")
    assert top.id == "20260201090000"
    assert "FTS5" in top.excerpt
    assert top.path == "03-Learnings/2026-02-01-sqlite.md"


# ---- answering --------------------------------------------------------------------

def router_returning(*payloads):
    """A fake model router: hands back each payload in turn, running the real
    validator over it exactly as llm.complete_json would."""
    calls = {"n": 0, "prompts": []}

    def router(prompt, config, validate_fn):
        calls["prompts"].append(prompt)
        i = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        data = payloads[i]
        if data is None or validate_fn(data):
            return None, None, []
        return data, "fake-provider", []

    router.calls = calls
    return router


def test_a_well_cited_answer_is_returned_with_its_sources(vault, tmp_path):
    path = db(tmp_path)
    search.rebuild(vault, path)
    router = router_returning({
        "answer": "FTS5 ships with the standard library [[20260201090000]].",
        "cited_ids": ["20260201090000"],
        "confident": True,
    })
    result = search.answer("does full-text search need a dependency", None, path,
                           router=router)
    assert result["found"] is True
    assert result["provider"] == "fake-provider"
    assert [s["id"] for s in result["sources"]] == ["20260201090000"]
    assert result["sources"][0]["file"] == "03-Learnings/2026-02-01-sqlite.md"


def test_a_fabricated_citation_is_retried_once_then_refused(vault, tmp_path):
    """The failure this whole feature is built around: an id that was never
    retrieved means the answer came from somewhere other than the vault."""
    path = db(tmp_path)
    search.rebuild(vault, path)
    invented = {"answer": "It is definitely so [[19990101000000]].",
                "cited_ids": ["19990101000000"], "confident": True}
    router = router_returning(invented, invented)

    result = search.answer("anything about loops", None, path, router=router)
    assert result["found"] is False
    assert result["answer"] is None
    assert set(result["message"]) == {"what", "cause", "todo"}
    assert "19990101000000" not in str(result)      # the invention never surfaces
    assert router.calls["n"] == 2                   # one stricter retry, then it stops
    assert "You cited an id that was not in the excerpts" in router.calls["prompts"][1]
    assert result["sources"]                        # the real notes are still offered


def test_the_stricter_retry_can_succeed(vault, tmp_path):
    path = db(tmp_path)
    search.rebuild(vault, path)
    router = router_returning(
        {"answer": "Made up [[19990101000000]].", "cited_ids": ["19990101000000"],
         "confident": True},
        {"answer": "An index you can rebuild is a cache [[20260301090000]].",
         "cited_ids": ["20260301090000"], "confident": True})
    result = search.answer("what is an index", None, path, router=router)
    assert result["found"] is True
    assert [s["id"] for s in result["sources"]] == ["20260301090000"]


def test_an_inline_citation_outside_the_retrieved_set_is_also_caught(vault, tmp_path):
    """cited_ids can be clean while the prose smuggles in an id — both are checked."""
    path = db(tmp_path)
    search.rebuild(vault, path)
    validate = search.make_validator({"20260201090000"})
    assert validate({"answer": "See [[19990101000000]].", "cited_ids": [],
                     "confident": True}) is not None
    assert validate({"answer": "See [[20260201090000]].",
                     "cited_ids": ["20260201090000"], "confident": True}) is None


def test_a_confident_answer_must_cite_something(vault):
    validate = search.make_validator({"20260201090000"})
    assert validate({"answer": "Yes.", "cited_ids": [], "confident": True}) is not None
    # an unconfident answer needs no citation — "I don't know" is a real answer
    assert validate({"answer": "The notes don't say.", "cited_ids": [],
                     "confident": False}) is None


def test_an_unconfident_model_is_reported_as_no_answer(vault, tmp_path):
    path = db(tmp_path)
    search.rebuild(vault, path)
    router = router_returning({"answer": "The notes don't cover that.",
                               "cited_ids": [], "confident": False})
    result = search.answer("what is my blood type", None, path, router=router)
    assert result["found"] is False
    assert "don't cover that" in result["message"]["cause"]


def test_nothing_matching_asks_no_model_at_all(vault, tmp_path):
    path = db(tmp_path)
    search.rebuild(vault, path)

    def never(prompt, config, validate_fn):
        raise AssertionError("no model should be called when nothing was retrieved")

    result = search.answer("zzzzqqq nonexistentword", None, path, router=never)
    assert result["found"] is False and result["sources"] == []
    assert set(result["message"]) == {"what", "cause", "todo"}


def test_querying_before_any_index_exists_is_graceful(tmp_path):
    result = search.answer("anything", None, tmp_path / "missing.db",
                           router=lambda *a: (_ for _ in ()).throw(AssertionError()))
    assert result["found"] is False and result["sources"] == []


# ---- the loop tick -------------------------------------------------------------------

class FakeEvents:
    def __init__(self):
        self.rows = []

    def log(self, *a, **k):
        self.rows.append((a, k))


def test_tick_indexes_and_logs_only_when_something_changed(vault, tmp_path):
    from types import SimpleNamespace
    config = SimpleNamespace(vault_path=vault, raw={})
    events = FakeEvents()
    search.tick(config, events, db(tmp_path))
    assert len(events.rows) == 1 and "indexed=3" in events.rows[0][1]["message"]

    search.tick(config, events, db(tmp_path))
    assert len(events.rows) == 1        # nothing changed, nothing logged


def test_tick_never_raises(tmp_path):
    from types import SimpleNamespace
    config = SimpleNamespace(vault_path=tmp_path / "does-not-exist", raw={})
    search.tick(config, FakeEvents(), db(tmp_path))     # must not raise


def test_content_bearing_frontmatter_is_searchable(vault, tmp_path):
    """A decision's claim and a person's company are the substance of those
    notes — searching for them has to work, or the two most structured note
    types become the least findable."""
    (vault / "09-Decisions").mkdir(exist_ok=True)
    (vault / "09-Decisions" / "2026-05-01-launch.md").write_text(
        "---\nid: 20260501090000\ntype: decision\nstatus: open\n"
        "claim: The launch slips past October\n"
        "outside_view: most launches slip\n---\n\n"
        "## Reasoning\nThe vendor is the long pole.\n")
    path = db(tmp_path)
    search.rebuild(vault, path)

    (top, *_rest) = search.top_k(path, "what did I decide about the launch")
    assert top.id == "20260501090000"
    assert top.title == "The launch slips past October"   # the claim titles it
