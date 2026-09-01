"""first_words: a short, honest echo of what was captured — the confirmation
that builds trust through evidence (Pass A, B1), not a summary or paraphrase."""
from __future__ import annotations


def first_words(text: str, n: int = 10, max_chars: int = 60) -> str:
    """The first n words of text, further capped at max_chars, with a
    trailing ellipsis whenever either limit actually cut something off.
    Empty/whitespace-only text returns "" (no words heard, nothing to echo)."""
    words = text.split()
    if not words:
        return ""
    joined = " ".join(words[:n])
    truncated_by_words = len(words) > n
    if len(joined) > max_chars:
        return joined[:max_chars].rstrip() + "…"
    return joined + ("…" if truncated_by_words else "")
