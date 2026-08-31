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
    def fetch(url, data=None, timeout=10, headers=None):
        if "oembed" in url:
            return YT_OEMBED
        return b""  # timedtext empty — fine
    enr = enrich.enrich_url("https://youtu.be/abc12345678", config(vault), fetch=fetch)
    assert enr.enriched and enr.platform == "youtube"
    assert enr.title == "How to Cook Perfect Rice"
    assert enr.author == "Kitchen Channel"
    assert enr.cover.endswith("hqdefault.jpg")


def test_youtube_transcript_via_innertube_json3(vault):
    def fetch(url, data=None, timeout=10, headers=None):
        if "oembed" in url:
            return YT_OEMBED
        if url == enrich._INNERTUBE_URL:
            payload = json.loads(data)
            assert payload["videoId"] == "abc12345678"
            return json.dumps({
                "captions": {"playerCaptionsTracklistRenderer": {"captionTracks": [
                    {"languageCode": "en", "baseUrl": "https://yt.example/caption"},
                ]}},
            }).encode()
        if url == "https://yt.example/caption&fmt=json3":
            return json.dumps({"events": [
                {"segs": [{"utf8": "Hello "}, {"utf8": "world."}]},
                {"segs": [{"utf8": " Rice is done."}]},
            ]}).encode()
        raise AssertionError(f"unexpected fetch: {url}")
    enr = enrich.enrich_url("https://youtu.be/abc12345678", config(vault), fetch=fetch)
    assert enr.transcript == "Hello world. Rice is done."


def test_youtube_transcript_prefers_english_or_hindi_track(vault):
    def fetch(url, data=None, timeout=10, headers=None):
        if "oembed" in url:
            return YT_OEMBED
        if url == enrich._INNERTUBE_URL:
            return json.dumps({
                "captions": {"playerCaptionsTracklistRenderer": {"captionTracks": [
                    {"languageCode": "fr", "baseUrl": "https://yt.example/fr"},
                    {"languageCode": "hi", "baseUrl": "https://yt.example/hi"},
                ]}},
            }).encode()
        if url == "https://yt.example/hi&fmt=json3":
            return json.dumps({"events": [{"segs": [{"utf8": "नमस्ते"}]}]}).encode()
        raise AssertionError(f"unexpected fetch: {url}")
    enr = enrich.enrich_url("https://youtu.be/abc12345678", config(vault), fetch=fetch)
    assert enr.transcript == "नमस्ते"


def test_youtube_transcript_falls_back_to_xml_shape(vault):
    def fetch(url, data=None, timeout=10, headers=None):
        if "oembed" in url:
            return YT_OEMBED
        if url == enrich._INNERTUBE_URL:
            return json.dumps({
                "captions": {"playerCaptionsTracklistRenderer": {"captionTracks": [
                    {"languageCode": "en", "baseUrl": "https://yt.example/caption"},
                ]}},
            }).encode()
        if url == "https://yt.example/caption&fmt=json3":
            return b'<transcript><text start="0">Hello world</text></transcript>'
        raise AssertionError(f"unexpected fetch: {url}")
    enr = enrich.enrich_url("https://youtu.be/abc12345678", config(vault), fetch=fetch)
    assert enr.transcript == "Hello world"


def test_youtube_with_no_captions_at_all_has_empty_transcript_not_a_failure(vault):
    def fetch(url, data=None, timeout=10, headers=None):
        if "oembed" in url:
            return YT_OEMBED
        if url == enrich._INNERTUBE_URL:
            return json.dumps({"captions": {}}).encode()
        raise AssertionError(f"unexpected fetch: {url}")
    enr = enrich.enrich_url("https://youtu.be/abc12345678", config(vault), fetch=fetch)
    assert enr.enriched is True     # a missing transcript never fails the note
    assert enr.transcript == ""


def test_youtube_transcript_fetch_erroring_is_swallowed(vault):
    def fetch(url, data=None, timeout=10, headers=None):
        if "oembed" in url:
            return YT_OEMBED
        raise ConnectionError("network down")
    enr = enrich.enrich_url("https://youtu.be/abc12345678", config(vault), fetch=fetch)
    assert enr.enriched is True
    assert enr.transcript == ""


def test_recipe_detection_writes_sections(vault):
    def fetch(url, data=None, timeout=10, headers=None):
        return YT_OEMBED if "oembed" in url else b""
    enr = enrich.enrich_url("https://youtu.be/abc12345678", config(vault), fetch=fetch)
    structured = enrich.structure("great weeknight recipe", enr, config(vault), router=recipe_router)
    path = enrich.route_link(item(), "great weeknight recipe", enr, structured, vault / "vault")
    text = path.read_text(encoding="utf-8")
    assert path.parent.name == "04-Resources"
    assert "resource_type: recipe" in text and "enriched: true" in text
    assert "## Ingredients" in text and "- 1 cup rice" in text
    assert "## Steps" in text and "1. Rinse the rice" in text
    assert "## Insight" in text and "great weeknight recipe" in text  # user's words, verbatim


