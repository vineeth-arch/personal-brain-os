"""The conversation type is wired through every place that must agree.

SCHEMA-REFERENCE.md's own header requires its type->folder table and
route.TYPE_FOLDER to change together; these tests make that mechanical rather
than a promise. Mirrors test_route_company.py.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from pipeline import classify, route

REPO = Path(__file__).resolve().parents[2]


class _Item:
    captured = datetime(2026, 8, 30, 14, 0, 0)
    source = "plaud"


class _Cls:
    type = "conversation"
    title = "product sync"
    needs_review = False
    routed_by = "tag"
    categories: list = []
    subjects: list = []
    tags: list = []
    speakers: list = []
    confidence = 1.0


def test_conversation_is_a_known_type_with_a_folder_and_a_lifecycle():
    assert "conversation" in classify.NOTE_TYPES
    assert route.TYPE_FOLDER["conversation"] == "12-Conversations"
    assert route.STATUS_INITIAL["conversation"] == "active"


def test_every_type_has_a_folder_and_an_initial_status():
    """The three lists cannot drift apart silently."""
    for t in classify.NOTE_TYPES:
        assert t in route.TYPE_FOLDER, f"{t} has no folder"
        assert t in route.STATUS_INITIAL, f"{t} has no initial status"


def test_schema_reference_and_route_agree():
    """SCHEMA-REFERENCE.md §9 is the law; route.py must match it exactly."""
    schema = (REPO / "SCHEMA-REFERENCE.md").read_text(encoding="utf-8")
    rows = dict(re.findall(r"^\| (\w+) \| `([^`]+)` \|$", schema, re.MULTILINE))
    for note_type, folder in route.TYPE_FOLDER.items():
        assert note_type in rows, f"{note_type} is in route.py but not the schema table"
        assert rows[note_type].rstrip("/") == folder.rstrip("/"), \
            f"{note_type}: schema says {rows[note_type]}, route.py says {folder}"


def test_typescript_union_matches_python():
    ts = (REPO / "web" / "src" / "api" / "types.ts").read_text(encoding="utf-8")
    block = ts.split("export const NOTE_TYPES = [", 1)[1].split("] as const;", 1)[0]
    assert set(re.findall(r'"(\w+)"', block)) == set(classify.NOTE_TYPES)


def test_conversation_routes_to_its_own_folder(tmp_path):
    paths = route.route(_Item(), _Cls(), "[00:01] Ana: hello\n[00:04] Ben: hi", tmp_path)
    assert paths[0].parent.name == "12-Conversations"
    text = paths[0].read_text(encoding="utf-8")
    assert "type: conversation" in text
    assert "status: active" in text
    assert "[00:01] Ana: hello" in text          # transcript kept verbatim (§8)


def test_conversation_has_no_capture_tag():
    """§4 caps capture tags at eight; a conversation is recognised by speakers."""
    assert "conversation" not in classify.TAG_TO_TYPE
    assert len(classify.TAG_TO_TYPE) == 8
