"""Image-text extraction router (Pass 13). Cheapest-first, mirroring the
philosophy of llm.py's model router but for vision:

  1. on-device OCR (from the capture sidecar) — zero API calls, free.
  2. gemini-flash (vision-capable) — cheap, keyless-skipped if unconfigured.
  3. openai-mini (gpt-4o-mini vision) — keyless-skipped if unconfigured.
  4. claude-haiku (vision) — the floor, always runs last.

This module ONLY extracts text/description material out of raw image bytes;
it never writes notes or decides note type (pipeline/photo.py does that,
reusing classify.classify() on whatever text comes back here — exactly like
a voice memo's transcript). Never raises: an all-fail result still lets a
note be written with an empty extraction (Pass L's enrichment principle)."""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

TIMEOUT = 20  # vision payloads are heavier than text-only completions

OCR_MIN_CHARS = 40  # sidecar OCR at least this long short-circuits the whole chain

DEFAULT_CHAIN = ["gemini-flash", "openai-mini", "claude-haiku"]
FLOOR = "claude-haiku"

ENV_KEYS = {
    "gemini-flash": "GEMINI_API_KEY",
    "openai-mini": "OPENAI_API_KEY",
    "claude-haiku": "ANTHROPIC_API_KEY",
}

VISION_PROMPT = (
    "Look at this image, captured into a personal knowledge app. Return ONLY JSON with "
    "one key: extracted_text.\n"
    "extracted_text = any readable text visible in the image — a screenshot's text, a "
    "whiteboard, a book page, a recipe card, a slide — transcribed as faithfully as "
    "possible. If the image has no readable text (e.g. a plain photo), describe in one "
    "short line what the image shows instead. Never leave it empty."
)


@dataclass
class Attempt:
    provider: str
    outcome: str  # served | invalid-json | schema | timeout | rate-limit | error


class RateLimited(Exception):
    pass


def _post_json(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited() from e
        raise


def _call_gemini(image_b64: str, mime: str, key: str) -> str:
    data = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}",
        {"contents": [{"parts": [
            {"text": VISION_PROMPT},
            {"inline_data": {"mime_type": mime, "data": image_b64}},
        ]}]}, {})
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_openai_mini(image_b64: str, mime: str, key: str) -> str:
    data = _post_json(
        "https://api.openai.com/v1/chat/completions",
        {"model": "gpt-4o-mini", "messages": [{
            "role": "user", "content": [
                {"type": "text", "text": VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
            ]}]},
        {"Authorization": f"Bearer {key}"})
    return data["choices"][0]["message"]["content"]


def _call_claude(image_b64: str, mime: str, key: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model="claude-haiku-4-5", max_tokens=1024, timeout=TIMEOUT,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": image_b64}},
            {"type": "text", "text": VISION_PROMPT},
        ]}])
    return msg.content[0].text


PROVIDERS = {
    "gemini-flash": _call_gemini,
    "openai-mini": _call_openai_mini,
    "claude-haiku": _call_claude,
}


def chain(config) -> list[str]:
    configured = ((config.raw.get("vision") or {}).get("providers")
                  if getattr(config, "raw", None) else None) or DEFAULT_CHAIN
    known = [p for p in configured if p in PROVIDERS and p != FLOOR]
    return known + [FLOOR]


def _strip_fences(text: str) -> str:
    return re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()


def _validate(data: object) -> str | None:
    if not isinstance(data, dict):
        return "not a JSON object"
    if not str(data.get("extracted_text") or "").strip():
        return "empty extracted_text"
    return None


def complete_vision_json(image_bytes: bytes, mime: str, config) -> tuple[dict | None, str | None, list[Attempt]]:
    """Run the vision chain until a provider returns usable JSON. Same
    fall-through rules as llm.complete_json: invalid JSON / schema violation /
    timeout / rate-limit moves to the next provider; keyless providers are
    skipped silently; the floor always runs last; all-fail returns None."""
    image_b64 = base64.b64encode(image_bytes).decode()
    attempts: list[Attempt] = []
    for name in chain(config):
        key = os.environ.get(ENV_KEYS[name], "")
        if not key:
            continue
        try:
            text = PROVIDERS[name](image_b64, mime, key)
        except RateLimited:
            attempts.append(Attempt(name, "rate-limit"))
            continue
        except TimeoutError:
            attempts.append(Attempt(name, "timeout"))
            continue
        except Exception as e:
            outcome = "timeout" if "timed out" in str(e).lower() else "error"
            attempts.append(Attempt(name, outcome))
            continue
        try:
            data = json.loads(_strip_fences(text))
        except json.JSONDecodeError:
            attempts.append(Attempt(name, "invalid-json"))
            continue
        problem = _validate(data)
        if problem:
            attempts.append(Attempt(name, "schema"))
            continue
        attempts.append(Attempt(name, "served"))
        return data, name, attempts
    return None, None, attempts


def extract(image_bytes: bytes, mime: str, ocr_hint: str, config, router=None) -> tuple[str, str, list[Attempt]]:
    """Returns (extracted_text, source, attempts). source is "on-device-ocr"
    when the sidecar's own OCR text was good enough to skip every API call,
    else the provider name that served, else "none" when everything failed
    (extracted_text is "" in that case — the note still gets written)."""
    ocr_hint = (ocr_hint or "").strip()
    if len(ocr_hint) >= OCR_MIN_CHARS:
        return ocr_hint, "on-device-ocr", [Attempt("on-device-ocr", "served")]
    router = router or complete_vision_json
    data, provider, attempts = router(image_bytes, mime, config)
    if data is None:
        return "", "none", attempts
    return str(data.get("extracted_text", "")).strip(), provider, attempts