def test_strip_url_removes_the_raw_match_trailing_punctuation_and_all():
    text = "great grid tutorial https://youtu.be/x try for studio"
    assert enrich.strip_url(text, "https://youtu.be/x") == "great grid tutorial try for studio"
    # extract_url trims trailing punctuation off the stored url — the raw
    # match in the text still carries it, and that must go too
    assert enrich.strip_url("see https://x.com/a, thanks", "https://x.com/a") == "see thanks"


def test_strip_url_with_no_url_just_strips_whitespace():
    assert enrich.strip_url("  just a thought  ", None) == "just a thought"


def test_build_resource_note_insight_has_no_url_glued_in(vault):
    def fetch(url, data=None, timeout=10, headers=None):
        return YT_OEMBED if "oembed" in url else b""
    enr = enrich.enrich_url("https://youtu.be/abc12345678", config(vault), fetch=fetch)
    text = "great grid tutorial https://youtu.be/abc12345678 try for studio"
    structured = enrich.structure(text, enr, config(vault), router=no_router)
    path = enrich.route_link(item(), text, enr, structured, vault / "vault")
    note = path.read_text(encoding="utf-8")
    assert "## Insight" in note
    assert "great grid tutorial try for studio" in note
    assert "youtu.be/abc12345678" not in note.split("## Insight")[1].split("##")[0]
    # the URL isn't lost — it's in source_url, just not duplicated in the insight
    assert "source_url:" in note and "youtu.be/abc12345678" in note


def test_a_capture_with_only_a_url_and_no_other_words_has_no_insight_section(vault):
    def fetch(url, data=None, timeout=10, headers=None):
        return YT_OEMBED if "oembed" in url else b""
    enr = enrich.enrich_url("https://youtu.be/abc12345678", config(vault), fetch=fetch)
    structured = enrich.structure("https://youtu.be/abc12345678", enr, config(vault), router=no_router)
    path = enrich.route_link(item(), "https://youtu.be/abc12345678", enr, structured, vault / "vault")
    assert "## Insight" not in path.read_text(encoding="utf-8")


def test_instagram_failure_saves_note_unenriched(vault):
    def failing_fetch(url, data=None, timeout=10, headers=None):
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
    text = path.read_text(encoding="utf-8")
    assert "enriched: false" in text
    assert "## Insight" in text and "saw this reel" in text
    assert "## Enrichment" in text  # the quiet reason, in the note not an alarm


def _apify_env(monkeypatch):
    import os
    monkeypatch.setenv("APIFY_TOKEN", "fake")


def test_instagram_carousel_childposts_shape(vault, monkeypatch):
    _apify_env(monkeypatch)
    cfg = config(vault, apify={"actor_id": "apify~instagram-scraper"})

    def fetch(url, data=None, timeout=10, headers=None):
        return json.dumps([{
            "caption": "day at the studio",
            "displayUrl": "https://ig.example/cover.jpg",
            "ownerUsername": "artist",
            "childPosts": [
                {"displayUrl": "https://ig.example/1.jpg", "caption": "first piece"},
                {"displayUrl": "https://ig.example/2.jpg"},
            ],
        }]).encode()

    enr = enrich.enrich_url("https://instagram.com/p/ABC/", cfg, fetch=fetch)
    assert enr.enriched and len(enr.slides) == 2
    assert enr.slides[0] == {"image_url": "https://ig.example/1.jpg", "caption": "first piece"}
    assert enr.slides[1] == {"image_url": "https://ig.example/2.jpg", "caption": ""}
    structured = enrich.structure("saw this", enr, cfg, router=no_router)
    path = enrich.route_link(item(), "saw this", enr, structured, vault / "vault")
    text = path.read_text(encoding="utf-8")
    assert "## Slides" in text
    assert "1. https://ig.example/1.jpg — first piece" in text
    assert "2. https://ig.example/2.jpg" in text


def test_instagram_carousel_images_shape(vault, monkeypatch):
    _apify_env(monkeypatch)
    cfg = config(vault, apify={"actor_id": "apify~instagram-scraper"})

    def fetch(url, data=None, timeout=10, headers=None):
        return json.dumps([{
            "caption": "trip photos",
            "displayUrl": "https://ig.example/cover.jpg",
            "images": ["https://ig.example/a.jpg", "https://ig.example/b.jpg"],
        }]).encode()

    enr = enrich.enrich_url("https://instagram.com/p/XYZ/", cfg, fetch=fetch)
    assert enr.enriched and len(enr.slides) == 2
    assert enr.slides[0]["image_url"] == "https://ig.example/a.jpg"


