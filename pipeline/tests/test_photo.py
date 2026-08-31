"""Pass 13 — pipeline/photo.py orchestration: sidecar reading, HEIC fallback
conversion, the classify guard (no tag + no text -> needs-review without an
LLM call on nothing), attachment copying, and both note-writing paths
(resource vs generic). Hermetic — router/llm_fn are injected, ffmpeg calls
are monkeypatched, no network."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import photo

CAPTURED = datetime(2026, 8, 13, 14, 30)


def item(tag=None, name="photo", source="photo"):
    return SimpleNamespace(captured=CAPTURED, source=source, name=name, tag=tag,
                           path=Path("/inbox/2026-08-13-1430 photo.jpg"))


def config(**raw):
    return SimpleNamespace(raw=raw, confidence_threshold=0.7)


# ---- sidecar reading ----------------------------------------------------------

def test_read_sidecar_missing_defaults_empty(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    assert photo.read_sidecar(img) == {"text": "", "ocr": "", "source": "photo"}


def test_read_sidecar_valid(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    (tmp_path / "photo.meta.json").write_text(json.dumps(
        {"text": "my thought", "ocr": "some ocr", "source": "photo"}))
    assert photo.read_sidecar(img) == {"text": "my thought", "ocr": "some ocr", "source": "photo"}


def test_read_sidecar_malformed_json_degrades_gracefully(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    (tmp_path / "photo.meta.json").write_text("{not valid json!!!")
    assert photo.read_sidecar(img) == {"text": "", "ocr": "", "source": "photo"}


def test_read_sidecar_non_dict_json_degrades_gracefully(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    (tmp_path / "photo.meta.json").write_text(json.dumps(["not", "a", "dict"]))
    assert photo.read_sidecar(img) == {"text": "", "ocr": "", "source": "photo"}


def test_read_sidecar_oversized_fields_are_capped_defensively(tmp_path):
    """A sidecar could in principle arrive via Syncthing without going
    through the capture endpoint's own cap — read_sidecar caps again."""
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    (tmp_path / "photo.meta.json").write_text(json.dumps(
        {"text": "x" * 50_000, "ocr": "y" * 50_000, "source": "photo"}))
    data = photo.read_sidecar(img)
    assert len(data["text"]) == 20_000
    assert len(data["ocr"]) == 20_000


def test_read_sidecar_coerces_non_string_fields():
    pass  # covered implicitly by str() coercion; explicit case below


