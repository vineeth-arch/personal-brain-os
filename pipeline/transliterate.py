"""Stage 2b — transliterate. Hindi (Devanagari) → Roman Hindi / Hinglish.

Whisper returns Hindi speech in Devanagari, which is faithful but not what the
owner of this vault reads or searches in. So the transcript is converted to
Roman Hindi/Hinglish before classification, and the Devanagari original is kept
in the same note under its own heading — one recording is still one note
(SCHEMA-REFERENCE.md §8), nothing is thrown away, and nothing is summarized.

Two engines, chosen in config.json (`transliteration.engine`):
  - "ollama"     — a local OpenAI-compatible server (no key, url + model)
  - "openrouter" — the cloud fallback, keyed by OPENROUTER_API_KEY

Both are hand-rolled stdlib HTTP calls (CLAUDE.md §7 locks the dependency
list). If neither is configured, or the call fails, the capture still becomes a
note with the Devanagari transcript — a transliteration outage must never cost
a recording.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

TIMEOUT = 120
DEFAULT_OLLAMA_URL = "http://localhost:11434"
ORIGINAL_HEADING = "## Original (Devanagari)"

# Long transcripts go up in pieces so no single request has to carry a
# two-hour meeting. Chunking here is by words, on paragraph boundaries.
WORDS_PER_PIECE = 2000

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

PROMPT = (
    "Transliterate the following Hindi text from Devanagari script into Roman "
    "Hindi (Hinglish), exactly as an Indian speaker would type it in a chat "
    "message.\n"
    "Rules:\n"
    "- Transliterate, do NOT translate. Hindi words stay Hindi, just in Roman "
    "letters.\n"
    "- Keep English words that appear in the text as they are.\n"
    "- Preserve every line break, every [hh:mm] marker and every [... "
    "unintelligible ...] placeholder exactly where it appears.\n"
    "- Keep any #hashtag exactly as written and in the same position — a "
    "spoken #tag at the start routes the note, so dropping it changes where "
    "the capture lands.\n"
    "- Do not summarize, shorten, explain, or add anything. Return only the "
    "transliterated text.\n\n"
    "Text:\n"
)


def has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI.search(text))


def configured_engine(config) -> str:
    """The engine that can actually run right now, or "" if none can."""
    block = (getattr(config, "raw", {}) or {}).get("transliteration") or {}
    engine = (block.get("engine") or "").strip().lower()
    if engine == "ollama":
        return "ollama" if (block.get("ollama") or {}).get("model") else ""
    if engine == "openrouter":
        has_key = bool(os.environ.get("OPENROUTER_API_KEY"))
        return "openrouter" if has_key and (block.get("openrouter") or {}).get("model") else ""
    return ""


def _post(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


def _call_ollama(text: str, block: dict) -> str:
    base = (block.get("ollama") or {}).get("url") or DEFAULT_OLLAMA_URL
    model = (block.get("ollama") or {}).get("model")
    data = _post(f"{base.rstrip('/')}/api/chat", {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT + text}],
        "stream": False,
    }, {})
    return (data.get("message") or {}).get("content", "").strip()


def _call_openrouter(text: str, block: dict) -> str:
    model = (block.get("openrouter") or {}).get("model")
    data = _post("https://openrouter.ai/api/v1/chat/completions", {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT + text}],
    }, {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"})
    return data["choices"][0]["message"]["content"].strip()


ENGINES = {"ollama": _call_ollama, "openrouter": _call_openrouter}


def _pieces(text: str, words_per_piece: int = WORDS_PER_PIECE) -> list[str]:
    """Split on blank lines, grouping paragraphs up to the word budget so
    [hh:mm] markers and placeholders always travel with their own text."""
    paragraphs = text.split("\n\n")
    pieces, current, count = [], [], 0
    for para in paragraphs:
        words = len(para.split())
        if current and count + words > words_per_piece:
            pieces.append("\n\n".join(current))
            current, count = [], 0
        current.append(para)
        count += words
    if current:
        pieces.append("\n\n".join(current))
    return pieces or [text]


def to_hinglish(text: str, config, *, caller=None) -> str:
    """Roman-Hindi version of `text`, or "" if it can't be produced.

    Never raises — the caller keeps the Devanagari transcript when this returns
    empty, so a missing or unreachable engine costs formatting, not a capture.
    """
    engine = configured_engine(config)
    if not engine or not text.strip():
        return ""
    call = caller or ENGINES[engine]
    block = (getattr(config, "raw", {}) or {}).get("transliteration") or {}
    out = []
    for piece in _pieces(text):
        try:
            converted = call(piece, block)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
                KeyError, ValueError, json.JSONDecodeError):
            return ""      # a partly-converted transcript would be worse than none
        if not converted:
            return ""
        out.append(converted)
    return "\n\n".join(out).strip()


def compose_body(hinglish: str, original: str) -> str:
    """The note body: what the owner reads first, with the original kept below."""
    if not hinglish:
        return original.rstrip()
    return f"{hinglish.rstrip()}\n\n{ORIGINAL_HEADING}\n\n{original.rstrip()}"
