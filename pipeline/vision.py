"""Vision — one-shot photo description via the `anthropic` SDK directly.

Images are the one modality outside the Pass B text router (llm.py):
Groq/OpenRouter-free aren't multimodal, so this is a new seam BESIDE the
router, not inside it — same floor model the router already uses
(claude-haiku-4-5, which is vision-capable). Same principle as Pass L link
enrichment: the note is written unconditionally; a description is
decoration. No key, a timeout, a bad response — describe() returns None and
the caller (Pass V3, pipeline/watcher.py) writes an honest, undescribed note
instead. Never an exception, never a guess, never anything invented.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path

log = logging.getLogger("pipeline")

TIMEOUT = 30
MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1024

# Must match pipeline/enrich.py RESOURCE_TYPES / SCHEMA-REFERENCE.md §7 "resource_type".
RESOURCE_TYPES = ["tool", "tutorial", "book", "movie", "recipe", "place", "article"]

_MIME_BY_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp"}

# The leash: describe only what's visible, never invent — a vision module
# reading photos of people/places is exactly the shape that would tempt
# fabricated detail (CLAUDE.md §4 territory, structurally guarded by
# api/tests/test_no_send.py's folder scan too).
PROMPT = (
    "Describe ONLY what is visible in this image. Never invent people, places, "
    "text, or context you cannot actually see — if you're unsure, say less, not more. "
    "Return ONLY JSON with keys: description, resource_type, extracted_text.\n"
    "description = one or two plain sentences describing what's actually in the photo.\n"
    f"resource_type must be one of: {', '.join(RESOURCE_TYPES)} — your best guess for how "
    "this photo would be filed as a resource (a screenshot of an article is 'article', a "
    "book cover is 'book', a photo of a place is 'place', etc.); default to 'article' when unsure.\n"
    "extracted_text = any text visibly written or printed in the image, verbatim, up to "
    "2000 characters; '' when there is none."
)


def _strip_fences(text: str) -> str:
    return re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()


def validate_vision(data: object) -> str | None:
    if not isinstance(data, dict):
        return "not a JSON object"
    if not str(data.get("description") or "").strip():
        return "empty description"
    return None


def _call(image_path: Path, mime: str, key: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    msg = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS, timeout=TIMEOUT,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
            {"type": "text", "text": PROMPT},
        ]}])
    return msg.content[0].text


def describe(image_path: Path, config, caller=None) -> dict | None:
    """{description, resource_type, extracted_text} or None.

    None on: an unrecognized extension, a missing ANTHROPIC_API_KEY, or any
    failure/invalid response from the model — the caller always has a safe,
    honest fallback (an undescribed note, `enriched: false`, retryable later).
    `caller(image_path, mime, key) -> raw text` is the hermetic test seam,
    mirroring llm.py's provider functions.
    """
    image_path = Path(image_path)
    mime = _MIME_BY_EXT.get(image_path.suffix.lower())
    if mime is None:
        return None
    key = getattr(config, "anthropic_key", None)
    if not key:
        return None
    try:
        text = (caller or _call)(image_path, mime, key)
        data = json.loads(_strip_fences(text))
    except Exception:
        log.info("vision describe failed", exc_info=True)
        return None
    if validate_vision(data) is not None:
        return None
    rtype = str(data.get("resource_type", "")).lower()
    if rtype not in RESOURCE_TYPES:
        rtype = "article"
    return {
        "description": str(data.get("description") or "").strip(),
        "resource_type": rtype,
        "extracted_text": str(data.get("extracted_text") or "").strip()[:2000],
    }
