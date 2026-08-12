"""Decision journal (Pass 8): Brier arithmetic, calibration bucketing, and the
rule that a probability is only ever recorded when it was actually spoken."""
from __future__ import annotations

import pytest

from pipeline import decisions, notefm


# ---- scoring --------------------------------------------------------------------

@pytest.mark.parametrize("probability,outcome,expected", [
    (100, True, 0.0),        # certain and right
    (100, False, 1.0),       # certain and wrong — the worst possible score
    (0, False, 0.0),
    (70, True, 0.09),
    (70, False, 0.49),
    (50, True, 0.25),        # a coin flip always scores 0.25 either way
    (50, False, 0.25),
])
def test_brier_score(probability, outcome, expected):
    assert decisions.brier_score(probability, outcome) == pytest.approx(expected)


def test_no_stated_probability_scores_none_not_zero():
    """A missing forecast must not read as a perfect one."""
    assert decisions.brier_score(None, True) is None
    assert decisions.brier_score(None, False) is None


@pytest.mark.parametrize("raw,expected", [
    ("70", 70), (70, 70), ("70%", 70), ("0", 0), ("100", 100), ("69.6", 70),
    ("", None), (None, None), ("soon", None), ("150", None), ("-10", None),
])
def test_parse_probability(raw, expected):
    assert decisions.parse_probability(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("3", 3), (5, 5), ("1", 1), ("0", None), ("6", None), ("B", None), (None, None),
])
def test_parse_grade_is_the_1_to_5_scale(raw, expected):
    assert decisions.parse_grade(raw) == expected


# ---- capture-time extraction -------------------------------------------------------

def llm_returning(text):
    return lambda prompt, config: text


def test_a_spoken_probability_is_stored():
    assert decisions.resolve_probability("I'd say 70%.", None,
                                         llm_fn=llm_returning('{"probability": 70}')) == 70


def test_no_stated_probability_stays_none():
    assert decisions.resolve_probability("I think this will probably work out.", None,
                                         llm_fn=llm_returning('{"probability": null}')) is None


def test_a_model_that_invents_a_probability_is_still_bounded_by_the_schema():
    """The prompt forbids inventing one; the validator is the second line of
    defence for anything outside 0–100."""
    assert decisions.resolve_probability("no numbers here", None,
                                         llm_fn=llm_returning('{"probability": 420}')) is None
    assert decisions.resolve_probability("no numbers here", None,
                                         llm_fn=llm_returning('{"probability": "high"}')) is None


def test_falls_back_to_a_single_unambiguous_percentage_without_a_model():
    """No provider available: one written-out percentage is safe to read; zero
    or several are not. The fallback is deliberately literal — reading
    'seventy percent' takes a model, and the cost of it being wrong here is a
    fabricated forecast, so it declines instead."""
    none_llm = llm_returning(None)
    assert decisions.resolve_probability("I'd say a 70% chance.", None, llm_fn=none_llm) == 70
    assert decisions.resolve_probability("a 70 percent chance", None, llm_fn=none_llm) == 70
    assert decisions.resolve_probability("no number stated", None, llm_fn=none_llm) is None
    assert decisions.resolve_probability("seventy percent maybe", None, llm_fn=none_llm) is None
    # two numbers = which one is the forecast? unanswerable, so nothing is stored
    assert decisions.resolve_probability("60% for us, 40% for them", None,
                                         llm_fn=none_llm) is None


def test_malformed_model_output_never_raises():
    for text in ("not json", "```json\n{oops}\n```", "[]", '{"other": 1}'):
        assert decisions.resolve_probability("a decision", None,
                                             llm_fn=llm_returning(text)) is None

    def boom(prompt, config):
        raise RuntimeError("provider exploded")

    assert decisions.resolve_probability("a decision", None, llm_fn=boom) is None


def test_stamp_probability_writes_an_explicit_blank_for_none():
    note = "---\nid: 20260101090000\ntype: decision\nstatus: open\n---\n\nThe claim.\n"
    assert "probability: 70" in decisions.stamp_probability(note, 70)
    stamped = decisions.stamp_probability(note, None)
    assert "probability:" in stamped and "probability: None" not in stamped
    assert "The claim." in stamped


# ---- calibration -------------------------------------------------------------------

@pytest.mark.parametrize("probability,index", [
    (0, 0), (9, 0), (10, 1), (55, 5), (99, 9), (100, 9),   # 100 lands in the last bucket
])
def test_bucket_index(probability, index):
    assert decisions.bucket_index(probability) == index


