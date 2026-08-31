"""Pass V3: vision.describe() is a hermetic seam (the `caller` param stands
in for the real anthropic call, exactly like llm.py's provider functions) —
no network, no real key needed. Every failure mode degrades to None; nothing
here should ever raise."""
from __future__ import annotations

import json
from types import SimpleNamespace

from pipeline import vision


def config(key="fake-key"):
    return SimpleNamespace(anthropic_key=key)


def test_describe_returns_structured_result(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"not really a jpeg, the caller is stubbed")

    def caller(path, mime, key):
        assert path == img and mime == "image/jpeg" and key == "fake-key"
        return json.dumps({
            "description": "A handwritten note on a whiteboard.",
            "resource_type": "article",
            "extracted_text": "Ship by Friday",
        })

    result = vision.describe(img, config(), caller=caller)
    assert result == {
        "description": "A handwritten note on a whiteboard.",
        "resource_type": "article",
        "extracted_text": "Ship by Friday",
    }


def test_describe_strips_markdown_fences(tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"x")

    def caller(path, mime, key):
        return '```json\n{"description": "a cat", "resource_type": "article", "extracted_text": ""}\n```'

    result = vision.describe(img, config(), caller=caller)
    assert result is not None and result["description"] == "a cat"


def test_describe_returns_none_without_a_key(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    result = vision.describe(img, config(key=None), caller=lambda *a: "{}")
    assert result is None


def test_describe_returns_none_for_an_unrecognised_extension(tmp_path):
    img = tmp_path / "photo.heic"
    img.write_bytes(b"x")
    result = vision.describe(img, config(), caller=lambda *a: "{}")
    assert result is None


def test_describe_returns_none_on_bad_json(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    result = vision.describe(img, config(), caller=lambda *a: "not json at all")
    assert result is None


def test_describe_returns_none_when_caller_raises(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")

    def caller(path, mime, key):
        raise TimeoutError("slow")

    assert vision.describe(img, config(), caller=caller) is None


def test_describe_returns_none_on_empty_description(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")

    def caller(path, mime, key):
        return json.dumps({"description": "", "resource_type": "article", "extracted_text": ""})

    assert vision.describe(img, config(), caller=caller) is None


def test_describe_falls_back_to_article_for_unknown_resource_type(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")

    def caller(path, mime, key):
        return json.dumps({"description": "a receipt", "resource_type": "not-a-real-type",
                           "extracted_text": ""})

    result = vision.describe(img, config(), caller=caller)
    assert result["resource_type"] == "article"


def test_describe_truncates_extracted_text_to_2000_chars(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    long_text = "a" * 3000

    def caller(path, mime, key):
        return json.dumps({"description": "a wall of text", "resource_type": "article",
                           "extracted_text": long_text})

    result = vision.describe(img, config(), caller=caller)
    assert len(result["extracted_text"]) == 2000


def test_validate_vision_rejects_non_dict_and_empty_description():
    assert vision.validate_vision("not a dict") == "not a JSON object"
    assert vision.validate_vision({"description": "  "}) == "empty description"
    assert vision.validate_vision({"description": "ok"}) is None


def test_prompt_never_authorizes_invention():
    """The leash: the model is told to describe only what it can see."""
    assert "only what is visible" in vision.PROMPT.lower()
    assert "never invent" in vision.PROMPT.lower()


# ---- the real chain (no `caller` override) — cheapest provider first ----------
# These exercise describe()'s production path: DEFAULT_CHAIN walked in order,
# reading keys straight from the environment (mirrors llm.py's own chain),
# never the single-shot `caller` seam above.

GOOD = json.dumps({"description": "a receipt", "resource_type": "article", "extracted_text": ""})


def _use(monkeypatch, providers: dict):
    monkeypatch.setattr(vision, "PROVIDERS", providers)
    monkeypatch.setattr(vision, "ENV_KEYS",
                        {name: vision.ENV_KEYS.get(name, "ANTHROPIC_API_KEY") for name in providers})


def test_chain_tries_cheapest_provider_first(tmp_path, monkeypatch):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    for var in vision.ENV_KEYS.values():
        monkeypatch.setenv(var, "fake-key")
    calls = []
    _use(monkeypatch, {
        "gemini-flash": lambda p, m, k: calls.append("gemini") or GOOD,
        "claude-haiku": lambda p, m, k: calls.append("claude") or GOOD,
    })
    result = vision.describe(img, config())
    assert result["description"] == "a receipt"
    assert calls == ["gemini"]  # cheapest provider served — claude never ran


def test_chain_falls_through_on_failure_to_the_floor(tmp_path, monkeypatch):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    for var in vision.ENV_KEYS.values():
        monkeypatch.setenv(var, "fake-key")
    calls = []

    def boom(p, m, k):
        calls.append("gemini")
        raise ConnectionError("network down")

    _use(monkeypatch, {"gemini-flash": boom, "claude-haiku": lambda p, m, k: calls.append("claude") or GOOD})
    result = vision.describe(img, config())
    assert result is not None
    assert calls == ["gemini", "claude"]


def test_chain_skips_keyless_providers_silently(tmp_path, monkeypatch):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    _use(monkeypatch, {
        "gemini-flash": lambda p, m, k: (_ for _ in ()).throw(AssertionError("must not be called")),
        "claude-haiku": lambda p, m, k: GOOD,
    })
    result = vision.describe(img, config())
    assert result is not None


def test_chain_all_providers_fail_returns_none_never_raises(tmp_path, monkeypatch):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    for var in vision.ENV_KEYS.values():
        monkeypatch.setenv(var, "fake-key")
    _use(monkeypatch, {
        "gemini-flash": lambda p, m, k: "garbage",
        "claude-haiku": lambda p, m, k: "also garbage",
    })
    assert vision.describe(img, config()) is None


def test_chain_no_keys_at_all_returns_none(tmp_path, monkeypatch):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    for var in vision.ENV_KEYS.values():
        monkeypatch.delenv(var, raising=False)
    assert vision.describe(img, config()) is None


def test_chain_ignores_config_anthropic_key_uses_environment_instead(tmp_path, monkeypatch):
    """The real chain reads os.environ directly (matching llm.py's own
    convention) — it does not depend on config.anthropic_key at all."""
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _use(monkeypatch, {"claude-haiku": lambda p, m, k: GOOD})
    result = vision.describe(img, SimpleNamespace(anthropic_key=None))
    assert result is not None
