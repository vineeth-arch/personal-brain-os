"""Decision endpoints (Pass 8): resolving restamps the note and commits, the
Brier score is only computed when a probability was actually stated, and the
calibration payload is rebuilt from the vault."""
from __future__ import annotations

import subprocess
from datetime import date

from pipeline import decisions, notefm

from .test_api import TOKEN, Server, env  # noqa: F401  (env is a fixture)


def _write_decision(vault, note_id, claim, *, probability=None, status="open",
                    created="2026-01-01", resolves="2026-09-01"):
    folder = vault / decisions.DECISIONS_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        "---", f"id: {note_id}", "type: decision", f"created: {created}",
        "source: voice", "origin: human", f"status: {status}", f"claim: {claim}",
        "outside_view: base rate is about a third", f"resolves: {resolves}",
    ]
    lines.append(f"probability: {probability if probability is not None else ''}")
    lines.append("---")
    body = "## Reasoning\nThe sub-questions that must be true.\n\n## Disconfirmers\nI am wrong if…"
    (folder / f"{created}-{note_id}.md").write_text("\n".join(lines) + "\n\n" + body + "\n")


def _git_log(vault):
    out = subprocess.run(["git", "-C", str(vault), "log", "--format=%s"],
                         capture_output=True, text=True).stdout
    return out.strip().splitlines()


def _note_text(vault, note_id):
    path = decisions.find_decision(vault, note_id)
    assert path is not None
    return path.read_text()


def test_missing_folder_is_an_empty_list_not_an_error(env):
    root, _, _, _ = env
    with Server(root) as s:
        code, body = s.req("GET", "/api/decisions")
        assert code == 200 and body["items"] == []
        assert body["calibration"]["resolved_count"] == 0
        assert len(body["calibration"]["buckets"]) == 10
        assert body["calibration"]["mean_brier"] is None


def test_resolving_with_a_stated_probability_scores_it(env):
    root, vault, _, _ = env
    _write_decision(vault, "20260101090001", "The launch slips", probability=70)
    with Server(root) as s:
        code, body = s.req("POST", "/api/decisions/20260101090001/resolve",
                           {"outcome": True, "process_grade": 4})
        assert code == 200
        assert body["status"] == "resolved" and body["outcome"] is True
        assert body["brier"] == 0.09 and body["process_grade"] == 4
        assert body["resolved"] == date.today().isoformat()

        _, listed = s.req("GET", "/api/decisions")
        cal = listed["calibration"]
        assert cal["resolved_count"] == 1 and cal["scored_count"] == 1
        assert cal["mean_brier"] == 0.09 and cal["mean_process_grade"] == 4.0
        assert cal["buckets"][7]["count"] == 1 and cal["buckets"][7]["actual"] == 1.0

    text = _note_text(vault, "20260101090001")
    fm, body_text = notefm.parse_frontmatter(text)
    assert fm["status"] == "resolved" and fm["outcome"] == "true" and fm["brier"] == "0.09"
    assert fm["process_grade"] == "4"
    assert "I am wrong if" in body_text          # the reasoning survives untouched
    assert _git_log(vault)[0] == "api: resolved 20260101090001"


def test_resolving_without_a_probability_still_closes_the_loop(env):
    """No number was ever stated, so there's nothing to score — but the
    decision resolves, and it simply stays off the chart."""
    root, vault, _, _ = env
    _write_decision(vault, "20260101090002", "We hire in Q3")
    with Server(root) as s:
        code, body = s.req("POST", "/api/decisions/20260101090002/resolve",
                           {"outcome": False, "process_grade": 2})
        assert code == 200
        assert body["status"] == "resolved" and body["brier"] is None
        assert body["outcome"] is False and body["process_grade"] == 2

        _, listed = s.req("GET", "/api/decisions")
        cal = listed["calibration"]
        assert cal["resolved_count"] == 1
        assert cal["scored_count"] == 0        # excluded from the curve
        assert cal["mean_brier"] is None
        assert all(b["count"] == 0 for b in cal["buckets"])
        assert cal["mean_process_grade"] == 2.0   # the grade still counts

    # an empty brier, not a zero — a zero would read as a perfect forecast
    fm, _ = notefm.parse_frontmatter(_note_text(vault, "20260101090002"))
    assert fm["brier"] == "" and fm["outcome"] == "false"


def test_resolving_twice_is_a_conflict_not_a_silent_overwrite(env):
    root, vault, _, _ = env
    _write_decision(vault, "20260101090003", "The bet", probability=30)
    with Server(root) as s:
        assert s.req("POST", "/api/decisions/20260101090003/resolve",
                     {"outcome": True, "process_grade": 3})[0] == 200
        code, body = s.req("POST", "/api/decisions/20260101090003/resolve",
                           {"outcome": False, "process_grade": 1})
        assert code == 409
        assert set(body["error"]) == {"what", "cause", "todo"}

    fm, _ = notefm.parse_frontmatter(_note_text(vault, "20260101090003"))
    assert fm["outcome"] == "true" and fm["process_grade"] == "3"   # first answer stands


def test_grade_outside_the_scale_is_rejected(env):
    root, vault, _, _ = env
    _write_decision(vault, "20260101090004", "The bet", probability=50)
    with Server(root) as s:
        for grade in (0, 6, -1):
            code, body = s.req("POST", "/api/decisions/20260101090004/resolve",
                               {"outcome": True, "process_grade": grade})
            assert code == 400, grade
            assert set(body["error"]) == {"what", "cause", "todo"}
        # …and nothing was written
        _, listed = s.req("GET", "/api/decisions")
        assert listed["items"][0]["status"] == "open"


def test_unknown_decision_is_a_404_envelope(env):
    root, vault, _, _ = env
    _write_decision(vault, "20260101090005", "The bet")
    with Server(root) as s:
        code, body = s.req("POST", "/api/decisions/99999999999999/resolve",
                           {"outcome": True, "process_grade": 3})
        assert code == 404 and set(body["error"]) == {"what", "cause", "todo"}


def test_open_decisions_sort_first_by_resolution_date(env):
    root, vault, _, _ = env
    _write_decision(vault, "20260101090006", "Later", resolves="2026-12-01")
    _write_decision(vault, "20260101090007", "Sooner", resolves="2026-06-01")
    _write_decision(vault, "20260101090008", "Closed", probability=40, status="resolved")
    with Server(root) as s:
        _, body = s.req("GET", "/api/decisions")
        claims = [d["claim"] for d in body["items"]]
        assert claims == ["Sooner", "Later", "Closed"]
        assert body["calibration"]["open_count"] == 2