def test_read_sidecar_non_string_field_types_are_coerced(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    (tmp_path / "photo.meta.json").write_text(json.dumps({"text": 12345, "ocr": None}))
    data = photo.read_sidecar(img)
    assert data["text"] == "12345"
    assert data["ocr"] == "None"


# ---- HEIC fallback conversion -------------------------------------------------

def test_non_heic_reads_bytes_directly(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff-fake-jpeg")
    it = SimpleNamespace(path=img)
    data, mime = photo.load_image_for_processing(it)
    assert data == b"\xff\xd8\xff-fake-jpeg"
    assert mime == "image/jpeg"


def test_heic_without_ffmpeg_quarantines_with_plain_english_error(tmp_path, monkeypatch):
    monkeypatch.setattr(photo.shutil, "which", lambda name: None)
    img = tmp_path / "photo.heic"
    img.write_bytes(b"heic-bytes")
    it = SimpleNamespace(path=img)
    with pytest.raises(photo.errors.StageError) as exc_info:
        photo.load_image_for_processing(it)
    e = exc_info.value
    assert "HEIC" in e.what
    assert e.transient is False  # won't fix itself on retry


def test_heic_conversion_failure_raises_plain_english_error(tmp_path, monkeypatch):
    monkeypatch.setattr(photo.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def boom(*a, **k):
        raise photo.subprocess.CalledProcessError(1, "ffmpeg")

    monkeypatch.setattr(photo.subprocess, "run", boom)
    img = tmp_path / "photo.heic"
    img.write_bytes(b"heic-bytes")
    it = SimpleNamespace(path=img)
    with pytest.raises(photo.errors.StageError):
        photo.load_image_for_processing(it)


def test_heic_conversion_success(tmp_path, monkeypatch):
    monkeypatch.setattr(photo.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def fake_run(cmd, **kwargs):
        # the destination path is the last positional arg in our ffmpeg call
        dst = Path(cmd[-1])
        dst.write_bytes(b"converted-jpeg-bytes")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(photo.subprocess, "run", fake_run)
    img = tmp_path / "photo.heic"
    img.write_bytes(b"heic-bytes")
    it = SimpleNamespace(path=img)
    data, mime = photo.load_image_for_processing(it)
    assert data == b"converted-jpeg-bytes"
    assert mime == "image/jpeg"


# ---- classify guard -----------------------------------------------------------

def test_classify_no_tag_no_text_never_calls_llm(monkeypatch):
    def must_not_be_called(*a, **k):
        raise AssertionError("llm_fn must not be called with nothing to go on")

    cls = photo.classify_image(item(tag=None), "", config(), llm_fn=must_not_be_called)
    assert cls.needs_review is True
    assert cls.confidence == 0.0
    assert cls.provider == "none"


def test_classify_tag_present_routes_free_even_with_no_text():
    cls = photo.classify_image(item(tag="todo"), "", config(), llm_fn=None)
    assert cls.type == "todo"
    assert cls.routed_by == "tag"
    assert cls.needs_review is False


def test_classify_no_tag_but_text_present_calls_classifier():
    def fake_llm(transcript, cfg):
        assert transcript == "some extracted text"
        return {"type": "learning", "confidence": 0.9, "title": "t",
                "categories": [], "subjects": [], "tags": []}

    cls = photo.classify_image(item(tag=None), "some extracted text", config(), llm_fn=fake_llm)
    assert cls.type == "learning"
    assert cls.needs_review is False


# ---- attachments copy ----------------------------------------------------------

def test_copy_to_attachments_creates_folder_and_file(tmp_path):
    vault = tmp_path / "vault"
    it = item(name="sunset")  # CAPTURED = 2026-08-13
    rel = photo.copy_to_attachments(it, b"image-bytes", ".jpg", vault)
    assert rel == "attachments/2026-08-13-sunset.jpg"
    assert (vault / rel).read_bytes() == b"image-bytes"


def test_copy_to_attachments_dedupes_on_collision(tmp_path):
    vault = tmp_path / "vault"
    it = item(name="photo")
    rel1 = photo.copy_to_attachments(it, b"first", ".jpg", vault)
    rel2 = photo.copy_to_attachments(it, b"second", ".jpg", vault)
    assert rel1 != rel2
    assert (vault / rel1).read_bytes() == b"first"


def test_copy_to_attachments_strips_hash_tag_suffix_for_yaml_safety(tmp_path):
    """A '#' in an unquoted YAML scalar starts a comment — the raw inbox
    filename's ' #tag' suffix (e.g. 'sunset #resource.jpg') must never leak
    into the attachments filename that lands in `cover:`/an embed link."""
    vault = tmp_path / "vault"
    it = item(name="a great tutorial screenshot", tag="resource")
    rel = photo.copy_to_attachments(it, b"bytes", ".jpg", vault)
    assert "#" not in rel
    assert "resource" not in rel  # the tag itself doesn't belong in the filename either


# ---- structure() (resource description writer) --------------------------------

def test_structure_uses_injected_router():
    def fake_router(prompt, cfg, validate):
        data = {"resource_type": "recipe", "title": "Shakshuka", "description": "eggs in tomato sauce"}
        assert validate(data) is None
        return data, "gemini-flash", []

    result = photo.structure("my thought", "recipe text here", config(), router=fake_router)
    assert result["resource_type"] == "recipe"
    assert result["title"] == "Shakshuka"


def test_structure_all_fail_degrades_to_plain_resource():
    def no_router(prompt, cfg, validate):
        return None, None, []

    result = photo.structure("thought", "text", config(), router=no_router)
    assert result == {"resource_type": "article", "title": "captured-photo", "description": ""}


# ---- note building --------------------------------------------------------------

def test_build_image_resource_note_has_required_fields():
    it = item(source="photo")
    structured = {"resource_type": "book", "title": "Designing Brand Identity",
                 "description": "A branding reference book."}
    text = photo.build_image_resource_note(
        it, structured, "extracted book cover text", "my thought about this book",
        "20260813143000", "2026-08-13", "attachments/photo.jpg")
    assert "type: resource" in text
    assert "resource_type: book" in text
    assert "cover: attachments/photo.jpg" in text
    assert "description: A branding reference book." in text
    assert "meta_origin: ai" in text
    assert "origin: human" in text
    assert "status: inbox" in text
    assert "![[attachments/photo.jpg]]" in text
    assert "## Insight" in text
    assert "my thought about this book" in text
    assert "## Extracted text" in text
    assert "extracted book cover text" in text


def test_build_image_resource_note_invalid_resource_type_defaults_to_article():
    it = item()
    structured = {"resource_type": "not-a-real-type", "title": "x", "description": ""}
    text = photo.build_image_resource_note(it, structured, "", "", "id", "2026-08-13", "attachments/x.jpg")
    assert "resource_type: article" in text


def test_build_image_resource_note_no_insight_when_no_user_text():
    it = item()
    structured = {"resource_type": "tool", "title": "x", "description": "d"}
    text = photo.build_image_resource_note(it, structured, "", "", "id", "2026-08-13", "attachments/x.jpg")
    assert "## Insight" not in text


def test_route_image_resource_writes_file(tmp_path):
    vault = tmp_path / "vault"
    it = item()
    structured = {"resource_type": "tool", "title": "Some Tool", "description": "d"}
    path = photo.route_image_resource(it, structured, "", "", vault, "attachments/x.jpg")
    assert path.exists()
    assert path.parent.name == "04-Resources"
    assert path.read_text() == photo.build_image_resource_note(
        it, structured, "", "", "20260813143000", "2026-08-13", "attachments/x.jpg")


def test_generic_image_body_assembles_embed_text_and_extraction():
    body = photo.generic_image_body("attachments/photo.jpg", "call the plumber", "extracted note text")
    assert body.startswith("![[attachments/photo.jpg]]")
    assert "call the plumber" in body
    assert "## Extracted text" in body
    assert "extracted note text" in body


def test_generic_image_body_omits_empty_sections():
    body = photo.generic_image_body("attachments/photo.jpg", "", "")
    assert body.strip() == "![[attachments/photo.jpg]]"