def test_instagram_single_post_has_no_slides(vault, monkeypatch):
    _apify_env(monkeypatch)
    cfg = config(vault, apify={"actor_id": "apify~instagram-scraper"})

    def fetch(url, data=None, timeout=10, headers=None):
        return json.dumps([{"caption": "one photo", "displayUrl": "https://ig.example/x.jpg"}]).encode()

    enr = enrich.enrich_url("https://instagram.com/p/ONE/", cfg, fetch=fetch)
    assert enr.slides == []
    structured = enrich.structure("nice", enr, cfg, router=no_router)
    path = enrich.route_link(item(), "nice", enr, structured, vault / "vault")
    assert "## Slides" not in path.read_text(encoding="utf-8")


def test_instagram_carousel_slides_capped_at_20(vault, monkeypatch):
    _apify_env(monkeypatch)
    cfg = config(vault, apify={"actor_id": "apify~instagram-scraper"})

    def fetch(url, data=None, timeout=10, headers=None):
        return json.dumps([{
            "caption": "big carousel",
            "childPosts": [{"displayUrl": f"https://ig.example/{i}.jpg"} for i in range(30)],
        }]).encode()

    enr = enrich.enrich_url("https://instagram.com/p/BIG/", cfg, fetch=fetch)
    assert len(enr.slides) == 20


def test_retry_pending_retries_instagram_beyond_attempt_2_once_apify_configured(vault, monkeypatch):
    folder = vault / "vault" / "04-Resources"
    folder.mkdir(parents=True)
    note = folder / "2026-07-01-a-reel.md"
    note.write_text(
        "---\nid: 20260701090000\ntype: resource\nresource_type: article\n"
        "source_url: https://instagram.com/p/ABC/\nplatform: instagram\n"
        "enriched: false\nenrich_attempts: 2\n"
        f"enrich_last: {(datetime(2026, 6, 28)).isoformat(timespec='seconds')}\n---\n\n"
        "## Insight\n\nsaw this\n", encoding="utf-8")

    def no_apify_fetch(url, data=None, timeout=10, headers=None):
        raise AssertionError("should not be called before Apify is configured")

    events = EventLog(vault / "events.db", vault / "vault")
    # not configured yet — the note must be left alone (past its 1 guaranteed retry)
    enrich.retry_pending(config(vault), events, now=datetime(2026, 7, 3), fetch=no_apify_fetch)
    fm_after, _ = enrich._parse_note(note.read_text(encoding="utf-8"))
    assert fm_after["enrich_attempts"] == "2"

    _apify_env(monkeypatch)
    cfg = config(vault, apify={"actor_id": "apify~instagram-scraper"})

    def fetch(url, data=None, timeout=10, headers=None):
        return json.dumps([{"caption": "now enriched", "displayUrl": "https://ig.example/x.jpg"}]).encode()

    enrich.retry_pending(cfg, events, now=datetime(2026, 7, 3), fetch=fetch)
    fm_after, _ = enrich._parse_note(note.read_text(encoding="utf-8"))
    assert fm_after["enrich_attempts"] == "3"
    assert fm_after["enriched"] == "true"


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
        f"categories: []\nsubjects: []\ntags: []\n---\n\n## Insight\n\nsaw this reel\n", encoding="utf-8")
    events = EventLog(tmp_path / "events.db", vault / "vault")
    cfg = config(vault, apify={"actor_id": "a/b"})

    # this time Apify "works" — the fetch returns a caption payload
    def working_fetch(url, data=None, timeout=10, headers=None):
        return json.dumps([{"caption": "a plating tip", "displayUrl": "https://x/i.jpg",
                            "ownerUsername": "chef"}]).encode()
    import os
    os.environ["APIFY_TOKEN"] = "fake"
    try:
        enrich.retry_pending(cfg, events, now=datetime.now(),
                             fetch=working_fetch, router=no_router)
    finally:
        del os.environ["APIFY_TOKEN"]
    text = note.read_text(encoding="utf-8")
    assert "enriched: true" in text and "enrich_attempts: 2" in text
    assert "## Caption" in text and "a plating tip" in text
    # a second pass does NOT re-attempt (attempts already 2)
    enrich.retry_pending(cfg, events, now=datetime.now() + timedelta(hours=48),
                         fetch=working_fetch, router=no_router)
    assert "enrich_attempts: 2" in note.read_text(encoding="utf-8")
    events.close()


def test_router_fallback_is_plain_resource(vault):
    enr = enrich.Enrichment("web", True, "https://x.com", title="Some Page")
    structured = enrich.structure("interesting", enr, config(vault), router=no_router)
    assert structured["resource_type"] == "article" and structured["title"] == "Some Page"
    assert structured["is_recipe"] is False
