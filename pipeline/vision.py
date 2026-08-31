"""Vision — one-shot photo description, cheapest-provider-first.

Images are the one modality outside the Pass B text router (llm.py):
Groq/OpenRouter-free aren't multimodal, so this is a new seam BESIDE the
router, not inside it. Real calls walk a cost-tiered chain — gemini-flash,
then openai-mini (gpt-4o-mini), then claude-haiku-4-5 as the floor — reading
keys straight from the environment the same way llm.py's own chain does
(ENV_KEYS + os.environ.get), since Config has no per-provider vision-key
properties to route through. Same principle as Pass L link enrichment: the
note is written unconditionally; a description is decoration. No key, a
timeout, a bad response from every provider — describe() returns None and
the caller (pipeline/enrich.py, pipeline/watcher.py) writes an honest,
undescribed note instead. Never an exception, never a guess, never anything
invented.

`caller` is a single-shot test seam (exactly the original Pass V3 contract):
when given, it bypasses the chain entirely and is called once against the
configured `anthropic_key`, gated on that key being present — every existing
test in this file's suite depends on that exact behavior. Production calls
(no `caller`) use the real cost-tiered chain instead.
"""
from __future__ import annotations

import base64
import json
import logging
import os
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

DEFAULT_CHAIN = ["gemini-flash", "openai-mini", "claude-haiku"]
FLOOR = "claude-haiku"
ENV_KEYS = {
    "gemini-flash": "GEMINI_API_KEY",
    "openai-mini": "OPENAI_API_KEY",
    "claude-haiku": "ANTHROPIC_API_KEY",
}

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


# ---- provider calls (identical prompt in, raw text out; raise on failure) ------

def _call_claude(image_path: Path, mime: str, key: str) -> str:
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


def _call_gemini(image_path: Path, mime: str, key: str) -> str:
    import urllib.request
    b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}",
        data=json.dumps({"contents": [{"parts": [
            {"text": PROMPT},
            {"inline_data": {"mime_type": mime, "data": b64}},
        ]}]}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read())
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_openai_mini(image_path: Path, mime: str, key: str) -> str:
    import urllib.request
    b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps({"model": "gpt-4o-mini", "messages": [{
            "role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


PROVIDERS = {
    "gemini-flash": _call_gemini,
    "openai-mini": _call_openai_mini,
    "claude-haiku": _call_claude,
}


def chain() -> list[str]:
    # filtered against PROVIDERS (not just DEFAULT_CHAIN) so tests can swap
    # PROVIDERS to a smaller fake set without a stale ENV_KEYS lookup
    return [name for name in DEFAULT_CHAIN if name in PROVIDERS]


def _parse_result(text: str) -> dict | None:
    try:
        data = json.loads(_strip_fences(text))
    except json.JSONDecodeError:
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


def describe(image_path: Path, config, caller=None) -> dict | None:
    """{description, resource_type, extracted_text} or None.

    None on: an unrecognized extension, no usable key anywhere in the chain,
    or every attempt failing/returning an invalid response — the caller
    always has a safe, honest fallback (an undescribed note, `enriched:
    false`, retryable later).
    """
    image_path = Path(image_path)
    mime = _MIME_BY_EXT.get(image_path.suffix.lower())
    if mime is None:
        return None

    if caller is not None:
        # Single-shot test seam — exactly the original Pass V3 contract.
        key = getattr(config, "anthropic_key", None)
        if not key:
            return None
        try:
            text = caller(image_path, mime, key)
        except Exception:
            log.info("vision describe failed", exc_info=True)
            return None
        return _parse_result(text)

    # Real chain: cheapest vision-capable provider first, claude-haiku the floor.
    for name in chain():
        key = os.environ.get(ENV_KEYS[name], "")
        if not key:
            continue  # keyless providers are skipped silently, same as llm.py
        try:
            text = PROVIDERS[name](image_path, mime, key)
        except Exception as e:
            log.info("vision provider %s failed: %s", name, e)
            continue
        result = _parse_result(text)
        if result is not None:
            return result
    return None
