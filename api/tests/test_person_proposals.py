"""Pass RM: GET /api/review's person_proposals and POST
/api/review/proposal/{id}. A proposal says what it becomes before the
decision; Remember writes exactly one cited, marked line and commits; Skip
writes nothing. Reuses test_api.py's hermetic harness."""
from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

from api.tests.test_api import Server, _seed_events, env  # noqa: F401

PID = "20260701090000"
NOTE_ID = "20260917101500"


def _priya(vault: Path, company: str = "") -> Path:
    folder = vault / "07-People"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "2026-07-01-priya-raman.md"
    path.write_text(
        f"---\nid: {PID}\ntype: person\ncreated: 2026-07-01\nsource: manual\norigin: human\n"
        f"company: {company}\nlast_contact: 2026-09-01\nstatus: active\n---\n\n# Priya Raman\n\n"
        "## Context\n\n\n## Next action\n\n", encoding="utf-8")
    return path


def _seed(root: Path, **proposal) -> None:
    p = {"person_id": PID, "note_id": NOTE_ID, "index": 0, "title": "coffee with priya",
         **proposal}
    _seed_events(root / "events.db", [
        {"timestamp": "2026-09-17T10:15:00", "file": "/in/capture.txt",
         "stage": "person_proposal", "status": "needs_review", "message": json.dumps(p)}])


def _commits(vault: Path) -> int:
    out = subprocess.run(["git", "-C", str(vault), "rev-list", "--count", "HEAD"],
                         capture_output=True, text=True)
    return int(out.stdout.strip() or 0) if out.returncode == 0 else 0


def test_card_says_what_it_becomes_and_remember_writes_it(env):
    root, vault, _, _ = env
    path = _priya(vault)
    event_day = (date.today() + timedelta(days=20)).isoformat()
    _seed(root, type="upcoming", text="moving to Dubai", date=event_day)
    due = (date.today() + timedelta(days=21)).isoformat()
    with Server(root) as s:
        code, body = s.req("GET", "/api/review")
        assert code == 200
        [card] = body["person_proposals"]
        assert card["person_name"] == "Priya Raman" and card["section"] == "Next action"
        assert card["due"] == due and card["line"].startswith("Ask how it went: moving to Dubai")

        code, body = s.req("POST", f"/api/review/proposal/{card['id']}", {"decision": "remember"})
        assert code == 200 and body["written"] == path.name

        text = path.read_text(encoding="utf-8")
        assert f"- {due} · Ask how it went: moving to Dubai" in text
        assert f"derived-from:: [[{NOTE_ID}]] (ai, approved) <!-- bc:{NOTE_ID}:0 -->" in text
        assert _commits(vault) == 1

        # decided → gone from the queue, and can't be applied twice
        assert s.req("GET", "/api/review")[1]["person_proposals"] == []
        assert s.req("POST", f"/api/review/proposal/{card['id']}",
                     {"decision": "remember"})[0] == 404

        code, detail = s.req("GET", f"/api/people/{PID}")
        assert "<!--" not in detail["next_action"] and "· ai" in detail["next_action"]


def test_skip_writes_nothing(env):
    root, vault, _, _ = env
    path = _priya(vault)
    before = path.read_text(encoding="utf-8")
    _seed(root, type="fact", text="Has two kids")
    with Server(root) as s:
        [card] = s.req("GET", "/api/review")[1]["person_proposals"]
        code, _ = s.req("POST", f"/api/review/proposal/{card['id']}", {"decision": "skip"})
        assert code == 200
        assert s.req("GET", "/api/review")[1]["person_proposals"] == []
    assert path.read_text(encoding="utf-8") == before and _commits(vault) == 0


def test_date_can_be_corrected_before_remembering(env):
    root, vault, _, _ = env
    path = _priya(vault)
    _seed(root, type="upcoming", text="exam", date="2026-10-01")
    with Server(root) as s:
        [card] = s.req("GET", "/api/review")[1]["person_proposals"]
        code, _ = s.req("POST", f"/api/review/proposal/{card['id']}",
                        {"decision": "remember", "date": "2026-10-05"})
        assert code == 200
    assert "- 2026-10-06 · Ask how it went: exam (5 Oct)" in path.read_text(encoding="utf-8")


def test_company_knowledge_without_a_company_note_is_a_plain_refusal(env):
    root, vault, _, _ = env
    _priya(vault, company="Emaar")
    _seed(root, type="company_knowledge", text="Hiring architects in Q4")
    with Server(root) as s:
        [card] = s.req("GET", "/api/review")[1]["person_proposals"]
        code, body = s.req("POST", f"/api/review/proposal/{card['id']}", {"decision": "remember"})
        assert code == 409
        # still pending — a refusal is not a decision
        assert len(s.req("GET", "/api/review")[1]["person_proposals"]) == 1


def test_company_knowledge_lands_on_the_company_note(env):
    root, vault, _, _ = env
    _priya(vault, company='"[[emaar]]"')
    (vault / "11-Companies").mkdir()
    company = vault / "11-Companies" / "emaar.md"
    company.write_text("---\nid: 1\ntype: company\nname: Emaar\n---\n\n## About\n\n", encoding="utf-8")
    _seed(root, type="company_knowledge", text="Hiring architects in Q4")
    with Server(root) as s:
        [card] = s.req("GET", "/api/review")[1]["person_proposals"]
        code, body = s.req("POST", f"/api/review/proposal/{card['id']}", {"decision": "remember"})
        assert code == 200 and body["written"] == "emaar.md"
    assert "## Facts" in company.read_text() and "Hiring architects in Q4" in company.read_text()
