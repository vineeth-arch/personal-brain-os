"""Multi-topic splitter (Pass E). Review-gated: propose() only ever logs a
SUGGESTION to events.db — it never touches the note itself. The note is only
ever split when a human taps [Split] in Triage (api/notes.py::execute_split).
Self-contained, stdlib + pipeline.llm only — no api/ imports, matching
pipeline/resurface.py's and pipeline/related.py's established convention."""
from __future__ import annotations

from pathlib import Path

from . import llm

MIN_WORDS = 200
MIN_SEGMENTS = 2
MAX_SEGMENTS = 5
CONFIDENCE_FLOOR = 0.7


def _prompt(body: str) -> str:
    numbered = "\n".join(f"{i + 1}: {line}" for i, line in enumerate(body.splitlines()))
    return (
        "Does this capture cover more than one distinct topic? Return ONLY "
        "JSON with keys: multi_topic (bool), confidence (0..1 — how sure you "
        "are), segments (a list of 2-5 objects, each {title, start_line, "
        "end_line}, 1-indexed against the numbered lines below, contiguous, "
        "covering every line, in order). If multi_topic is false, segments "
        "may be an empty list.\n\n"
        f"NOTE (numbered lines):\n{numbered}"
    )


def validate_split(data: object) -> str | None:
    if not isinstance(data, dict):
        return "not a JSON object"
    if not isinstance(data.get("multi_topic"), bool):
        return "multi_topic must be a boolean"
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        return "confidence not a number"
    if not (0.0 <= confidence <= 1.0):
        return "confidence outside 0..1"
    if not data["multi_topic"]:
        return None  # segments irrelevant when not multi-topic
    segments = data.get("segments")
    if not isinstance(segments, list) or not (MIN_SEGMENTS <= len(segments) <= MAX_SEGMENTS):
        return f"segments must be a list of {MIN_SEGMENTS}-{MAX_SEGMENTS} items when multi_topic"
    prev_end = 0
    for seg in segments:
        if not isinstance(seg, dict):
            return "each segment must be an object"
        title, start, end = seg.get("title"), seg.get("start_line"), seg.get("end_line")
        if not isinstance(title, str) or not title.strip():
            return "each segment needs a non-empty title"
        if not isinstance(start, int) or not isinstance(end, int) or start > end:
            return "each segment needs integer start_line <= end_line"
        if start != prev_end + 1:
            return "segments must be contiguous, starting at line 1, no gaps or overlaps"
        prev_end = end
    return None


def propose(note_type: str, body: str, config, *, llm_fn=None) -> dict | None:
    """None when: wrong type, too short, the model says one topic, low
    confidence, or every provider failed/returned invalid JSON — never a
    guess, same discipline as classify.classify(). `llm_fn`, when given,
    replaces the real llm.complete_json call for tests (signature:
    llm_fn(body, config) -> dict | None, matching classify()'s injectable
    seam) — production code always omits it."""
    from . import route
    if note_type not in route.SPLITTABLE:
        return None
    if len(body.split()) < MIN_WORDS:
        return None
    if llm_fn is not None:
        data = llm_fn(body, config)
    else:
        data, _provider, _attempts = llm.complete_json(_prompt(body), config, validate_split)
    if data is None or not data.get("multi_topic"):
        return None
    confidence = float(data.get("confidence", 0))
    if confidence < CONFIDENCE_FLOOR:
        return None
    segments = data.get("segments") or []
    if not (MIN_SEGMENTS <= len(segments) <= MAX_SEGMENTS):
        return None
    return {"confidence": confidence, "segments": segments}
