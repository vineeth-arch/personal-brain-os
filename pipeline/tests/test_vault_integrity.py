"""The vault is the only source of truth (CLAUDE.md §1), so these are the
regressions that matter most: each one is a way a note used to be silently
corrupted, reset, or written-but-not-written.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from api import notes as api_notes
from pipeline import enrich, relationships, route


class _Item:
    captured = datetime(2026, 8, 30, 9, 0, 0)
    source = "voice"


class _Cls:
    type = "musing"
    title = "t"
    needs_review = False
    routed_by = "llm"
    confidence = 0.9
    categories: list = []
    subjects: list = []
    tags: list = []


def _cls(**kw):
    c = _Cls()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# ---- frontmatter can survive whatever a model returns ------------------------

@pytest.mark.parametrize("hostile", [
    "multi\nline: tag",          # a newline forged a new top-level key
    "key: value",                # a colon-space did the same
    "trailing:",
    "# looks like a comment",
    "- looks like a list item",
    '"already quoted"',
    "{braces}",
    "[brackets]",
])
def test_hostile_tag_cannot_forge_a_frontmatter_key(hostile):
    fm = route.build_frontmatter(_Item(), _cls(tags=[hostile]))
    parsed, _ = api_notes.parse_frontmatter(fm + "\n\nbody\n")
    # the block still parses to exactly the schema's keys, nothing injected
    assert set(parsed) == {"id", "type", "created", "source", "origin",
                           "meta_origin", "status", "categories", "subjects", "tags"}
    assert parsed["type"] == "musing"
    assert parsed["status"] == "active"


def test_hostile_category_cannot_escape_the_wikilink():
    fm = route.build_frontmatter(_Item(), _cls(categories=['a"] injected: x']))
    parsed, _ = api_notes.parse_frontmatter(fm + "\n\nbody\n")
    assert "injected" not in parsed


def test_ordinary_values_are_left_unquoted():
    """The escaper must not churn every existing note."""
    assert route._scalar("a normal title") == "a normal title"
    assert route._scalar("https://example.com/a/b?c=d") == "https://example.com/a/b?c=d"
    assert route._scalar("Hello, world - fine") == "Hello, world - fine"


def test_quoted_scalars_round_trip_back_to_the_plain_string():
    fm = route.build_frontmatter(_Item(), _cls(tags=["key: value"]))
    assert '"key: value"' in fm
    parsed, _ = api_notes.parse_frontmatter(fm + "\n\nbody\n")
    assert parsed["tags"] == ""          # list values live on their own lines


# ---- re-enrichment merges, never rewrites -----------------------------------

RESOURCE = """---
id: 20260704100000
type: resource
resource_type: article
created: 2026-07-04
source: manual
origin: human
meta_origin: ai
title: old
cover:
source_url: https://example.com/x
description:
status: consumed
rating: 6
platform: web
enriched: false
enrich_attempts: 1
enrich_last: 2026-07-04T10:00:00
categories: []
subjects: []
tags:
  - worth-rereading
---

## Insight

my own words

## My own notes

- a section the pipeline did not write
"""


def test_reenrich_preserves_everything_it_does_not_own(tmp_path):
    note = tmp_path / "r.md"
    note.write_text(RESOURCE, encoding="utf-8")
    config = type("C", (), {"raw": {}})()
    enrich.reenrich_note(
        note, config,
        fetch=lambda u, data=None, timeout=10: b"<html><title>New</title></html>",
        router=lambda p, c, v: ({"resource_type": "article", "title": "New",
                                 "description": "d"}, "stub", []))
    out = note.read_text(encoding="utf-8")
    fm, body = api_notes.parse_frontmatter(out)

    # user-owned, untouched
    assert fm["status"] == "consumed"
    assert fm["rating"] == "6"
    assert "worth-rereading" in out
    assert "## My own notes" in body
    assert "a section the pipeline did not write" in body
    assert "my own words" in body
    # enrichment-owned, updated
    assert fm["title"] == "New"
    assert fm["enriched"] == "true"
    assert fm["enrich_attempts"] == "2"


def test_reenrich_without_a_source_url_is_a_no_op(tmp_path):
    note = tmp_path / "r.md"
    note.write_text(RESOURCE.replace("source_url: https://example.com/x", "source_url:"),
                    encoding="utf-8")
    before = note.read_text(encoding="utf-8")
    assert enrich.reenrich_note(note, type("C", (), {"raw": {}})()) is False
    assert note.read_text(encoding="utf-8") == before


# ---- the interaction log is actually written --------------------------------

@pytest.mark.parametrize("heading", ["## Interaction log", "### Interaction log"])
def test_log_contact_writes_the_line_whatever_the_heading_level(heading):
    text = f"---\nid: 1\nlast_contact:\n---\n\n# A\n\n{heading}\n\nolder note\n"
    out = relationships._append_to_section(text, "Interaction log", "- 2026-08-30 — Reached out.")
    assert "Reached out" in out, "the line must never be silently dropped"
    assert "older note" in out


def test_append_to_section_keeps_existing_content_once():
    text = "---\nid: 1\n---\n\n## Context\n\nfirst\n"
    out = relationships._append_to_section(text, "Context", "- second")
    assert out.count("first") == 1
    assert out.count("- second") == 1
