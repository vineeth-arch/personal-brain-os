"""Task E1: GET /api/review's split_proposals (pure events.db read, no vault
scan) and POST /api/review/split/{id}'s decision route — the review-gated
multi-topic splitter. Reuses the hermetic harness from test_api.py (ephemeral
uvicorn + urllib, the git-initialised tmp vault fixture), matching
test_resources.py's import idiom."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

# Reuse the harness + the tmp-vault fixture verbatim (env is a pytest fixture;
# importing it here lets pytest discover it in this module too).
from api.tests.test_api import Server, _seed_events, env  # noqa: F401


PARENT_ID = "20260628070000"

SEGMENTS = [
    {"title": "The client call went sideways", "start_line": 1, "end_line": 3},
    {"title": "Grocery run for the week", "start_line": 4, "end_line": 6},
    {"title": "An onboarding idea", "start_line": 7, "end_line": 9},
]

PARENT_BODY_LINES = [
    "The client call this morning went sideways fast.",
    "They pushed back on scope again.",
    "Need to follow up tomorrow with a written recap.",
    "",
    "Groceries: eggs, oat milk, spinach, coffee.",
    "Meal prep Sunday night like always.",
    "",
    "Idea: the onboarding flow should ask for a name before anything else.",
    "Small thing but it would make the first screen feel less cold.",
]


def _seed_proposal(db: Path, *, note_id: str = PARENT_ID, confidence: float = 0.82,
                   file: str = "/in/sunday.m4a") -> None:
    message = json.dumps({
        "note_id": note_id,
        "title": "sunday-morning-notes",
        "confidence": confidence,
        "segments": SEGMENTS,
    })
    _seed_events(db, [
        {"timestamp": "2026-06-28T07:00:00", "file": file, "stage": "split",
         "status": "needs_review", "message": message},
    ])


def _write_parent_note(vault: Path, note_id: str = PARENT_ID) -> Path:
    folder = vault / "02-Musings"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "2026-06-28-sunday-morning-notes.md"
    body = "\n".join(PARENT_BODY_LINES)
    path.write_text(
        f"---\nid: {note_id}\ntype: musing\ncreated: 2026-06-28\nsource: voice\n"
        f"origin: human\nmeta_origin: ai\nstatus: active\ncategories: []\n"
        f"subjects: []\ntags: []\n---\n\n{body}\n", encoding="utf-8")
    return path


# ---- GET /api/review split_proposals ----------------------------------------

def test_split_proposals_listed_from_events_db_only(env):
    root, vault, _, _ = env
    _seed_proposal(root / "events.db")
    with Server(root) as s:
        code, body = s.req("GET", "/api/review")
        assert code == 200
        assert body["split_proposals"] == [{
            "id": PARENT_ID,
            "title": "sunday-morning-notes",
            "segment_titles": [seg["title"] for seg in SEGMENTS],
            "confidence": 0.82,
        }]


def test_no_split_proposal_means_empty_list(env):
    root, _, _, _ = env
    with Server(root) as s:
        code, body = s.req("GET", "/api/review")
        assert code == 200
        assert body["split_proposals"] == []


# ---- POST /api/review/split/{id} ---------------------------------------------

def test_keep_decision_supersedes_proposal_no_vault_write(env):
    root, vault, _, _ = env
    _seed_proposal(root / "events.db")
    _write_parent_note(vault)
    with Server(root) as s:
        code, body = s.req("POST", f"/api/review/split/{PARENT_ID}", {"decision": "keep"})
        assert code == 200
        assert body == {"ok": True, "decision": "keep", "child_ids": []}

        # superseded — a second GET no longer lists it
        code, body = s.req("GET", "/api/review")
        assert body["split_proposals"] == []

        # a second decision call 404s — it's no longer pending
        code, body = s.req("POST", f"/api/review/split/{PARENT_ID}", {"decision": "keep"})
        assert code == 404


def test_split_decision_reconstructs_parent_body_exactly(env):
    root, vault, _, _ = env
    _seed_proposal(root / "events.db")
    parent_path = _write_parent_note(vault)
    original_body = "\n".join(PARENT_BODY_LINES)

    with Server(root) as s:
        code, body = s.req("POST", f"/api/review/split/{PARENT_ID}", {"decision": "split"})
        assert code == 200
        assert body["decision"] == "split"
        child_ids = body["child_ids"]
        assert len(child_ids) == len(SEGMENTS)

        # the parent is archived IN PLACE — full original text intact, never deleted
        assert parent_path.exists()
        parent_text = parent_path.read_text(encoding="utf-8")
        assert "status: archived" in parent_text
        assert "type: musing" in parent_text
        assert original_body in parent_text

        # every child note exists with the right segment's exact body lines,
        # origin: ai, and a derived-from:: edge line pointing at the parent
        folder = vault / "02-Musings"
        child_bodies = []
        for seg in SEGMENTS:
            expected_lines = PARENT_BODY_LINES[seg["start_line"] - 1:seg["end_line"]]
            expected_body = "\n".join(expected_lines).rstrip()
            matches = [
                p for p in folder.glob("*.md")
                if p != parent_path and expected_body in p.read_text(encoding="utf-8")
            ]
            assert len(matches) == 1, f"expected exactly one child for segment {seg['title']!r}"
            child_text = matches[0].read_text(encoding="utf-8")
            assert "origin: ai" in child_text
            assert f"derived-from:: [[{PARENT_ID}]]" in child_text
            child_bodies.append(expected_body)

        # THE reconstruction invariant: every child's body concatenated back
        # together, in segment order, reproduces the parent's original body
        # exactly.
        assert "\n".join(child_bodies) == original_body

        # exactly ONE new commit was made for the whole batch
        log = subprocess.run(
            ["git", "-C", str(vault), "log", "--format=%s"],
            capture_output=True, text=True).stdout.strip().splitlines()
        split_commits = [line for line in log if line.startswith("api: split ")]
        assert len(split_commits) == 1
        assert split_commits[0] == f"api: split {PARENT_ID} into {len(SEGMENTS)} notes"

        # superseded — a second GET no longer lists it, and a second decision 404s
        code, body = s.req("GET", "/api/review")
        assert body["split_proposals"] == []
        code, body = s.req("POST", f"/api/review/split/{PARENT_ID}", {"decision": "keep"})
        assert code == 404


def test_unknown_proposal_id_404s(env):
    root, _, _, _ = env
    with Server(root) as s:
        code, body = s.req("POST", "/api/review/split/nope", {"decision": "keep"})
        assert code == 404
        assert set(body["error"]) == {"what", "cause", "todo"}


def test_invalid_decision_400s(env):
    root, _, _, _ = env
    _seed_proposal(root / "events.db")
    with Server(root) as s:
        code, body = s.req("POST", f"/api/review/split/{PARENT_ID}", {"decision": "sideways"})
        assert code == 400
        assert set(body["error"]) == {"what", "cause", "todo"}
