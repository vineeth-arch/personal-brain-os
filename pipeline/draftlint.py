"""pipeline/draftlint.py — A9 draft linter and Anti-Seducer chips
(RELATIONSHIP-OS-ARCHITECTURE.md A9, A11.3-A11.5). Pure text analysis over a
draft string plus light touch context (channel, tier, touch_type, quiet);
stdlib only, no dependency on the other pipeline modules. Never raises."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

SOFTENERS = ("no rush", "no pressure", "whenever you get a chance", "whenever works", "just checking in",
             "touching base", "circling back", "hope this finds you well", "sorry to bother", "if you get time")
WA_MAX_LINES, EMAIL_MAX_WORDS = 4, 120        # A9.13
SEDUCER_ORDER = ("windbag", "moraliser", "tightwad", "reactor", "bumbler", "pushy")


@dataclass
class Lint:
    code: str
    start: int
    end: int
    snippet: str
    message: str


# Date patterns used by `no_date`: if any of these match, a date is present.
_DATE_PATTERNS = (
    r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b",
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
    r"\b\d{1,2}(st|nd|rd|th)\b",
    r"\b\d{1,2}[/-]\d{1,2}\b",
    r"\b(today|tomorrow|tonight|this week|next week)\b",
    r"\bby \d",
)

_SOFTENER_RE = re.compile(r"\b(?:" + "|".join(re.escape(s) for s in SOFTENERS) + r")\b", re.I)
_NO_DATE_TRIGGER = re.compile(r"let me know|get back to me|when you can", re.I)
_EM_DASH = re.compile(r"—| – ")
_EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_JUSTIFY_WORD = re.compile(
    r"\bbecause\b|\bsince\b|\bthe reason\b|\bthat's why\b|\bwhich is why\b|\bto be fair\b", re.I)
_COMPLAINT = re.compile(
    r"\bexhausted\b|\bswamped\b|\bfrustrat\w*\b|\bnightmare\b|\bfed up\b|\bso busy\b|\bannoying\b|\bcan't deal\b",
    re.I)
_OWN_WIN = re.compile(
    r"\bwe launched\b|\bi closed\b|\bwe won\b|\bjust signed\b|\bproud to announce\b|\bexcited to share\b|"
    r"\bfinally got\b", re.I)
_OWN_WIN_EXCLUDE = re.compile(
    r"\bthanks to\b|\bbecause of you\b|\byour\b|\bmonths\b|\brejected\b|\bstruggle\b|\bafter\b", re.I)
_FAVOURS = re.compile(
    r"\bafter everything\b|\bi've always helped\b|\byou owe me\b|\bi did for you\b|\breturn the favou?r\b|"
    r"\bremember when i helped\b", re.I)
_SELF_OPINION_EXCLUDE = re.compile(
    r"\byou know\b|\byour call\b|\bup to you\b|\bcorrect me\b|\byou're right that\b|\bi know you meant\b", re.I)

_MORALISER = re.compile(r"\byou should have\b|\byou need to\b|\byou ought to\b|\byou must\b", re.I)
_REACTOR_CAPS = re.compile(r"\b[A-Z]{4,}\b")  # case-sensitive, per spec
_BUMBLER = re.compile(
    r"\bsorry\b|\bapologi\w*\b|\bjust wanted\b|\bmaybe\b|\bhopefully\b|\bi think perhaps\b", re.I)
_PUSHY_SOFTEN = re.compile(r"\bif not\b|\bno problem\b|\byour call\b|\bif nobody\b", re.I)


def _from_match(code: str, match: re.Match, message: str, text: str) -> Lint:
    return Lint(code, match.start(), match.end(), text[match.start():match.end()], message)


def _whole(code: str, text: str, message: str) -> Lint:
    return Lint(code, 0, len(text), text, message)


def _non_empty_lines(text: str) -> list[str]:
    return [ln for ln in text.split("\n") if ln.strip()]


def _is_length_violation(text: str, channel: str) -> bool:
    if channel == "email":
        first_line, _, rest = text.partition("\n")
        body = rest if first_line.strip().lower().startswith("subject:") else text
        return len(body.split()) > EMAIL_MAX_WORDS
    return len(_non_empty_lines(text)) > WA_MAX_LINES


def _justify_span(text: str) -> tuple[int, int] | None:
    """Find the first run of >=3 consecutive sentences that each carry a
    justification word, and return its (start, end) span in `text`."""
    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    spans = []
    pos = 0
    for s in sentences:
        idx = text.index(s, pos)
        spans.append((idx, idx + len(s)))
        pos = idx + len(s)

    run_start = None
    run_len = 0
    for i, s in enumerate(sentences):
        if _JUSTIFY_WORD.search(s):
            if run_len == 0:
                run_start = i
            run_len += 1
            if run_len >= 3:
                return spans[run_start][0], spans[run_start + 2][1]
        else:
            run_len = 0
    return None


def lint(text: str, *, channel: str = "whatsapp", tier: str = "", touch_type: str = "",
         quiet: bool = False) -> dict:
    """Run the A9 lint rules and Anti-Seducer chips over a draft.

    Returns {"lints": [asdict(Lint), ...], "seducer": [chip, ...]}. Never
    raises — a blank or unparseable draft simply produces an empty result.
    """
    try:
        return _lint(text, channel=channel, tier=tier, touch_type=touch_type, quiet=quiet)
    except Exception:
        return {"lints": [], "seducer": []}


def _lint(text: str, *, channel: str, tier: str, touch_type: str, quiet: bool) -> dict:
    if not text or not text.strip():
        return {"lints": [], "seducer": []}

    lints: list[Lint] = []

    m = _SOFTENER_RE.search(text)
    if m:
        lints.append(_from_match("softener", m, "say the date and why", text))

    needs_date = touch_type in ("ask", "invite") or bool(_NO_DATE_TRIGGER.search(text))
    if needs_date and not any(re.search(p, text, re.I) for p in _DATE_PATTERNS):
        lints.append(_whole("no_date", text, "Add a date"))

    m = _EM_DASH.search(text)
    if m:
        lints.append(_from_match("em_dash", m, "no em dashes", text))

    m = _EMOJI.search(text)
    if m:
        lints.append(_from_match("emoji", m, "no emojis", text))

    length_hit = _is_length_violation(text, channel)
    if length_hit:
        lints.append(_whole("length", text, "say less"))

    justify_span = _justify_span(text)
    if justify_span:
        start, end = justify_span
        lints.append(Lint("justify", start, end, text[start:end], "state it once, then offer a choice"))

    if tier != "inner":
        m = _COMPLAINT.search(text)
        if m:
            lints.append(_from_match("complaint", m, "keep this for the inner circle", text))

    m = _OWN_WIN.search(text)
    own_win_hit = bool(m) and not _OWN_WIN_EXCLUDE.search(text)
    if own_win_hit:
        lints.append(_from_match("own_win", m, "name the struggle or credit someone", text))

    m = _FAVOURS.search(text)
    if m:
        lints.append(_from_match("favours", m, "appeal to what they gain", text))

    if touch_type == "kind_truth" and not _SELF_OPINION_EXCLUDE.search(text):
        lints.append(_whole("self_opinion", text, "leave the decision with them"))

    chips = set()
    if length_hit:
        chips.add("windbag")
    if _MORALISER.search(text):
        chips.add("moraliser")
    if own_win_hit:
        chips.add("tightwad")
    if text.count("!") >= 2 or _REACTOR_CAPS.search(text):
        chips.add("reactor")
    if len(_BUMBLER.findall(text)) >= 2:
        chips.add("bumbler")
    if quiet or (touch_type == "ask" and not _PUSHY_SOFTEN.search(text)):
        chips.add("pushy")

    seducer = [c for c in SEDUCER_ORDER if c in chips]

    return {"lints": [asdict(l) for l in lints], "seducer": seducer}
