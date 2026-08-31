"""Company notes never come from the classify/LLM router — only handshake or
manual creation sets type=company — but route() still has to know where to
put one, or it silently falls back to 00-Inbox and a company note gets
treated as needing review forever."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pipeline import classify, route
from pipeline.intake import Item


def _company_item(name: str = "acme-fabrication") -> Item:
    return Item(
        path=Path(f"{name}.md"),
        kind="text",
        captured=datetime(2026, 8, 31, 14, 0, 0),
        name=name,
        tag=None,
        source="handshake",
    )


def _company_classification(title: str = "Acme Fabrication") -> classify.Classification:
    return classify.Classification(
        type="company",
        title=title,
        categories=["[[Company]]"],
        confidence=1.0,
        needs_review=False,
        routed_by="tag",
        provider="handshake",
    )


def test_company_routes_to_11_companies(tmp_path):
    paths = route.route(_company_item(), _company_classification(), "About Acme.", tmp_path)
    assert len(paths) == 1
    assert paths[0].parent.name == "11-Companies"


def test_company_status_starts_active():
    frontmatter = route.build_frontmatter(_company_item(), _company_classification())
    assert "status: active" in frontmatter
    assert "type: company" in frontmatter


def test_company_is_in_the_locked_type_list():
    assert "company" in classify.NOTE_TYPES


def test_company_is_never_reached_by_the_llm_router():
    # No capture tag routes to "company" — the 8-tag vocabulary is locked and
    # company notes only ever arrive with type already decided.
    assert "company" not in classify.TAG_TO_TYPE.values()


def test_a_company_note_a_confidence_threshold_below_review_still_parks_in_inbox(tmp_path):
    # Belt-and-suspenders: even if something someday classifies a note as
    # company via the LLM path with low confidence, needs_review still wins.
    low_confidence = classify.Classification(
        type="company", title="Maybe Acme", confidence=0.2,
        needs_review=True, routed_by="llm", provider="stub",
    )
    paths = route.route(_company_item(), low_confidence, "Unsure.", tmp_path)
    assert paths[0].parent.name == "00-Inbox"
