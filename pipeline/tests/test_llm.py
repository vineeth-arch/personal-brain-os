"""Pass B router tests: fall-through on invalid JSON, all-fail → needs-review,
keyless skip, floor ordering, provider recorded. Hermetic — providers are mock
callables, keys are fake env vars."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pipeline import classify, llm


def make_config(providers=None):
    raw = {"classification": {"providers": providers}} if providers else {}
    return SimpleNamespace(raw=raw, confidence_threshold=0.7)


@pytest.fixture
def fake_env(monkeypatch):
    for var in llm.ENV_KEYS.values():
        monkeypatch.setenv(var, "fake-key")


def _use(monkeypatch, providers: dict):
    monkeypatch.setattr(llm, "PROVIDERS", providers)
    monkeypatch.setattr(
        llm, "ENV_KEYS", {name: llm.ENV_KEYS.get(name, "ANTHROPIC_API_KEY")
                          for name in providers})


GOOD = json.dumps({"type": "learning", "confidence": 0.9, "title": "spaced-repetition",
                   "categories": [], "subjects": [], "tags": []})


def test_fall_through_on_invalid_json(monkeypatch, fake_env):
    calls = []
    _use(monkeypatch, {
        "gemini-flash": lambda p, k: calls.append("gemini") or "not json {{{",
        "claude-haiku": lambda p, k: calls.append("claude") or GOOD,
    })
    data, provider, attempts = llm.complete_json(
        "prompt", make_config(["gemini-flash"]), classify.validate_classification)
    assert data["type"] == "learning" and provider == "claude-haiku"
    assert [a.outcome for a in attempts] == ["invalid-json", "served"]
    assert calls == ["gemini", "claude"]        # identical prompt walked the chain


def test_schema_violation_falls_through(monkeypatch, fake_env):
    bad_type = json.dumps({"type": "brainstorm", "confidence": 0.9, "title": "x"})
    _use(monkeypatch, {
        "gemini-flash": lambda p, k: bad_type,   # type not in the locked list
        "claude-haiku": lambda p, k: GOOD,
    })
    data, provider, attempts = llm.complete_json(
        "prompt", make_config(["gemini-flash"]), classify.validate_classification)
    assert provider == "claude-haiku"
    assert attempts[0].outcome == "schema"


def test_keyless_skipped_silently(monkeypatch):
    for var in llm.ENV_KEYS.values():
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    _use(monkeypatch, {
        "gemini-flash": lambda p, k: (_ for _ in ()).throw(AssertionError("must not be called")),
        "claude-haiku": lambda p, k: GOOD,
    })
    monkeypatch.setitem(llm.ENV_KEYS, "gemini-flash", "GEMINI_API_KEY")  # unset → skip
    data, provider, attempts = llm.complete_json(
        "prompt", make_config(["gemini-flash"]), classify.validate_classification)
    assert provider == "claude-haiku"
    assert all(a.provider != "gemini-flash" for a in attempts)   # no row — silent skip


def test_all_fail_is_needs_review_never_a_guess(monkeypatch, fake_env, tmp_path):
    _use(monkeypatch, {
        "gemini-flash": lambda p, k: "garbage",
        "claude-haiku": lambda p, k: "also garbage",
    })
    item = SimpleNamespace(tag=None, name="mystery-memo")
    config = SimpleNamespace(raw={}, confidence_threshold=0.7)
    cls = classify.classify(item, "some transcript with no tag", config, llm_fn=None)
    assert cls.needs_review is True and cls.confidence == 0.0
    assert cls.provider == "none"
    assert [a.outcome for a in cls.attempts] == ["invalid-json", "invalid-json"]


def test_provider_recorded_on_success(monkeypatch, fake_env):
    _use(monkeypatch, {"claude-haiku": lambda p, k: GOOD})
    item = SimpleNamespace(tag=None, name="memo")
    config = SimpleNamespace(raw={}, confidence_threshold=0.7)
    cls = classify.classify(item, "no tag here", config, llm_fn=None)
    assert cls.provider == "claude-haiku" and cls.type == "learning"
    assert cls.attempts[-1].outcome == "served"
    assert cls.attempts[-1].confidence == 0.9


def test_floor_stays_last():
    config = make_config(["claude-haiku", "gemini-flash"])   # user put the floor first
    assert llm.chain(config)[-1] == "claude-haiku"
    assert llm.chain(config).count("claude-haiku") == 1
