"""Decision notes — stated probability in, Brier score out.

Two rules carry this module, and both are about not lying to yourself:

1. **A probability is only ever captured when it was actually spoken.** If a
   recording says "I'd say 70%", that number is stored; if it doesn't, the field
   stays null and the decision simply never appears on the calibration chart.
   An inferred probability would be the model's confidence wearing the owner's
   name, and it would corrupt the one measurement this whole feature exists to
   produce.
2. **Resolving is allowed without one.** Closing the loop on a bet matters even
   when there's no number to score, so a decision with `probability: null`
   resolves normally with `brier: null` — recorded, just not plotted.

Frontmatter and the `open → resolved (+ brier, process_grade)` lifecycle are
SCHEMA-REFERENCE.md §6/§7 verbatim.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from . import llm, notefm, route

DECISIONS_FOLDER = route.TYPE_FOLDER["decision"]     # "09-Decisions"

OPEN, RESOLVED = "open", "resolved"

# The owner's self-rated process grade, 1–5 (graded on PROCESS, not outcome).
GRADE_MIN, GRADE_MAX = 1, 5

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
# "70%", "70 percent", "0.7 chance" — a single unambiguous statement only.
_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|percent\b)", re.IGNORECASE)


def decision_notes(vault: Path) -> Iterator[tuple[Path, dict[str, str], str]]:
    folder = Path(vault) / DECISIONS_FOLDER
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*.md")):
        try:
            fm, body = notefm.parse_frontmatter(path.read_text())
        except OSError:
            continue
        if fm.get("type") == "decision":
            yield path, fm, body


def find_decision(vault: Path, note_id: str) -> Path | None:
    for path, fm, _ in decision_notes(vault):
        if fm.get("id") == note_id:
            return path
    return None


def decision_title(path: Path, fm: dict[str, str]) -> str:
    return fm.get("title") or _DATE_PREFIX_RE.sub("", path.stem)


def parse_probability(value) -> int | None:
    """A stated probability as a whole 0–100, or None. Out-of-range values are
    None rather than clamped: 150% isn't a probability, it's a parse error."""
    if value is None or value == "":
        return None
    try:
        number = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    if not (0 <= number <= 100):
        return None
    return int(round(number))


def parse_grade(value) -> int | None:
    try:
        grade = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return grade if GRADE_MIN <= grade <= GRADE_MAX else None


def brier_score(probability: int | None, outcome: bool) -> float | None:
    """(p - outcome)^2 on a 0..1 scale. None when no probability was stated —
    the decision still resolves, it just can't be scored."""
    if probability is None:
        return None
    return round((probability / 100 - (1.0 if outcome else 0.0)) ** 2, 4)


# ---- capture-time probability extraction ---------------------------------------

def probability_prompt(transcript: str) -> str:
    return (
        "Read the decision below and report the probability the speaker STATED.\n"
        'Return ONLY JSON: {"probability": integer 0-100 | null}\n'
        "Rules: report a number ONLY if the speaker actually said one (\"70%\", "
        "\"seventy percent\", \"a 1 in 4 chance\"). If they expressed confidence in "
        "words alone (\"pretty sure\", \"likely\", \"probably\"), or stated no "
        "probability at all, return null. NEVER estimate, infer, or supply your own "
        "probability — a number you invented would be recorded as the speaker's own "
        "forecast and would corrupt their calibration record.\n"
        "If several probabilities appear and it isn't obvious which is the "
        "speaker's forecast for this decision, return null.\n\n"
        f"Decision:\n{transcript}"
    )


def _regex_probability(transcript: str) -> int | None:
    """The no-model fallback: exactly one percentage in the text, or nothing."""
    matches = _PERCENT_RE.findall(transcript)
    if len(matches) != 1:
        return None            # zero is nothing stated; several is ambiguous
    return parse_probability(matches[0])


def resolve_probability(transcript: str, config, llm_fn=None) -> int | None:
    """The probability the speaker stated, or None. Every failure path returns
    None — a decision without a number is the normal case, not an error."""
    llm_fn = llm_fn or (lambda prompt, cfg: llm.complete_text(prompt, cfg)[0])
    try:
        text = llm_fn(probability_prompt(transcript), config)
    except Exception:
        text = None
    if not text:
        return _regex_probability(transcript)
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return _regex_probability(transcript)
    if not isinstance(data, dict):
        return _regex_probability(transcript)
    return parse_probability(data.get("probability"))


def stamp_probability(text: str, probability: int | None) -> str:
    """Write `probability:` into a decision note. A None is written as an empty
    field: the schema names it, and an explicit blank reads as 'none stated'
    rather than 'this note is from another era'."""
    split = notefm.split_note(text)
    if split is None:
        return text
    fm_block, body = split
    value = "" if probability is None else str(probability)
    return notefm.compose_note(notefm.stamp_field(fm_block, "probability", value), body)


# ---- calibration ------------------------------------------------------------------

def bucket_index(probability: int) -> int:
    """0–10% → 0 … 90–100% → 9. 100 lands in the last bucket, not an 11th."""
    return min(9, max(0, probability // 10))


def calibration_buckets(resolved: list[dict]) -> list[dict]:
    """Ten buckets of (stated probability → how often it actually happened).

    Only resolved decisions that carry a stated probability are counted; the
    rest were never forecasts and would drag the curve toward whatever the
    scorer assumed about them."""
    buckets = [{"bucket": i, "label": f"{i * 10}–{i * 10 + 10}%", "count": 0,
                "hits": 0, "actual": None, "midpoint": i * 10 + 5} for i in range(10)]
    for decision in resolved:
        probability = parse_probability(decision.get("probability"))
        outcome = decision.get("outcome")
        if probability is None or outcome is None:
            continue
        b = buckets[bucket_index(probability)]
        b["count"] += 1
        if outcome:
            b["hits"] += 1
    for b in buckets:
        if b["count"]:
            b["actual"] = round(b["hits"] / b["count"], 4)
    return buckets
