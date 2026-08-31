"""Pass P — Devanagari → Hinglish. The note leads with Roman Hindi because that
is what the owner reads and searches; the original stays in the same note. The
rule these tests defend: a transliteration engine that is missing, misconfigured
or down costs formatting, never a capture."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pipeline import transliterate as tl

HINDI = "मैं कल दफ़्तर जाऊँगा"
ROMAN = "main kal daftar jaunga"


def cfg(block: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(raw={"transliteration": block} if block else {})


OLLAMA = {"engine": "ollama", "ollama": {"url": "http://localhost:11434", "model": "qwen2.5"}}
OPENROUTER = {"engine": "openrouter", "openrouter": {"model": "meta-llama/llama-3.1-8b-instruct"}}


def test_devanagari_detection():
    assert tl.has_devanagari(HINDI) is True
    assert tl.has_devanagari("just english here") is False
    assert tl.has_devanagari("mixed मैं text") is True


def test_engine_must_be_configured_to_be_used(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert tl.configured_engine(cfg()) == ""                       # nothing set
    assert tl.configured_engine(cfg({"engine": "ollama"})) == ""   # no model named
    assert tl.configured_engine(cfg(OLLAMA)) == "ollama"
    assert tl.configured_engine(cfg(OPENROUTER)) == "", "openrouter without a key is not usable"
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert tl.configured_engine(cfg(OPENROUTER)) == "openrouter"


def test_body_leads_with_hinglish_and_keeps_the_original():
    body = tl.compose_body(ROMAN, HINDI)
    assert body.startswith(ROMAN)
    assert tl.ORIGINAL_HEADING in body
    assert body.rstrip().endswith(HINDI)
    # one note, both scripts — never two notes (SCHEMA-REFERENCE.md §8)
    assert body.count(tl.ORIGINAL_HEADING) == 1


def test_body_is_devanagari_alone_when_transliteration_produced_nothing():
    assert tl.compose_body("", HINDI) == HINDI


def test_to_hinglish_uses_the_configured_engine():
    seen = []

    def caller(text, block):
        seen.append(text)
        return ROMAN

    out = tl.to_hinglish(HINDI, cfg(OLLAMA), caller=caller)
    assert out == ROMAN and seen == [HINDI]


def test_to_hinglish_is_empty_when_no_engine_is_configured():
    called = []
    assert tl.to_hinglish(HINDI, cfg(), caller=lambda t, b: called.append(t) or ROMAN) == ""
    assert called == [], "an unconfigured engine must not be called at all"


@pytest.mark.parametrize("boom", [OSError("refused"), TimeoutError(), ValueError("bad json")])
def test_engine_failure_degrades_to_empty_rather_than_raising(boom):
    def caller(text, block):
        raise boom

    # never raises: the watcher keeps the Devanagari transcript and writes the note
    assert tl.to_hinglish(HINDI, cfg(OLLAMA), caller=caller) == ""


def test_a_partial_conversion_is_refused():
    """Half-converted output would be worse than none — the note would silently
    lose the untranslated half's meaning to the reader."""
    calls = []

    def caller(text, block):
        calls.append(text)
        return ROMAN if len(calls) == 1 else ""

    long_text = "\n\n".join(" ".join(["शब्द"] * 1500) for _ in range(3))
    assert tl.to_hinglish(long_text, cfg(OLLAMA), caller=caller) == ""


def test_long_transcripts_go_up_in_pieces_and_come_back_stitched():
    def caller(text, block):
        return f"<{len(text.split())}>"

    long_text = "\n\n".join(" ".join(["शब्द"] * 1500) for _ in range(3))
    out = tl.to_hinglish(long_text, cfg(OLLAMA), caller=caller)
    assert out.count("<") == 3, "4500 words should not go up as one request"


def test_markers_and_placeholders_survive_the_prompt_contract():
    """The prompt must tell the model to keep chunk markers and the
    unintelligible placeholder — they are how a long note stays readable."""
    assert "[hh:mm]" in tl.PROMPT
    assert "unintelligible" in tl.PROMPT
    assert "do NOT translate" in tl.PROMPT
    assert "summarize" in tl.PROMPT


def test_an_engine_that_echoes_devanagari_back_is_treated_as_a_failure():
    """D9: some proxy or mis-instructed model just reflects the request back —
    that must never be trusted as 'transliterated', or the note would carry
    the same Devanagari text under both headings."""
    out = tl.to_hinglish(HINDI, cfg(OLLAMA), caller=lambda text, block: text)
    assert out == ""


def test_an_engine_that_stays_mostly_devanagari_is_treated_as_a_failure():
    """Not a byte-for-byte echo, but still mostly Devanagari — a bad or
    confused reply, not a real transliteration."""
    mostly_devanagari = HINDI + " ok"
    out = tl.to_hinglish(HINDI, cfg(OLLAMA), caller=lambda text, block: mostly_devanagari)
    assert out == ""


def test_a_genuine_transliteration_still_passes():
    out = tl.to_hinglish(HINDI, cfg(OLLAMA), caller=lambda text, block: ROMAN)
    assert out == ROMAN


def test_pieces_split_on_paragraph_boundaries():
    text = "\n\n".join(["para one", "para two", "para three"])
    assert tl._pieces(text, words_per_piece=100) == [text]      # small → one request
    pieces = tl._pieces("\n\n".join(" ".join(["w"] * 80) for _ in range(4)), words_per_piece=100)
    assert len(pieces) == 4 and all(p.count("\n\n") == 0 for p in pieces)
