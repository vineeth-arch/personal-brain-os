"""Task E1: pipeline/split.py — validate_split's contiguity/overlap rules and
propose()'s short-circuits (wrong type, too short, low confidence, model says
one topic) BEFORE any LLM call. No conftest.py, matching this repo's per-file
fixture style (see pipeline/tests/test_related.py)."""
from __future__ import annotations

from pipeline import split


def _segments(*ranges: tuple[int, int]) -> list[dict]:
    return [
        {"title": f"part {i + 1}", "start_line": s, "end_line": e}
        for i, (s, e) in enumerate(ranges)
    ]


# ---- validate_split ----------------------------------------------------------

def test_rejects_non_dict():
    assert split.validate_split("not a dict") is not None
    assert split.validate_split([1, 2, 3]) is not None


def test_rejects_missing_multi_topic():
    assert split.validate_split({"confidence": 0.8}) is not None


def test_rejects_bad_confidence():
    assert split.validate_split({"multi_topic": True, "confidence": 1.5,
                                 "segments": _segments((1, 5), (6, 10))}) is not None
    assert split.validate_split({"multi_topic": True, "confidence": -0.1,
                                 "segments": _segments((1, 5), (6, 10))}) is not None
    assert split.validate_split({"multi_topic": True, "confidence": "high",
                                 "segments": _segments((1, 5), (6, 10))}) is not None


def test_rejects_gap_between_segments():
    data = {"multi_topic": True, "confidence": 0.8,
            "segments": _segments((1, 5), (7, 10))}  # gap: line 6 missing
    assert split.validate_split(data) is not None


def test_rejects_overlapping_segments():
    data = {"multi_topic": True, "confidence": 0.8,
            "segments": _segments((1, 5), (4, 10))}  # overlap: lines 4-5
    assert split.validate_split(data) is not None


def test_rejects_too_few_segments():
    data = {"multi_topic": True, "confidence": 0.8, "segments": _segments((1, 10))}
    assert split.validate_split(data) is not None


def test_rejects_too_many_segments():
    data = {"multi_topic": True, "confidence": 0.8,
            "segments": _segments((1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12))}
    assert split.validate_split(data) is not None


def test_rejects_missing_or_empty_title():
    data = {"multi_topic": True, "confidence": 0.8,
            "segments": [{"title": "", "start_line": 1, "end_line": 5},
                        {"title": "ok", "start_line": 6, "end_line": 10}]}
    assert split.validate_split(data) is not None
    data2 = {"multi_topic": True, "confidence": 0.8,
             "segments": [{"start_line": 1, "end_line": 5},
                         {"title": "ok", "start_line": 6, "end_line": 10}]}
    assert split.validate_split(data2) is not None


def test_rejects_start_after_end():
    data = {"multi_topic": True, "confidence": 0.8,
            "segments": [{"title": "a", "start_line": 5, "end_line": 1},
                        {"title": "b", "start_line": 6, "end_line": 10}]}
    assert split.validate_split(data) is not None


def test_accepts_exactly_two_segments():
    data = {"multi_topic": True, "confidence": 0.8, "segments": _segments((1, 5), (6, 10))}
    assert split.validate_split(data) is None


def test_accepts_exactly_five_segments():
    data = {"multi_topic": True, "confidence": 0.8,
            "segments": _segments((1, 2), (3, 4), (5, 6), (7, 8), (9, 10))}
    assert split.validate_split(data) is None


def test_multi_topic_false_always_valid_regardless_of_segments():
    # per the "segments irrelevant when not multi-topic" rule — even a
    # malformed/overlapping segments list is ignored when multi_topic is false
    assert split.validate_split({"multi_topic": False, "confidence": 0.9,
                                 "segments": "garbage"}) is None
    assert split.validate_split({"multi_topic": False, "confidence": 0.9}) is None


# ---- propose() -----------------------------------------------------------------

def _long_body(words: int = 250) -> str:
    return " ".join(["word"] * words)


def _boom(_body, _config):
    raise AssertionError("llm_fn should never be called for this case")


def test_propose_none_when_multi_topic_false():
    def llm_fn(_body, _config):
        return {"multi_topic": False, "confidence": 0.9, "segments": []}
    assert split.propose("journal", _long_body(), None, llm_fn=llm_fn) is None


def test_propose_short_circuits_on_ineligible_type_no_llm_call():
    assert split.propose("resource", _long_body(), None, llm_fn=_boom) is None


def test_propose_short_circuits_on_short_body_no_llm_call():
    assert split.propose("journal", "too short", None, llm_fn=_boom) is None


def test_propose_none_on_low_confidence():
    def llm_fn(_body, _config):
        return {"multi_topic": True, "confidence": 0.5,
                "segments": [{"title": "a", "start_line": 1, "end_line": 5},
                            {"title": "b", "start_line": 6, "end_line": 10}]}
    assert split.propose("journal", _long_body(), None, llm_fn=llm_fn) is None


def test_propose_returns_confidence_and_segments_when_eligible():
    segments = [{"title": "a", "start_line": 1, "end_line": 5},
               {"title": "b", "start_line": 6, "end_line": 10}]

    def llm_fn(_body, _config):
        return {"multi_topic": True, "confidence": 0.8, "segments": segments}

    result = split.propose("musing", _long_body(), None, llm_fn=llm_fn)
    assert result == {"confidence": 0.8, "segments": segments}
