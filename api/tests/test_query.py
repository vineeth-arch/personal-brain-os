"""/query endpoints (Pass 9). The model is monkeypatched at the router seam —
no provider is ever called from a test."""
from __future__ import annotations

from pipeline import search

from .test_api import TOKEN, Server, env  # noqa: F401  (env is a fixture)


def _seed(vault):
    (vault / "03-Learnings").mkdir(parents=True, exist_ok=True)
    (vault / "03-Learnings" / "2026-02-01-sqlite.md").write_text(
        "---\nid: 20260201090000\ntype: learning\ntitle: SQLite FTS5\n"
        "created: 2026-02-01\norigin: human\nstatus: active\n---\n\n"
        "FTS5 ships with the standard library build, so full-text search needs "
        "no dependency.\n")


def test_reindex_then_query_answers_with_a_citation(env, monkeypatch):
    root, vault, _, _ = env
    _seed(vault)

    def fake_complete(prompt, config, validate_fn, section="classification"):
        assert section == "query"        # the query chain, not the classifier's
        data = {"answer": "It needs no dependency [[20260201090000]].",
                "cited_ids": ["20260201090000"], "confident": True}
        assert validate_fn(data) is None
        return data, "fake-provider", []

    monkeypatch.setattr(search.llm, "complete_json", fake_complete)

    with Server(root) as s:
        code, body = s.req("POST", "/api/query/reindex")
        assert code == 200 and body["indexed"] == 1 and body["ok"] is True

        code, body = s.req("POST", "/api/query", {"question": "does search need a dependency"})
        assert code == 200 and body["found"] is True
        assert "[[20260201090000]]" in body["answer"]
        assert body["sources"][0]["file"] == "03-Learnings/2026-02-01-sqlite.md"
        assert body["provider"] == "fake-provider"

        code, body = s.req("GET", "/api/query/status")
        assert code == 200 and body["indexed"] == 1


def test_a_fabricated_citation_degrades_to_a_three_part_message(env, monkeypatch):
    root, vault, _, _ = env
    _seed(vault)

    def fake_complete(prompt, config, validate_fn, section="classification"):
        data = {"answer": "Certainly [[19990101000000]].",
                "cited_ids": ["19990101000000"], "confident": True}
        assert validate_fn(data)          # the validator rejects it
        return None, None, []

    monkeypatch.setattr(search.llm, "complete_json", fake_complete)

    with Server(root) as s:
        s.req("POST", "/api/query/reindex")
        code, body = s.req("POST", "/api/query", {"question": "anything"})
        # a graceful 200 with a plain-English message, not a crash and not a cite
        assert code == 200 and body["found"] is False
        assert set(body["message"]) == {"what", "cause", "todo"}
        assert "19990101000000" not in str(body)


def test_a_blank_question_is_a_400_envelope(env):
    root, _, _, _ = env
    with Server(root) as s:
        for question in ("", "   "):
            code, body = s.req("POST", "/api/query", {"question": question})
            assert code == 400
            assert set(body["error"]) == {"what", "cause", "todo"}


def test_query_requires_the_token(env):
    root, _, _, _ = env
    with Server(root) as s:
        assert s.req("POST", "/api/query", {"question": "hi"}, token=None)[0] == 401
        assert s.req("POST", "/api/query/reindex", token=None)[0] == 401


def test_querying_an_empty_vault_says_so_without_calling_a_model(env, monkeypatch):
    root, _, _, _ = env

    def never(*a, **k):
        raise AssertionError("no model should be called with nothing retrieved")

    monkeypatch.setattr(search.llm, "complete_json", never)
    with Server(root) as s:
        s.req("POST", "/api/query/reindex")
        code, body = s.req("POST", "/api/query", {"question": "what do I know"})
        assert code == 200 and body["found"] is False and body["sources"] == []


def test_config_exposes_the_query_chain(env):
    root, _, _, _ = env
    with Server(root) as s:
        _, body = s.req("GET", "/api/config")
        # unset in this fixture → falls back to the classification chain
        assert body["query_providers"][-1] == "claude-haiku"   # the floor stays last
