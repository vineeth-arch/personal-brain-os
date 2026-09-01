"""Unit tests for pipeline/echo.py::first_words — the transcript/vision echo
that builds trust through evidence on capture confirmations (Pass A, B1)."""
from __future__ import annotations

from pipeline.echo import first_words


def test_exactly_n_words_no_ellipsis():
    text = "one two three four five six seven eight nine ten"
    assert len(text.split()) == 10
    assert first_words(text) == text


def test_more_than_n_words_ellipsis_and_only_first_n_kept():
    text = "one two three four five six seven eight nine ten eleven twelve"
    result = first_words(text)
    assert result == "one two three four five six seven eight nine ten…"
    assert "eleven" not in result


def test_short_text_under_both_limits_returned_verbatim():
    assert first_words("just a few words") == "just a few words"


def test_long_single_word_exceeding_max_chars_is_char_truncated():
    word = "a" * 100
    result = first_words(word)
    assert result == ("a" * 60) + "…"
    assert len(result) == 61


def test_empty_string_returns_empty():
    assert first_words("") == ""


def test_whitespace_only_returns_empty():
    assert first_words("   \n\t  ") == ""


def test_unicode_word_boundary_devanagari():
    # Hindi/Devanagari, more than 10 words — verify slicing at a word
    # boundary doesn't corrupt multi-byte/combining characters.
    words = ["मैं", "कल", "दफ़्तर", "जाऊँगा", "और", "मुझे", "डॉक्टर", "को",
             "कॉल", "करना", "है", "अभी"]
    text = " ".join(words)
    result = first_words(text)
    assert result == " ".join(words[:10]) + "…"
    assert "अभी" not in result
    # every surviving word round-trips cleanly through the result — no mojibake
    for w in words[:10]:
        assert w in result


def test_unicode_accented_text_word_boundary():
    text = "café déjà vu naïve résumé cliché fiancée château être élève"
    assert len(text.split()) == 10
    assert first_words(text) == text  # exactly 10 words, no ellipsis


def test_max_chars_cap_applies_even_within_n_words():
    # Fewer than n words, but their combined length still exceeds max_chars.
    text = "supercalifragilisticexpialidocious antidisestablishmentarianism"
    result = first_words(text, n=10, max_chars=20)
    assert result == text[:20].rstrip() + "…"
