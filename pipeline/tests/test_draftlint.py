"""pipeline/tests/test_draftlint.py — A9 draft linter and Anti-Seducer chips
(RELATIONSHIP-OS-ARCHITECTURE.md A9, A11.3-A11.5). Pure text tests, no vault."""
from __future__ import annotations

from pipeline.draftlint import lint


def _codes(result):
    return {l["code"] for l in result["lints"]}


def test_blank_text_empty_result():
    assert lint("") == {"lints": [], "seducer": []}
    assert lint("   \n  ") == {"lints": [], "seducer": []}


# --- softener ---

def test_softener_present():
    result = lint("No rush, whenever you get a chance.")
    assert "softener" in _codes(result)


def test_softener_absent():
    result = lint("Let's meet Thursday at 4.")
    assert "softener" not in _codes(result)


# --- no_date ---

def test_no_date_missing_on_ask():
    result = lint("Can you send the file over?", touch_type="ask")
    assert "no_date" in _codes(result)


def test_no_date_present_on_ask():
    result = lint("Can you send the file over by Friday?", touch_type="ask")
    assert "no_date" not in _codes(result)


# --- em_dash ---

def test_em_dash_present():
    result = lint("We shipped it — finally.")
    assert "em_dash" in _codes(result)


def test_em_dash_absent():
    result = lint("We shipped it, finally.")
    assert "em_dash" not in _codes(result)


# --- emoji ---

def test_emoji_present():
    result = lint("Great news 🎉")
    assert "emoji" in _codes(result)


def test_emoji_absent():
    result = lint("Great news")
    assert "emoji" not in _codes(result)


# --- length ---

def test_length_whatsapp_too_many_lines():
    result = lint("one\ntwo\nthree\nfour\nfive")
    assert "length" in _codes(result)


def test_length_whatsapp_within_limit():
    result = lint("one\ntwo\nthree\nfour")
    assert "length" not in _codes(result)


def test_length_email_too_many_words():
    result = lint("Subject: Update\n" + "word " * 121, channel="email")
    assert "length" in _codes(result)


def test_length_email_within_limit():
    result = lint("Subject: Update\n" + "word " * 100, channel="email")
    assert "length" not in _codes(result)


# --- justify ---

def test_justify_three_consecutive():
    text = ("I'm late because the vendor slipped. This matters since it affects billing. "
            "That's why I want to flag it now.")
    result = lint(text)
    assert "justify" in _codes(result)


def test_justify_two_consecutive_is_clean():
    text = "I'm late because the vendor slipped. This matters since it affects billing. Let's talk tomorrow."
    result = lint(text)
    assert "justify" not in _codes(result)


# --- complaint ---

def test_complaint_outside_inner_tier():
    result = lint("I'm so swamped this week.", tier="core")
    assert "complaint" in _codes(result)


def test_complaint_inside_inner_tier_is_clean():
    result = lint("I'm so swamped this week.", tier="inner")
    assert "complaint" not in _codes(result)


# --- own_win ---

def test_own_win_without_credit():
    result = lint("We launched the new site today.")
    assert "own_win" in _codes(result)


def test_own_win_with_credit_is_clean():
    result = lint("We launched the new site today, thanks to your help.")
    assert "own_win" not in _codes(result)


# --- favours ---

def test_favours_present():
    result = lint("After everything I've done, you owe me this.")
    assert "favours" in _codes(result)


def test_favours_absent():
    result = lint("Happy to help with this.")
    assert "favours" not in _codes(result)


# --- self_opinion ---

def test_self_opinion_missing_on_kind_truth():
    result = lint("This part of the plan won't work.", touch_type="kind_truth")
    assert "self_opinion" in _codes(result)


def test_self_opinion_present_on_kind_truth_is_clean():
    result = lint("You know this best, so correct me if I'm wrong here.", touch_type="kind_truth")
    assert "self_opinion" not in _codes(result)


# --- seducer chips ---

def test_windbag_from_length():
    result = lint("one\ntwo\nthree\nfour\nfive")
    assert "windbag" in result["seducer"]


def test_windbag_absent_when_short():
    result = lint("one\ntwo")
    assert "windbag" not in result["seducer"]


def test_moraliser_present():
    result = lint("You need to fix this now.")
    assert "moraliser" in result["seducer"]


def test_moraliser_absent():
    result = lint("Could you take a look at this?")
    assert "moraliser" not in result["seducer"]


def test_tightwad_from_own_win():
    result = lint("I closed the deal today.")
    assert "tightwad" in result["seducer"]


def test_tightwad_absent():
    result = lint("I closed the deal today, thanks to your help.")
    assert "tightwad" not in result["seducer"]


def test_reactor_from_exclamations():
    result = lint("This is great! Amazing!")
    assert "reactor" in result["seducer"]


def test_reactor_from_caps():
    result = lint("This is URGENT please read")
    assert "reactor" in result["seducer"]


def test_reactor_absent():
    result = lint("This is good, thanks.")
    assert "reactor" not in result["seducer"]


def test_bumbler_present():
    result = lint("Sorry, maybe I'm wrong, hopefully this makes sense.")
    assert "bumbler" in result["seducer"]


def test_bumbler_absent_single_hit():
    result = lint("Sorry about that.")
    assert "bumbler" not in result["seducer"]


def test_pushy_from_quiet():
    result = lint("Just checking on the invoice.", quiet=True)
    assert "pushy" in result["seducer"]


def test_pushy_from_ask_without_out():
    result = lint("Send me the file by Friday.", touch_type="ask")
    assert "pushy" in result["seducer"]


def test_pushy_absent_when_ask_has_out():
    result = lint("Send me the file by Friday, no problem if you can't.", touch_type="ask")
    assert "pushy" not in result["seducer"]


# --- reference scripts (A11.5) ---

def test_reference_remember_script_is_clean():
    text = "Rohan, Aarav's board results were due this week. How did he do?"
    result = lint(text, touch_type="remember", tier="core")
    assert result == {"lints": [], "seducer": []}


def test_reference_kind_truth_script_is_clean():
    text = (
        "You know this category far better than I do, so correct me if I've read it wrong. "
        "On the new pack, the claim you care most about is the smallest thing on the front. "
        "I can show you two ways to fix that on Thursday. Your call whether it's worth changing."
    )
    result = lint(text, touch_type="kind_truth")
    assert result == {"lints": [], "seducer": []}


def test_spans_point_at_snippet():
    text = "We shipped it — finally, no rush on the follow up."
    result = lint(text)
    assert result["lints"]
    for l in result["lints"]:
        assert text[l["start"]:l["end"]] == l["snippet"]
