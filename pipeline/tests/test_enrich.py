"""Pass L tests: oEmbed parse from fixture JSON, Instagram failure path saves
the note anyway, recipe detection, 24h auto-retry. Hermetic — the HTTP `fetch`
and the model `router` are injected; no network, no real providers."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import enrich
from pipeline.events import EventLog

CAPTURED = datetime(2026, 7, 4, 10, 0)

YT_OEMBED = json.dumps({
    "title": "How to Cook Perfect Rice",
    "author_name": "Kitchen Channel",
    "thumbnail_url": "https://i.ytimg.com/vi/abc/hqdefault.jpg",
}).encode()


def config(tmp_path, **raw):
    return SimpleNamespace(vault_path=tmp_path / "vault", raw=raw)


def item(kind="link"):
    return SimpleNamespace(captured=CAPTURED, source="manual", name="link", kind=kind)


def no_router(prompt, cfg, validate):
    return None, None, []  # every provider "unavailable" → graceful fallback


def recipe_router(prompt, cfg, validate):
    data = {"resource_type": "recipe", "title": "Perfect Rice", "description": "fluffy rice",
            "is_recipe": True, "ingredients": ["1 cup rice", "2 cups water"],
            "steps": ["Rinse the rice", "Simmer 18 minutes"]}
    assert validate(data) is None
    return data, "gemini-flash", []


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "vault").mkdir()
    return tmp_path


def test_extract_url():
    assert enrich.extract_url("look at https://youtu.be/abc123 cool") == "https://youtu.be/abc123"
    assert enrich.extract_url("no url here") is None


def test_youtube_oembed_parse(vault):
    def fetch(url, data=None, timeout=10):
        if "oembed" in url:
            return YT_OEMBED
        return b""  # timedtext empty — fine
    enr = enrich.enrich_url("https://youtu.be/abc12345678", config(vault), fetch=fetch)
    assert enr.enriched and enr.platform == "youtube"
    assert enr.title == "How to Cook Perfect Rice"
    assert enr.author == "Kitchen Channel"
    assert enr.cover.endswith("hqdefault.jpg")


def test_recipe_detection_writes_sections(vault):
    def fetch(url, data=None, timeout=10):
        return YT_OEMBED if "oembed" in url else b""
    enr = enrich.enrich_url("https://youtu.be/abc12345678", config(vault), fetch=fetch)
    structured = enrich.structure("great weeknight recipe", enr, config(vault), router=recipe_router)
    path = enrich.route_link(item(), "great weeknight recipe", enr, structured, vault / "vault")
    text = path.read_text()
    assert path.parent.name == "04-Resources"
    assert "resource_type: recipe" in text and "enriched: true" in text
    assert "## Ingredients" in text and "- 1 cup rice" in text
    assert "## Steps" in text and "1. Rinse the rice" in text
    assert "## Insight" in text and "great weeknight recipe" in text  # user's words, verbatim


def test_instagram_failure_saves_note_unenriched(vault):
    def failing_fetch(url, data=None, timeout=10):
        raise ConnectionError("apify down")
    cfg = config(vault, apify={"actor_id": "some/actor"})
    import os
    os.environ["APIFY_TOKEN"] = "fake"
    try:
        enr = enrich.enrich_url("https://instagram.com/reel/XYZ/", cfg, fetch=failing_fetch)
    finally:
        del os.environ["APIFY_TOKEN"]
    assert enr.enriched is False and enr.platform == "instagram"
    assert "expected" in enr.detail.lower()
    # the note is written ANYWAY, enriched:false, with the plain-English reason
    structured = enrich.structure("saw this reel", enr, config(vault), router=no_router)
    path = enrich.route_link(item(), "saw this reel", enr, structured, vault / "vault")
    text = path.read_text()
    assert "enriched: false" in text
    assert "## Insight" in text and "saw this reel" in text
    assert "## Enrichment" in text  # the quiet reason, in the note not an alarm


def test_instagram_unconfigured_is_graceful(vault):
    enr = enrich.enrich_url("https://instagram.com/p/ABC/", config(vault), fetch=lambda *a, **k: b"")
    assert enr.enriched is False and "Apify isn't configured" in enr.detail


def test_web_title_and_og_image(vault):
    html = b'<html><head><title>A Great Article</title><meta property="og:image" content="https://x.com/i.jpg"></head></html>'
    enr = enrich.enrich_url("https://example.com/post", config(vault), fetch=lambda *a, **k: html)
    assert enr.enriched and enr.title == "A Great Article" and enr.cover == "https://x.com/i.jpg"


def test_retry_pending_one_reattempt_after_24h(vault, tmp_path):
    # seed an enriched:false note that's 25h old, attempts=1
    folder = vault / "vault" / "04-Resources"
    folder.mkdir(parents=True)
    old = (datetime.now() - timedelta(hours=25)).isoformat(timespec="seconds")
    note = folder / "2026-07-04-saw-this-reel.md"
    note.write_text(
        f"---\nid: 20260704100000\ntype: resource\nresource_type: article\ncreated: 2026-07-04\n"
        f"source: manual\norigin: human\nmeta_origin: ai\ntitle: saw this reel\ncover: \n"
        f"source_url: https://instagram.com/reel/XYZ/\ndescription: \nstatus: inbox\n"
        f"platform: instagram\nenriched: false\nenrich_attempts: 1\nenrich_last: {old}\n"
        f"categories: []\nsubjects: []\ntags: []\n---\n\n## Insight\n\nsaw this reel\n")
    events = EventLog(tmp_path / "events.db", vault / "vault")
    cfg = config(vault, apify={"actor_id": "a/b"})

    # this time Apify "works" — the fetch returns a caption payload
    def working_fetch(url, data=None, timeout=10):
        return json.dumps([{"caption": "a plating tip", "displayUrl": "https://x/i.jpg",
                            "ownerUsername": "chef"}]).encode()
    import os
    os.environ["APIFY_TOKEN"] = "fake"
    try:
        enrich.retry_pending(cfg, events, now=datetime.now(),
                             fetch=working_fetch, router=no_router)
    finally:
        del os.environ["APIFY_TOKEN"]
    text = note.read_text()
    assert "enriched: true" in text and "enrich_attempts: 2" in text
    assert "## Caption" in text and "a plating tip" in text
    # a second pass does NOT re-attempt (attempts already 2)
    enrich.retry_pending(cfg, events, now=datetime.now() + timedelta(hours=48),
                         fetch=working_fetch, router=no_router)
    assert "enrich_attempts: 2" in note.read_text()
    events.close()


def test_router_fallback_is_plain_resource(vault):
    enr = enrich.Enrichment("web", True, "https://x.com", title="Some Page")
    structured = enrich.structure("interesting", enr, config(vault), router=no_router)
    assert structured["resource_type"] == "article" and structured["title"] == "Some Page"
    assert structured["is_recipe"] is False
