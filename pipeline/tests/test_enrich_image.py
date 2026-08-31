"""Pass V3: image note building/routing/reenrich in pipeline/enrich.py.
Hermetic — vision is stubbed via the `caller` seam, no network."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import classify, enrich

CAPTURED = datetime(2026, 7, 4, 10, 0)


def config(tmp_path, key="fake-key"):
    return SimpleNamespace(vault_path=tmp_path / "vault", anthropic_key=key, raw={})


def item(name="a photo", tag=None, source="manual"):
    return SimpleNamespace(captured=CAPTURED, source=source, name=name, tag=tag,
                           kind="image", path=Path("/tmp/does-not-matter.jpg"))


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "vault").mkdir()
    return tmp_path


# ---- take_image_insight ------------------------------------------------------

def test_take_image_insight_reads_and_deletes_sidecar(tmp_path):
    img = tmp_path / "2026-07-04-1000 a-photo.jpg"
    img.write_bytes(b"x")
    sidecar = tmp_path / ".2026-07-04-1000 a-photo.insight"
    sidecar.write_text("  a quick thought  \n", encoding="utf-8")
    assert enrich.take_image_insight(img) == "a quick thought"
    assert not sidecar.exists()


def test_take_image_insight_is_blank_when_no_sidecar(tmp_path):
    img = tmp_path / "2026-07-04-1000 a-photo.jpg"
    img.write_bytes(b"x")
    assert enrich.take_image_insight(img) == ""


# ---- move_image_to_vault ------------------------------------------------------

def test_move_image_to_vault_lands_in_attachments(vault):
    src = vault / "inbox"
    src.mkdir()
    photo = src / "2026-07-04-1000 sunset.jpg"
    photo.write_bytes(b"jpeg-bytes")
    it = item(name="sunset")
    it.path = photo
    dest = enrich.move_image_to_vault(it, vault / "vault")
    assert dest.parent.name == "attachments"
    assert dest.name == "20260704100000-sunset.jpg"
    assert dest.exists() and not photo.exists()


def test_move_image_to_vault_avoids_collision(vault):
    attachments = vault / "vault" / "attachments"
    attachments.mkdir(parents=True)
    (attachments / "20260704100000-sunset.jpg").write_bytes(b"already here")
    src = vault / "inbox"
    src.mkdir()
    photo = src / "2026-07-04-1000 sunset.jpg"
    photo.write_bytes(b"new bytes")
    it = item(name="sunset")
    it.path = photo
    dest = enrich.move_image_to_vault(it, vault / "vault")
    assert dest.name == "20260704100000-sunset-2.jpg"


# ---- build_image_note / route_image (untagged → resource) --------------------

def test_untagged_image_becomes_a_described_resource_note(vault):
    it = item()
    vis = {"description": "A whiteboard with a roadmap sketch.",
          "resource_type": "tool", "extracted_text": "Q3 launch"}
    path = enrich.route_image(it, vis, "worth revisiting", "attachments/x.jpg", vault / "vault")
    assert path.parent.name == "04-Resources"
    text = path.read_text(encoding="utf-8")
    assert "type: resource" in text
    assert "resource_type: tool" in text
    assert "platform: photo" in text
    assert "cover: attachments/x.jpg" in text
    assert "enriched: true" in text
    assert "origin: human" in text and "meta_origin: ai" in text
    assert "## Insight" in text and "worth revisiting" in text
    assert "![[attachments/x.jpg]]" in text
    assert "## Extracted text" in text and "Q3 launch" in text


def test_untagged_image_with_no_vision_is_still_written_honestly(vault):
    it = item()
    path = enrich.route_image(it, None, "", "attachments/x.jpg", vault / "vault")
    text = path.read_text(encoding="utf-8")
    assert "enriched: false" in text
    assert "resource_type: article" in text   # safe fallback
    assert "## Enrichment" in text
    assert "couldn't be described" in text
    assert "![[attachments/x.jpg]]" in text    # the photo is still there


def test_untagged_image_title_prefers_insight_then_description_then_photo(vault):
    it = item()
    with_insight = enrich.route_image(it, {"description": "d", "resource_type": "article",
                                           "extracted_text": ""},
                                      "my own words", "attachments/a.jpg", vault / "vault")
    assert "title: my own words" in with_insight.read_text(encoding="utf-8")

    it2 = item(name="b")
    with_desc = enrich.route_image(it2, {"description": "a sunset photo", "resource_type": "article",
                                         "extracted_text": ""}, "", "attachments/b.jpg", vault / "vault")
    assert "title: a sunset photo" in with_desc.read_text(encoding="utf-8")

    it3 = item(name="c")
    bare = enrich.route_image(it3, None, "", "attachments/c.jpg", vault / "vault")
    assert "title: photo" in bare.read_text(encoding="utf-8")


# ---- build_tagged_image_note / route_tagged_image (tag → that type) ----------

def test_tagged_image_routes_to_the_tag_type_folder(vault):
    it = item(name="idea sketch", tag="idea")
    cls = classify.Classification(type="musing", title="idea sketch", tags=["idea"],
                                  confidence=1.0, needs_review=False, routed_by="tag")
    path = enrich.route_tagged_image(it, cls, {"description": "a napkin sketch",
                                               "resource_type": "article", "extracted_text": ""},
                                     "a quick idea", "attachments/x.jpg", vault / "vault")
    assert path.parent.name == "02-Musings"
    text = path.read_text(encoding="utf-8")
    assert "type: musing" in text
    assert "source: share" in text
    assert "origin: human" in text
    assert "a quick idea" in text
    assert "![[attachments/x.jpg]]" in text
    assert "## AI description" in text and "a napkin sketch" in text


def test_tagged_image_note_has_no_ai_description_section_when_vision_failed(vault):
    it = item(tag="learning")
    cls = classify.Classification(type="learning", title="photo", tags=["learning"],
                                  confidence=1.0, needs_review=False, routed_by="tag")
    path = enrich.route_tagged_image(it, cls, None, "", "attachments/x.jpg", vault / "vault")
    text = path.read_text(encoding="utf-8")
    assert path.parent.name == "03-Learnings"
    assert "## AI description" not in text
    assert "![[attachments/x.jpg]]" in text  # the photo is still there


# ---- reenrich_image_note -------------------------------------------------------

def _seed_photo_note(vault: Path, attempts: int = 1, enriched: str = "false") -> Path:
    folder = vault / "vault" / "04-Resources"
    folder.mkdir(parents=True, exist_ok=True)
    (vault / "vault" / "attachments").mkdir(parents=True, exist_ok=True)
    (vault / "vault" / "attachments" / "x.jpg").write_bytes(b"jpeg")
    note = folder / "2026-07-04-photo.md"
    note.write_text(
        "---\nid: 20260704100000\ntype: resource\nresource_type: article\ncreated: 2026-07-04\n"
        "source: manual\norigin: human\nmeta_origin: ai\ntitle: photo\ncover: attachments/x.jpg\n"
        "source_url: \ndescription: \nstatus: inbox\nplatform: photo\n"
        f"enriched: {enriched}\nenrich_attempts: {attempts}\nenrich_last: 2026-07-04T09:00:00\n"
        "categories: []\nsubjects: []\ntags: []\n---\n\n## Insight\n\nworth revisiting\n",
        encoding="utf-8")
    return note


def test_reenrich_image_note_merges_a_late_description(vault):
    note = _seed_photo_note(vault)

    def caller(path, mime, key):
        import json
        return json.dumps({"description": "a bookshelf", "resource_type": "book",
                           "extracted_text": "ISBN 12345"})

    enriched = enrich.reenrich_image_note(note, config(vault), caller=caller)
    assert enriched is True
    text = note.read_text(encoding="utf-8")
    assert "description: a bookshelf" in text
    assert "resource_type: book" in text
    assert "enriched: true" in text and "enrich_attempts: 2" in text
    assert "## Extracted text" in text and "ISBN 12345" in text
    assert "worth revisiting" in text  # the user's insight is untouched


def test_reenrich_image_note_keeps_note_honest_on_repeated_failure(vault):
    note = _seed_photo_note(vault)
    enriched = enrich.reenrich_image_note(note, config(vault), caller=lambda *a: "not json")
    assert enriched is False
    text = note.read_text(encoding="utf-8")
    assert "enriched: false" in text and "enrich_attempts: 2" in text
    assert "worth revisiting" in text


def test_reenrich_image_note_returns_false_without_a_cover(vault):
    folder = vault / "vault" / "04-Resources"
    folder.mkdir(parents=True)
    note = folder / "no-cover.md"
    note.write_text(
        "---\nid: 20260704100000\ntype: resource\nplatform: photo\ncover: \n"
        "enrich_attempts: 1\nenrich_last: 2026-07-04T09:00:00\n---\n\nbody\n", encoding="utf-8")
    assert enrich.reenrich_image_note(note, config(vault), caller=lambda *a: "{}") is False
