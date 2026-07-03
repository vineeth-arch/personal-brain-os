"""Model router: one prompt, an ordered fallback chain of providers.

Hard rules (Pass B):
- The IDENTICAL prompt goes to every provider.
- A response must parse as JSON and validate against the caller's schema;
  invalid JSON / schema violation / timeout (10s) / rate-limit → fall through
  to the next provider.
- Keys come from the environment ONLY; keyless providers are skipped silently.
- claude-haiku is the floor and always runs last.
- If every provider fails, the caller gets None — needs-review, never a guess.

Every attempt is reported back so the watcher can log provider stats to
events.db (stage='llm'), which GET /api/providers aggregates.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

TIMEOUT = 10

DEFAULT_CHAIN = ["gemini-flash", "groq-llama-3.3-70b", "openrouter-free", "claude-haiku"]
FLOOR = "claude-haiku"

ENV_KEYS = {
    "gemini-flash": "GEMINI_API_KEY",
    "groq-llama-3.3-70b": "GROQ_API_KEY",
    "openrouter-free": "OPENROUTER_API_KEY",
    "claude-haiku": "ANTHROPIC_API_KEY",
}


@dataclass
class Attempt:
    provider: str
    outcome: str            # served | invalid-json | schema | timeout | rate-limit | error
    confidence: float | None = None


class RateLimited(Exception):
    pass


# ---- provider calls (identical prompt in, raw text out; raise on failure) ------

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


def _call_gemini(prompt: str, key: str) -> str:
    data = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}",
        {"contents": [{"parts": [{"text": prompt}]}]}, {})
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_openai_style(url: str, model: str, prompt: str, key: str) -> str:
    data = _post_json(
        url, {"model": model, "messages": [{"role": "user", "content": prompt}]},
        {"Authorization": f"Bearer {key}"})
    return data["choices"][0]["message"]["content"]


def _call_groq(prompt: str, key: str) -> str:
    return _call_openai_style("https://api.groq.com/openai/v1/chat/completions",
                              "llama-3.3-70b-versatile", prompt, key)


def _call_openrouter(prompt: str, key: str) -> str:
    return _call_openai_style("https://openrouter.ai/api/v1/chat/completions",
                              "meta-llama/llama-3.3-70b-instruct:free", prompt, key)


def _call_claude(prompt: str, key: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(model="claude-haiku-4-5", max_tokens=1024,
                                 timeout=TIMEOUT,
                                 messages=[{"role": "user", "content": prompt}])
    return msg.content[0].text


PROVIDERS = {
    "gemini-flash": _call_gemini,
    "groq-llama-3.3-70b": _call_groq,
    "openrouter-free": _call_openrouter,
    "claude-haiku": _call_claude,
}


# ---- the chain ------------------------------------------------------------------

def chain(config) -> list[str]:
    configured = ((config.raw.get("classification") or {}).get("providers")
                  if getattr(config, "raw", None) else None) or DEFAULT_CHAIN
    known = [p for p in configured if p in PROVIDERS and p != FLOOR]
    return known + [FLOOR]   # the floor stays last, always present


def _strip_fences(text: str) -> str:
    return re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()


def complete_json(prompt: str, config, validate_fn) -> tuple[dict | None, str | None, list[Attempt]]:
    """Run the chain until a provider returns schema-valid JSON. validate_fn
    returns an error string or None. Returns (data, provider, attempts);
    data None = every provider failed → the caller must go to needs-review."""
    attempts: list[Attempt] = []
    for name in chain(config):
        key = os.environ.get(ENV_KEYS[name], "")
        if not key:
            continue  # keyless providers are skipped silently — no attempt row
        try:
            text = PROVIDERS[name](prompt, key)
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
        problem = validate_fn(data)
        if problem:
            attempts.append(Attempt(name, "schema"))
            continue
        confidence = data.get("confidence") if isinstance(data, dict) else None
        attempts.append(Attempt(name, "served",
                                float(confidence) if isinstance(confidence, (int, float)) else None))
        return data, name, attempts
    return None, None, attempts


def complete_text(prompt: str, config) -> tuple[str | None, str | None, list[Attempt]]:
    """Same chain, raw text out (the caller validates) — used by extraction."""
    attempts: list[Attempt] = []
    for name in chain(config):
        key = os.environ.get(ENV_KEYS[name], "")
        if not key:
            continue
        try:
            text = PROVIDERS[name](prompt, key)
        except RateLimited:
            attempts.append(Attempt(name, "rate-limit"))
            continue
        except Exception as e:
            outcome = "timeout" if "timed out" in str(e).lower() else "error"
            attempts.append(Attempt(name, outcome))
            continue
        attempts.append(Attempt(name, "served"))
        return text, name, attempts
    return None, None, attempts
