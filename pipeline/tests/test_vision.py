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
