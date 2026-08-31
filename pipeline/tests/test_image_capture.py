"""Pass 13 — end-to-end image capture through the real watcher pipeline.
Mirrors test_pipeline.py's pattern: a temp vault, real archive/quarantine
code, injected vision/classifier routers so no network or real API keys are
needed. Proves the whole wiring (intake -> vision -> classify -> route ->
extract -> archive) for images, not just the individual unit pieces."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pipeline import watcher
from pipeline.config import Config
from pipeline.events import EventLog
from pipeline.transcribe import Transcriber

JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes-for-testing"


class NullTranscriber(Transcriber):
    def transcribe(self, audio_path: Path) -> str:
        raise AssertionError("no audio in these tests — the audio transcriber must not run")


@pytest.fixture
def vault_env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    failed = tmp_path / "failed"
    for d in (vault, inbox, archive, failed):
        d.mkdir()
    monkeypatch.setattr(watcher, "DB_PATH", tmp_path / "events.db")
    monkeypatch.setattr(watcher, "HEARTBEAT_PATH", tmp_path / ".watcher-heartbeat")
    config = Config(vault_path=vault, inbox_path=inbox, archive_path=archive, failed_path=failed)
    return config, tmp_path


def _write_capture(inbox: Path, name: str, text: str = "", tag: str | None = None, ocr: str = ""):
    suffix = f" #{tag}" if tag else ""
    img = inbox / f"2026-08-13-1200 {name}{suffix}.jpg"
    img.write_bytes(JPEG)
    (inbox / f"{img.stem}.meta.json").write_text(
        json.dumps({"text": text, "ocr": ocr, "source": "photo"}))
    return img


def _fm(note_path: Path) -> dict:
    text = note_path.read_text()
    assert text.startswith("---\n")
    block = text.split("---\n", 2)[1]
    out = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


def _run(config, tmp_path, **dep_kwargs):
    events = EventLog(tmp_path / "events.db", config.vault_path)
    deps = watcher.Deps(transcriber=NullTranscriber(), **dep_kwargs)
    results = watcher.run_once(config, events, deps)
    events.close()
    return results


def test_tagged_resource_photo_gets_full_resource_schema(vault_env):
    config, tmp_path = vault_env
    _write_capture(config.inbox_path, "recipe-card", text="looks like a great recipe",
                   tag="resource", ocr="Shakshuka: eggs, tomato, peppers, cumin.")

    def vision_router(image_bytes, mime, cfg):
        raise AssertionError("OCR was long enough — vision provider must not be called")

    def enrich_router(prompt, cfg, validate):
        data = {"resource_type": "recipe", "title": "Shakshuka",
                "description": "A quick tomato-and-egg breakfast."}
        assert validate(data) is None
        return data, "gemini-flash", []

    results = _run(config, tmp_path, vision_router=vision_router, enrich_router=enrich_router)
    assert len(results) == 1 and results[0].status == "ok"

    notes = list((config.vault_path / "04-Resources").glob("*.md"))
    assert len(notes) == 1
    fm = _fm(notes[0])
    assert fm["type"] == "resource"
    assert fm["resource_type"] == "recipe"
    assert fm["cover"].startswith("attachments/")
    assert fm["description"] == "A quick tomato-and-egg breakfast."
    assert fm["meta_origin"] == "ai"
    assert fm["status"] == "inbox"
    body = notes[0].read_text()
    assert "## Insight" in body and "looks like a great recipe" in body
    assert "## Extracted text" in body and "Shakshuka: eggs" in body

    # the image was COPIED into attachments/ (not moved) — the original still
    # got archived by the normal stage-6 step
    assert (config.vault_path / "attachments").is_dir()
    assert len(list((config.vault_path / "attachments").iterdir())) == 1
    assert len(list(config.archive_path.glob("*.jpg"))) == 1
    assert len(list(config.archive_path.glob("*.meta.json"))) == 1  # sidecar archived too
    assert not any(config.inbox_path.iterdir())  # inbox fully drained


def test_tagged_todo_photo_uses_generic_note_with_embed(vault_env):
    config, tmp_path = vault_env
    _write_capture(config.inbox_path, "whiteboard", text="call the plumber tomorrow", tag="todo")

    def vision_router(image_bytes, mime, cfg):
        return {"extracted_text": "TODO: fix the sink, call the plumber"}, "gemini-flash", []

    results = _run(config, tmp_path, vision_router=vision_router)
    assert results[0].status == "ok"

    notes = [p for p in (config.vault_path / "06-Todos").glob("*.md")
             if p.read_text().startswith("---\n") and _fm(p).get("type") == "todo"]
    assert len(notes) == 1
    fm = _fm(notes[0])
    assert fm["meta_origin"] == "human"  # tag-routed, no AI classification metadata
    body = notes[0].read_text()
    assert "![[attachments/" in body
    assert "call the plumber tomorrow" in body
    assert "## Extracted text" in body
    assert "fix the sink" in body

    # action-item extraction (Stage 5) still ran on the extracted text
    todo_days = [p for p in (config.vault_path / "06-Todos").glob("*.md")
                 if p.stem.count("-") == 2 and "- [ ]" in p.read_text()]
    assert todo_days, "expected an action item appended to the daily todo file"


def test_untagged_photo_classified_by_llm(vault_env):
    config, tmp_path = vault_env
    _write_capture(config.inbox_path, "book-page")

    def vision_router(image_bytes, mime, cfg):
        return {"extracted_text": "Chapter 3: The Structure of Scientific Revolutions"}, "claude-haiku", []

    def classifier_fn(transcript, cfg):
        assert "Structure of Scientific" in transcript
        return {"type": "learning", "categories": [], "subjects": [], "tags": [],
               "confidence": 0.9, "title": "paradigm-shifts"}

    results = _run(config, tmp_path, vision_router=vision_router, classifier_fn=classifier_fn)
    assert results[0].status == "ok"
    notes = [p for p in (config.vault_path / "03-Learnings").glob("*.md")
             if p.read_text().startswith("---\n") and _fm(p).get("type") == "learning"]
    assert len(notes) == 1
    assert _fm(notes[0])["meta_origin"] == "ai"


def test_untagged_photo_no_extraction_needs_review_never_a_guess(vault_env):
    """All-fail vision extraction + no tag must go straight to needs-review —
    never an LLM call on nothing, never a silent guess."""
    config, tmp_path = vault_env
    _write_capture(config.inbox_path, "blurry-shot")

    def vision_router(image_bytes, mime, cfg):
        return None, None, []  # every vision provider "failed"

    def classifier_fn(transcript, cfg):
        raise AssertionError("classifier must not be called with nothing to go on")

    results = _run(config, tmp_path, vision_router=vision_router, classifier_fn=classifier_fn)
    assert results[0].status == "needs_review"
    notes = list((config.vault_path / "00-Inbox").glob("*.md"))
    assert len(notes) == 1
    assert _fm(notes[0])["status"] == "needs-review"


def test_heic_without_ffmpeg_quarantines_with_sidecar(vault_env, monkeypatch):
    config, tmp_path = vault_env
    from pipeline import photo
    monkeypatch.setattr(photo.shutil, "which", lambda name: None)
    img = config.inbox_path / "2026-08-13-1200 iphone-photo.heic"
    img.write_bytes(b"heic-bytes")
    (config.inbox_path / f"{img.stem}.meta.json").write_text(
        json.dumps({"text": "a moment", "ocr": "", "source": "photo"}))

    results = _run(config, tmp_path)
    assert results[0].status == "failed"
    assert len(list(config.failed_path.glob("*.heic"))) == 1
    assert len(list(config.failed_path.glob("*.meta.json"))) == 1  # sidecar quarantined too
    assert not any(config.inbox_path.iterdir())


def test_syncthing_arrived_image_with_no_sidecar_still_processes(vault_env):
    """An image dropped in via Syncthing (no capture endpoint involved) has
    no sidecar at all — must degrade gracefully, not crash."""
    config, tmp_path = vault_env
    img = config.inbox_path / "2026-08-13-1200 synced-photo #idea.jpg"
    img.write_bytes(JPEG)

    def vision_router(image_bytes, mime, cfg):
        return {"extracted_text": "a napkin sketch of an idea"}, "gemini-flash", []

    results = _run(config, tmp_path, vision_router=vision_router)
    assert results[0].status == "ok"
    notes = [p for p in (config.vault_path / "02-Musings").glob("*.md") if p.read_text().startswith("---\n")]
    assert len(notes) == 1
    assert "napkin sketch" in notes[0].read_text()


def test_events_log_vision_stage(vault_env):
    config, tmp_path = vault_env
    _write_capture(config.inbox_path, "photo", text="x", ocr="on-device OCR text long enough to short-circuit the vision chain entirely")

    _run(config, tmp_path)
    con = sqlite3.connect(tmp_path / "events.db")
    rows = con.execute("SELECT status, message FROM events WHERE stage='vision'").fetchall()
    con.close()
    assert any("on-device-ocr" in (msg or "") for _, msg in rows)
