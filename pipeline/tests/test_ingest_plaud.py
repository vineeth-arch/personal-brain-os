"""ingest.sweep's bundle awareness: applaud's directory-per-recording sync,
Plaud Desktop's flat sidecar export, and every fallback in between.

Every existing (pre-bundle) behavior in test_ingest.py must keep passing
unmodified — these tests cover only what bundles add.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import ingest, intake, plaud
from pipeline.events import EventLog


@pytest.fixture
def env(tmp_path):
    inbox = tmp_path / "inbox"
    watched = tmp_path / "PlaudSync"
    vault = tmp_path / "vault"
    for d in (inbox, watched, vault):
        d.mkdir()
    events = EventLog(tmp_path / "events.db", vault)
    return SimpleNamespace(tmp=tmp_path, inbox=inbox, watched=watched, events=events)


def cfg(env, source="plaud"):
    return SimpleNamespace(
        inbox_path=env.inbox,
        raw={"watch_folders": [{"path": str(env.watched), "source": source}]})


def _age(path: Path, seconds: int = 300) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def _applaud_folder(watched: Path, name: str, *, audio=True,
                    transcript="[00:01] Ana: hello\n[00:04] Ben: hi there",
                    summary=None, age_seconds: int = 300) -> Path:
    folder = watched / name
    folder.mkdir()
    if audio:
        p = folder / "audio.ogg"
        p.write_bytes(b"fake audio bytes")
        _age(p, age_seconds)
    if transcript is not None:
        p = folder / "transcript.txt"
        p.write_text(transcript, encoding="utf-8")
        _age(p, age_seconds)
    if summary is not None:
        p = folder / "summary.md"
        p.write_text(summary, encoding="utf-8")
        _age(p, age_seconds)
    return folder


# ---- applaud's directory shape ------------------------------------------------

def test_a_full_applaud_bundle_is_imported_with_transcript_sidecar(env):
    _applaud_folder(env.watched, "2026-04-11_Product_sync__74560101",
                    summary="Talked about the roadmap.")
    copied = ingest.sweep(cfg(env), env.events)
    assert len(copied) == 1
    dest = copied[0]
    assert dest.name.endswith(" Product sync.ogg")   # title from the folder name, not "audio"
    assert dest.parent == env.inbox / "plaud"

    transcript, summary = plaud.read_inbox_sidecars(dest)
    assert transcript is not None
    assert transcript.speakers == ["Ana", "Ben"]
    assert summary == "Talked about the roadmap."

    # the sidecars are invisible to the ordinary poll — one recording, one item
    items = intake.poll(env.inbox)
    assert len(items) == 1
    assert items[0].kind == "audio" and items[0].source == "plaud"


def test_a_second_sweep_does_not_reimport_a_bundle(env):
    _applaud_folder(env.watched, "2026-04-11_Product_sync__74560101")
    assert len(ingest.sweep(cfg(env), env.events)) == 1
    assert ingest.sweep(cfg(env), env.events) == []
    assert len(list((env.inbox / "plaud").glob("*.ogg"))) == 1


def test_an_audio_only_folder_still_processing_is_imported_without_a_sidecar(env):
    """No transcript yet — imported anyway, whisper picks it up at transcribe
    time exactly as it would for a lone recording with no Plaud involvement."""
    _applaud_folder(env.watched, "2026-04-11_still_processing", transcript=None)
    copied = ingest.sweep(cfg(env), env.events)
    assert len(copied) == 1
    transcript, summary = plaud.read_inbox_sidecars(copied[0])
    assert transcript is None and summary == ""


def test_a_transcript_only_folder_lands_as_a_visible_text_capture(env):
    """Audio hasn't arrived yet (or never will) — the words must not be lost."""
    _applaud_folder(env.watched, "2026-04-11_audio_pending", audio=False,
                    transcript="[00:01] Ana: don't lose this")
    copied = ingest.sweep(cfg(env), env.events)
    assert len(copied) == 1
    dest = copied[0]
    assert dest.suffix == ".txt"
    assert "don't lose this" in dest.read_text(encoding="utf-8")

    items = intake.poll(env.inbox)
    assert len(items) == 1
    assert items[0].kind == "text" and items[0].source == "plaud"


def test_a_folder_still_settling_is_left_for_the_next_tick(env):
    _applaud_folder(env.watched, "2026-04-11_fresh", age_seconds=1)
    assert ingest.sweep(cfg(env), env.events) == []


def test_an_unrecognisable_folder_is_left_alone(env):
    """No audio extension, no Plaud artifact names — sweep must not guess."""
    folder = env.watched / "2026-04-11_notes"
    folder.mkdir()
    (folder / "readme.md").write_text("not a recording", encoding="utf-8")
    _age(folder / "readme.md")
    assert ingest.sweep(cfg(env), env.events) == []


# ---- Plaud Desktop's flat sidecar shape ---------------------------------------

def test_flat_audio_with_a_matching_sidecar_transcript(env):
    audio = env.watched / "Client call.m4a"
    audio.write_bytes(b"fake audio bytes")
    (env.watched / "Client call.txt").write_text("Ana: hello\nBen: hi", encoding="utf-8")
    _age(audio)
    _age(env.watched / "Client call.txt")

    copied = ingest.sweep(cfg(env), env.events)
    assert len(copied) == 1
    dest = copied[0]
    assert dest.name.endswith("Client call.m4a")   # unchanged naming for this shape
    transcript, _ = plaud.read_inbox_sidecars(dest)
    assert transcript is not None and transcript.speakers == ["Ana", "Ben"]


def test_flat_audio_with_no_sidecar_is_unaffected(env):
    """The ordinary Voice Memos case — zero behavior change."""
    audio = env.watched / "memo.m4a"
    audio.write_bytes(b"fake audio bytes")
    _age(audio)
    copied = ingest.sweep(cfg(env, source="voice"), env.events)
    assert len(copied) == 1
    transcript, summary = plaud.read_inbox_sidecars(copied[0])
    assert transcript is None and summary == ""
    # and nothing extra shows up in intake's view of the inbox
    assert len(intake.poll(env.inbox)) == 1


def test_flat_audio_prefers_srt_over_no_sidecar_and_stays_undated(env):
    audio = env.watched / "standup.m4a"
    audio.write_bytes(b"fake audio bytes")
    (env.watched / "standup.srt").write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nAna: morning\n", encoding="utf-8")
    _age(audio)
    _age(env.watched / "standup.srt")
    copied = ingest.sweep(cfg(env), env.events)
    transcript, _ = plaud.read_inbox_sidecars(copied[0])
    assert transcript is not None and "morning" in transcript.body


# ---- sidecar writes must never undo a successful audio import ----------------

def test_a_sidecar_write_failure_does_not_undo_the_audio_import(env, monkeypatch):
    _applaud_folder(env.watched, "2026-04-11_x")
    monkeypatch.setattr(ingest, "_atomic_write", lambda *a, **kw: False)
    copied = ingest.sweep(cfg(env), env.events)
    assert len(copied) == 1                      # the audio still landed
    assert copied[0].exists()
    transcript, _ = plaud.read_inbox_sidecars(copied[0])
    assert transcript is None                     # sidecar simply didn't write
