"""Pass 13 — vision extraction router. Hermetic: PROVIDERS/ENV_KEYS are
swapped for fakes, no network, no real image bytes needed (the router never
looks at the bytes themselves, only base64-encodes and forwards them)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pipeline import vision

FAKE_IMAGE = b"\xff\xd8\xff\xe0fake-jpeg-bytes"


def make_config(providers=None):
    raw = {"vision": {"providers": providers}} if providers else {}
    return SimpleNamespace(raw=raw)


@pytest.fixture
def fake_env(monkeypatch):
    for var in vision.ENV_KEYS.values():
        monkeypatch.setenv(var, "fake-key")


def _use(monkeypatch, providers: dict):
    monkeypatch.setattr(vision, "PROVIDERS", providers)
    monkeypatch.setattr(
        vision, "ENV_KEYS", {name: vision.ENV_KEYS.get(name, "ANTHROPIC_API_KEY")
                             for name in providers})


GOOD = json.dumps({"extracted_text": "Chicken biryani: rice, chicken, yoghurt, spices."})


# ---- on-device OCR short-circuit --------------------------------------------

def test_ocr_hint_above_threshold_skips_every_provider(monkeypatch):
    _use(monkeypatch, {
        "gemini-flash": lambda img, mime, k: (_ for _ in ()).throw(AssertionError("must not be called")),
    })
    long_ocr = "x" * vision.OCR_MIN_CHARS
    text, source, attempts = vision.extract(FAKE_IMAGE, "image/jpeg", long_ocr, make_config())
    assert text == long_ocr
    assert source == "on-device-ocr"
    assert attempts == [vision.Attempt("on-device-ocr", "served")]


def test_short_ocr_hint_falls_through_to_providers(monkeypatch, fake_env):
    _use(monkeypatch, {"claude-haiku": lambda img, mime, k: GOOD})
    text, source, attempts = vision.extract(FAKE_IMAGE, "image/jpeg", "too short", make_config())
    assert "biryani" in text
    assert source == "claude-haiku"


def test_empty_ocr_hint_falls_through(monkeypatch, fake_env):
    _use(monkeypatch, {"claude-haiku": lambda img, mime, k: GOOD})
    text, source, attempts = vision.extract(FAKE_IMAGE, "image/jpeg", "", make_config())
    assert source == "claude-haiku"


# ---- provider chain ----------------------------------------------------------

def test_fall_through_on_invalid_json(monkeypatch, fake_env):
    calls = []
    _use(monkeypatch, {
        "gemini-flash": lambda img, mime, k: calls.append("gemini") or "not json {{{",
        "claude-haiku": lambda img, mime, k: calls.append("claude") or GOOD,
    })
    data, provider, attempts = vision.complete_vision_json(FAKE_IMAGE, "image/jpeg",
                                                           make_config(["gemini-flash"]))
    assert provider == "claude-haiku"
    assert [a.outcome for a in attempts] == ["invalid-json", "served"]
    assert calls == ["gemini", "claude"]


def test_schema_violation_falls_through(monkeypatch, fake_env):
    empty_text = json.dumps({"extracted_text": ""})
    _use(monkeypatch, {
        "gemini-flash": lambda img, mime, k: empty_text,
        "claude-haiku": lambda img, mime, k: GOOD,
    })
    data, provider, attempts = vision.complete_vision_json(FAKE_IMAGE, "image/jpeg",
                                                           make_config(["gemini-flash"]))
    assert provider == "claude-haiku"
    assert attempts[0].outcome == "schema"


def test_keyless_provider_skipped_silently(monkeypatch):
    for var in vision.ENV_KEYS.values():
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    _use(monkeypatch, {
        "gemini-flash": lambda img, mime, k: (_ for _ in ()).throw(AssertionError("must not be called")),
        "claude-haiku": lambda img, mime, k: GOOD,
    })
    monkeypatch.setitem(vision.ENV_KEYS, "gemini-flash", "GEMINI_API_KEY")
    data, provider, attempts = vision.complete_vision_json(FAKE_IMAGE, "image/jpeg",
                                                           make_config(["gemini-flash"]))
    assert provider == "claude-haiku"
    assert all(a.provider != "gemini-flash" for a in attempts)


def test_all_providers_fail_extract_returns_empty_never_raises(monkeypatch, fake_env):
    _use(monkeypatch, {
        "gemini-flash": lambda img, mime, k: "garbage",
        "claude-haiku": lambda img, mime, k: "also garbage",
    })
    text, source, attempts = vision.extract(FAKE_IMAGE, "image/jpeg", "", make_config(["gemini-flash"]))
    assert text == ""
    assert source == "none"
    assert len(attempts) == 2


def test_provider_exception_falls_through_never_raises(monkeypatch, fake_env):
    def boom(img, mime, k):
        raise ConnectionError("network is down")

    _use(monkeypatch, {"gemini-flash": boom, "claude-haiku": lambda img, mime, k: GOOD})
    text, source, attempts = vision.extract(FAKE_IMAGE, "image/jpeg", "", make_config(["gemini-flash"]))
    assert source == "claude-haiku"
    assert attempts[0].outcome == "error"


def test_rate_limit_falls_through(monkeypatch, fake_env):
    def rate_limited(img, mime, k):
        raise vision.RateLimited()

    _use(monkeypatch, {"gemini-flash": rate_limited, "claude-haiku": lambda img, mime, k: GOOD})
    data, provider, attempts = vision.complete_vision_json(FAKE_IMAGE, "image/jpeg",
                                                           make_config(["gemini-flash"]))
    assert provider == "claude-haiku"
    assert attempts[0].outcome == "rate-limit"


def test_floor_stays_last_even_if_reordered():
    config = make_config(["claude-haiku", "gemini-flash"])
    assert vision.chain(config)[-1] == "claude-haiku"
    assert vision.chain(config).count("claude-haiku") == 1


def test_default_chain_order():
    assert vision.chain(make_config()) == ["gemini-flash", "openai-mini", "claude-haiku"]


def test_image_bytes_are_base64_encoded_before_sending(monkeypatch, fake_env):
    seen = {}

    def capture(img_b64, mime, k):
        seen["b64"] = img_b64
        seen["mime"] = mime
        return GOOD

    _use(monkeypatch, {"claude-haiku": capture})
    vision.extract(FAKE_IMAGE, "image/png", "", make_config())
    import base64
    assert base64.b64decode(seen["b64"]) == FAKE_IMAGE
    assert seen["mime"] == "image/png"