def test_calibration_counts_only_scored_decisions():
    resolved = [
        {"probability": 70, "outcome": True},
        {"probability": 75, "outcome": True},
        {"probability": 72, "outcome": False},
        {"probability": 20, "outcome": False},
        {"probability": None, "outcome": True},   # never forecast — must not count
        {"probability": 90, "outcome": None},     # not resolved — must not count
    ]
    buckets = decisions.calibration_buckets(resolved)
    seventies = buckets[7]
    assert seventies["count"] == 3 and seventies["hits"] == 2
    assert seventies["actual"] == pytest.approx(2 / 3, abs=1e-4)
    assert seventies["label"] == "70–80%"
    assert buckets[2]["count"] == 1 and buckets[2]["actual"] == 0.0
    assert buckets[9]["count"] == 0 and buckets[9]["actual"] is None   # not 0.0
    assert sum(b["count"] for b in buckets) == 4


def test_calibration_of_nothing_is_ten_empty_buckets():
    buckets = decisions.calibration_buckets([])
    assert len(buckets) == 10
    assert all(b["count"] == 0 and b["actual"] is None for b in buckets)


# ---- capture-time wiring (the watcher stamps the note) -------------------------------

def test_watcher_stamps_a_spoken_probability_onto_the_decision_note(tmp_path, monkeypatch):
    """End-to-end: a decision capture that states a probability lands with it in
    frontmatter; the note is written whether or not the extraction works."""
    from pipeline import watcher
    from pipeline.config import Config
    from pipeline.events import EventLog

    vault, inbox = tmp_path / "vault", tmp_path / "inbox"
    for d in (vault, inbox, tmp_path / "archive", tmp_path / "failed"):
        d.mkdir()
    (inbox / "2026-08-12-0900 bet.md").write_text(
        "#decision I'd say 70% we ship before the conference.")
    monkeypatch.setattr(watcher, "DB_PATH", tmp_path / "events.db")
    monkeypatch.setattr(watcher, "HEARTBEAT_PATH", tmp_path / ".hb")

    config = Config(vault_path=vault, inbox_path=inbox, archive_path=tmp_path / "archive",
                    failed_path=tmp_path / "failed")
    events = EventLog(tmp_path / "events.db", vault)
    # extract_llm is the injected seam for both action items and probability
    deps = watcher.Deps(transcriber=None, extract_llm=lambda prompt, cfg:
                        '{"probability": 70}' if "probability" in prompt else "[]")

    results = watcher.run_once(config, events, deps)
    assert results[0].status != "failed"

    (path,) = list((vault / decisions.DECISIONS_FOLDER).glob("*.md"))
    fm, body = notefm.parse_frontmatter(path.read_text())
    assert fm["probability"] == "70"
    assert fm["status"] == "open"
    assert "we ship before the conference" in body      # transcript kept whole
    events.close()


def test_a_decision_without_a_probability_still_lands_whole(tmp_path, monkeypatch):
    from pipeline import watcher
    from pipeline.config import Config
    from pipeline.events import EventLog

    vault, inbox = tmp_path / "vault", tmp_path / "inbox"
    for d in (vault, inbox, tmp_path / "archive", tmp_path / "failed"):
        d.mkdir()
    (inbox / "2026-08-12-0900 bet.md").write_text(
        "#decision We should probably hire in Q3.")
    monkeypatch.setattr(watcher, "DB_PATH", tmp_path / "events.db")
    monkeypatch.setattr(watcher, "HEARTBEAT_PATH", tmp_path / ".hb")

    config = Config(vault_path=vault, inbox_path=inbox, archive_path=tmp_path / "archive",
                    failed_path=tmp_path / "failed")
    events = EventLog(tmp_path / "events.db", vault)

    # no provider serves — the router's contract is to return None, not raise
    deps = watcher.Deps(transcriber=None, extract_llm=lambda prompt, cfg: None)
    results = watcher.run_once(config, events, deps)
    assert results[0].status != "failed"       # a decoration failure never fails a capture

    (path,) = list((vault / decisions.DECISIONS_FOLDER).glob("*.md"))
    fm, body = notefm.parse_frontmatter(path.read_text())
    assert fm.get("probability", "") == ""     # nothing stated, nothing guessed
    assert "hire in Q3" in body
    events.close()
