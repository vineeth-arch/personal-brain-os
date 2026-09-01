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

# Segment 1 deliberately ENDS on a blank line (line 4) and segment 2 STARTS
# on one (line 5 is content, but segment boundaries elsewhere in this suite
# only ever land a blank line at a segment START — never an END). A
# .rstrip() on a segment's joined lines silently drops a trailing blank line,
# so shaping the fixture this way is what actually exercises that bug.
SEGMENTS = [
    {"title": "The client call went sideways", "start_line": 1, "end_line": 4},
    {"title": "Grocery run for the week", "start_line": 5, "end_line": 7},
    {"title": "An onboarding idea", "start_line": 8, "end_line": 9},
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
                   file: str = "/in/sunday.m4a", segments: list[dict] = SEGMENTS) -> None:
    message = json.dumps({
        "note_id": note_id,
        "title": "sunday-morning-notes",
        "confidence": confidence,
        "segments": segments,
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


def _child_body_from_file(path: Path, note_id: str) -> str:
    """Recover exactly the seg_body execute_split wrote into a child file,
    reading the REAL file off disk rather than trusting any test fixture —
    this is what makes the reconstruction-invariant test below actually
    prove something. Splits on the same structural markers execute_split's
    own template uses (the frontmatter fence, then its own "\\n\\n"
    separators around the derived-from:: line), not on any assumption about
    the content in between."""
    text = path.read_text(encoding="utf-8")
    _, _, after = text.partition("\n---\n")
    after = after[1:] if after.startswith("\n") else after
    marker = f"\n\n- derived-from:: [[{note_id}]]"
    body_part, sep, _ = after.rpartition(marker)
    assert sep, f"child file {path.name} is missing its derived-from:: edge line"
    return body_part


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

        # child_ids come back in segment order (execute_split appends them
        # in the same loop that writes each file), so each child can be
        # found by its OWN immutable id rather than by guessing at content.
        folder = vault / "02-Musings"
        child_paths: list[Path] = []
        for child_id in child_ids:
            matches = [p for p in folder.glob("*.md")
                      if p != parent_path and f"id: {child_id}" in p.read_text(encoding="utf-8")]
            assert len(matches) == 1, f"expected exactly one child with id {child_id}"
            child_text = matches[0].read_text(encoding="utf-8")
            assert "origin: ai" in child_text
            assert f"derived-from:: [[{PARENT_ID}]]" in child_text
            child_paths.append(matches[0])

        # THE reconstruction invariant: every child's ACTUAL body, read back
        # off disk (not the test's own idea of what it should be) and
        # concatenated in segment order, reproduces the parent's original
        # body exactly — including the blank line at segment 1's end, which
        # a wrongly-reintroduced .rstrip() would drop.
        child_bodies = [_child_body_from_file(p, PARENT_ID) for p in child_paths]
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


def test_split_rejected_when_segments_dont_cover_the_tail(env):
    """validate_split can't see the body (it never checks the LAST segment's
    end_line reaches the end), so execute_split must guard it itself —
    otherwise the note's tail silently never appears in any child. A
    corrupted/short proposal is rejected with 409 BEFORE anything is
    written: no child files, the parent's status untouched, no commit."""
    root, vault, _, _ = env
    short_segments = [
        {"title": "The client call went sideways", "start_line": 1, "end_line": 4},
        {"title": "Grocery run for the week", "start_line": 5, "end_line": 7},
        # stops at line 7 — lines 8-9 (real prose) would vanish
    ]
    _seed_proposal(root / "events.db", segments=short_segments)
    parent_path = _write_parent_note(vault)
    original_text = parent_path.read_text(encoding="utf-8")

    with Server(root) as s:
        code, body = s.req("POST", f"/api/review/split/{PARENT_ID}", {"decision": "split"})
        assert code == 409
        assert set(body["error"]) == {"what", "cause", "todo"}

        # nothing written: parent byte-for-byte unchanged, still active
        assert parent_path.read_text(encoding="utf-8") == original_text
        assert "status: active" in original_text
        folder = vault / "02-Musings"
        assert [p.name for p in folder.glob("*.md")] == [parent_path.name]

        # no commit was made
        log = subprocess.run(
            ["git", "-C", str(vault), "log", "--format=%s"],
            capture_output=True, text=True).stdout.strip().splitlines()
        assert not any(line.startswith("api: split ") for line in log)

        # the proposal is still pending — a rejected split does NOT supersede it
        code, body = s.req("GET", "/api/review")
        assert len(body["split_proposals"]) == 1


def test_split_rejected_when_segments_empty(env):
    """A corrupted events row with zero segments must not archive the parent
    and create nothing while still reporting success."""
    root, vault, _, _ = env
    _seed_proposal(root / "events.db", segments=[])
    parent_path = _write_parent_note(vault)
    original_text = parent_path.read_text(encoding="utf-8")

    with Server(root) as s:
        code, body = s.req("POST", f"/api/review/split/{PARENT_ID}", {"decision": "split"})
        assert code == 409
        assert parent_path.read_text(encoding="utf-8") == original_text
        folder = vault / "02-Musings"
        assert [p.name for p in folder.glob("*.md")] == [parent_path.name]


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


# ---- watcher → notes.py JSON contract ----------------------------------------

def test_watcher_split_event_keys_match_notes_parser(tmp_path):
    """Nothing else in this suite exercises the REAL pipeline/watcher.py
    Stage 4c write path — every test above hand-seeds JSON with keys that
    happen to already match list_split_proposals'/find_pending_split's
    parsing, so a key rename on either side would pass silently. This runs
    the actual watcher.process_file (via run_once) against a real
    events.db, then reads that SAME db back through the real
    api.notes functions — no hand-rolled JSON anywhere in this test."""
    from pipeline import watcher
    from pipeline.config import Config
    from pipeline.events import EventLog
    from pipeline.transcribe import Transcriber
    from api import notes as notes_mod

    vault = tmp_path / "vault"
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    failed = tmp_path / "failed"
    for d in (vault, inbox, archive, failed):
        d.mkdir()

    # >200 words, no URL, no capture tag — a plain journal/musing capture
    body_text = " ".join(["word"] * 220)
    (inbox / "2026-06-28-0700 sunday-morning-notes.txt").write_text(
        body_text, encoding="utf-8")

    config = Config(vault_path=vault, inbox_path=inbox, archive_path=archive, failed_path=failed)
    db_path = tmp_path / "events.db"
    events = EventLog(db_path, config.vault_path)

    def stub_classifier(_transcript, _config):
        return {"type": "musing", "categories": [], "subjects": [], "tags": [],
                "confidence": 0.9, "title": "sunday-morning-notes"}

    watcher_segments = [
        {"title": "Topic one", "start_line": 1, "end_line": 1},
        {"title": "Topic two", "start_line": 2, "end_line": 2},
    ]

    def stub_split_llm(_body, _config):
        return {"multi_topic": True, "confidence": 0.85, "segments": watcher_segments}

    class DummyTranscriber(Transcriber):
        def transcribe(self, audio_path):  # pragma: no cover — no audio in this test
            raise AssertionError("no audio in this test")

    deps = watcher.Deps(transcriber=DummyTranscriber(), classifier_fn=stub_classifier,
                        split_llm=stub_split_llm)
    try:
        results = watcher.run_once(config, events, deps)
    finally:
        events.close()

    assert len(results) == 1
    assert results[0].status != "failed", results[0].error

    proposals = notes_mod.list_split_proposals(db_path)
    assert len(proposals) == 1
    assert proposals[0]["title"] == "sunday-morning-notes"
    assert proposals[0]["confidence"] == 0.85
    assert proposals[0]["segment_titles"] == ["Topic one", "Topic two"]

    file_key, parsed_segments = notes_mod.find_pending_split(db_path, proposals[0]["id"])
    assert file_key is not None
    assert parsed_segments == watcher_segments
