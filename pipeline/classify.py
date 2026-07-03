"""Stage 3 — classify. Free tag-route when a capture #tag is in the filename or
spoken in the first 5 words; otherwise Claude Haiku returns structured JSON.
Below the confidence threshold → needs-review (never a silent best guess)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import llm
from .errors import StageError

# The 11 note TYPES (SCHEMA-REFERENCE.md §2) — a distinct vocabulary from tags.
NOTE_TYPES = ["musing", "learning", "todo", "journal", "project", "person",
              "resource", "decision", "principle", "insight", "reflection"]

# The 8 capture/routing TAGS (SCHEMA-REFERENCE.md §4) → the note type they route to.
TAG_TO_TYPE = {
    "todo": "todo", "idea": "musing", "journal": "journal", "learning": "learning",
    "person": "person", "resource": "resource", "decision": "decision", "project": "project",
}

_HASHTAG = re.compile(r"#([\w-]+)")


@dataclass
class Classification:
    type: str
    title: str
    categories: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    confidence: float = 1.0
    needs_review: bool = False
    routed_by: str = "tag"          # "tag" | "llm"
    provider: str = ""              # which model served the classification
    attempts: list = field(default_factory=list)  # llm.Attempt rows for stats


def _spoken_tag(transcript: str) -> str | None:
    words = transcript.split()[:5]
    for w in words:
        m = _HASHTAG.search(w)
        if m and m.group(1).lower() in TAG_TO_TYPE:
            return m.group(1).lower()
    return None


def classify(item, transcript: str, config, llm_fn=None) -> Classification:
    # 1. Free route: capture tag from filename, else spoken in first 5 words.
    tag = (item.tag or "").lower() if item.tag else None
    if tag not in TAG_TO_TYPE:
        tag = _spoken_tag(transcript)
    if tag in TAG_TO_TYPE:
        return Classification(
            type=TAG_TO_TYPE[tag], title=item.name, tags=[tag],
            confidence=1.0, needs_review=False, routed_by="tag")

    # 2. Paid route: the model router (Pass B). The injectable llm_fn seam is
    # kept for hermetic tests and behaves like a single always-on provider.
    provider = "stub"
    attempts: list = []
    if llm_fn is not None:
        data = llm_fn(transcript, config)
    else:
        data, provider, attempts = llm.complete_json(
            build_prompt(transcript), config, validate_classification)
        if data is None:
            # every provider failed → needs-review, NEVER a guess (the note
            # parks in 00-Inbox for a human decision)
            return Classification(
                type="musing", title=item.name, confidence=0.0,
                needs_review=True, routed_by="llm", provider="none",
                attempts=attempts)
    ctype = str(data.get("type", "")).lower()
    if ctype not in NOTE_TYPES:
        ctype = "musing"  # ponytail: unknown type from the model degrades to a safe default...
        data["confidence"] = min(float(data.get("confidence", 0)), 0.0)  # ...and is forced to review
    confidence = float(data.get("confidence", 0))
    return Classification(
        type=ctype,
        title=data.get("title") or item.name,
        categories=data.get("categories", []) or [],
        subjects=data.get("subjects", []) or [],
        tags=data.get("tags", []) or [],
        confidence=confidence,
        needs_review=confidence < config.confidence_threshold,
        routed_by="llm",
        provider=provider,
        attempts=attempts,
    )


def build_prompt(transcript: str) -> str:
    """The ONE classification prompt — identical to every provider in the chain."""
    return (
        "Classify this captured note. Return ONLY JSON with keys: "
        'type, categories, subjects, tags, confidence, title.\n'
        f"type must be one of: {', '.join(NOTE_TYPES)}.\n"
        "categories = what it IS, subjects = what it's ABOUT (short noun phrases). "
        "tags = controlled vocabulary tags. confidence = 0..1. title = a short kebab-friendly title.\n\n"
        f"NOTE:\n{transcript}"
    )


def validate_classification(data: object) -> str | None:
    """Schema gate every provider response must pass: locked type list,
    confidence 0-1, non-empty title. Returns a problem string or None."""
    if not isinstance(data, dict):
        return "not a JSON object"
    if str(data.get("type", "")).lower() not in NOTE_TYPES:
        return "type not in the locked list"
    try:
        conf = float(data.get("confidence"))
    except (TypeError, ValueError):
        return "confidence not a number"
    if not (0.0 <= conf <= 1.0):
        return "confidence outside 0..1"
    if not str(data.get("title") or "").strip():
        return "empty title"
    return None
