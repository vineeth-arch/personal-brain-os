"""Provenance survives intake (SCHEMA-REFERENCE.md §2 `source`).

A note's `source` cannot be reconstructed later, so intake must never invent
one. _parse takes a source_hint and used to honour it for audio but discard it
for text — which mattered the moment a device exported a transcript beside its
recording.
"""
from __future__ import annotations

import pytest

from pipeline import intake


@pytest.mark.parametrize("name,hint,expected", [
    ("2026-08-30-0900 meeting.m4a", "plaud", "plaud"),
    ("2026-08-30-0900 meeting.txt", "plaud", "plaud"),   # was "manual"
    ("2026-08-30-0900 meeting.md", "plaud", "plaud"),    # was "manual"
    ("2026-08-30-0900 memo.m4a", None, "voice"),         # audio default
    ("2026-08-30-0900 note.txt", None, "manual"),        # text default
    ("2026-08-30-0900 memo.m4a", "share", "share"),
])
def test_source_hint_is_honoured_for_every_kind(tmp_path, name, hint, expected):
    path = tmp_path / name
    path.write_text("some words", encoding="utf-8")
    item = intake._parse(path, hint)
    assert item is not None
    assert item.source == expected


def test_a_link_capture_also_keeps_its_provenance(tmp_path):
    path = tmp_path / "2026-08-30-0900 link.txt"
    path.write_text("https://example.com/a", encoding="utf-8")
    item = intake._parse(path, "plaud")
    assert item.kind == "link"
    assert item.source == "plaud"


def test_undecodable_text_does_not_raise(tmp_path):
    path = tmp_path / "2026-08-30-0900 broken.txt"
    path.write_bytes(b"\xff\xfe binary junk")
    item = intake._parse(path, "plaud")
    assert item is not None and item.kind == "text" and item.source == "plaud"
