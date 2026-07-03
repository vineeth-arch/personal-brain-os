"""Stage 3 — classify. Free tag-route when a capture #tag is in the filename or
spoken in the first 5 words; otherwise Claude Haiku returns structured JSON.
Below the confidence threshold → needs-review (never a silent best guess)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

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

    # 2. Paid route: Claude Haiku.
    llm_fn = llm_fn or _haiku_classify
    data = llm_fn(transcript, config)
    ctype = data.get("type", "").lower()
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
    )


def _haiku_classify(transcript: str, config) -> dict:
    if not config.anthropic_key:
        raise StageError("Could not classify the note.",
                         "ANTHROPIC_API_KEY is not set, so the Haiku classifier can't run.",
                         "export ANTHROPIC_API_KEY=... or add a #tag to route it for free.")
    import anthropic
    client = anthropic.Anthropic(api_key=config.anthropic_key)
    prompt = (
        "Classify this captured note. Return ONLY JSON with keys: "
        'type, categories, subjects, tags, confidence, title.\n'
        f"type must be one of: {', '.join(NOTE_TYPES)}.\n"
        "categories = what it IS, subjects = what it's ABOUT (short noun phrases). "
        "tags = controlled vocabulary tags. confidence = 0..1. title = a short kebab-friendly title.\n\n"
        f"NOTE:\n{transcript}"
    )
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5", max_tokens=512,
            messages=[{"role": "user", "content": prompt}])
        text = msg.content[0].text.strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        return json.loads(text)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise StageError("Could not classify the note.",
                         "Haiku did not return the expected JSON shape.",
                         "Re-run; if it recurs, add a #tag to route the note for free.") from e
    except Exception as e:
        raise StageError("Could not classify the note.",
                         "The Anthropic request failed (network, quota, or bad key).",
                         "Check ANTHROPIC_API_KEY and your connection, then re-run.") from e
