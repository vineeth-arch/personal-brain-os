"""End-to-end pipeline test against a temp vault. Hermetic: a FakeTranscriber
supplies canned text for the wav and a stub classifier stands in for Haiku, so
no whisper.cpp binary and no network are needed."""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from pipeline import watcher
from pipeline.config import Config
from pipeline.transcribe import Transcriber

FIXTURES = Path(__file__).parent / "fixtures"


class FakeTranscriber(Transcriber):
    # Spoken #journal in the first 5 words → free tag-route; also an action item.
    def transcribe(self, audio_path: Path) -> str:
        return "#journal Today I walked and I need to call the dentist tomorrow."


def stub_classifier(transcript: str, config) -> dict:
    return {
        "type": "learning",
        "categories": ["Memory"],
        "subjects": ["Spaced repetition"],
        "tags": ["learning"],
        "confidence": 0.95,
        "title": "spaced repetition retrieval",
    }


def _fm(note_path: Path) -> dict:
    """Parse the flat frontmatter block into a dict (values are raw strings)."""
    text = note_path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    block = text.split("---\n", 2)[1]
    out = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


@pytest.fixture
def vault_env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    failed = tmp_path / "failed"
    for d in (vault, inbox, archive, failed):
        d.mkdir()
    for f in FIXTURES.iterdir():
        shutil.copy(f, inbox / f.name)

    monkeypatch.setattr(watcher, "DB_PATH", tmp_path / "events.db")
    monkeypatch.setattr(watcher, "HEARTBEAT_PATH", tmp_path / ".watcher-heartbeat")

    config = Config(vault_path=vault, inbox_path=inbox, archive_path=archive, failed_path=failed)
    return config, tmp_path


def _notes_of_type(vault: Path, folder: str, ntype: str) -> list[Path]:
    # Skip non-note files (e.g. the daily todo list has no frontmatter).
    return [p for p in (vault / folder).glob("*.md")
            if p.read_text(encoding="utf-8").startswith("---\n") and _fm(p).get("type") == ntype]


def test_end_to_end(vault_env):
    config, tmp_path = vault_env
    from pipeline.events import EventLog
    events = EventLog(tmp_path / "events.db", config.vault_path)
    deps = watcher.Deps(transcriber=FakeTranscriber(), classifier_fn=stub_classifier)

    results = watcher.run_once(config, events, deps)

    # 3 files processed, none failed.
    assert len(results) == 3
    assert all(r.status != "failed" for r in results), [r.error for r in results]

    # #todo fixture → routed by tag → 06-Todos, type todo.
    todo_notes = _notes_of_type(config.vault_path, "06-Todos", "todo")
    assert len(todo_notes) == 1
    fm = _fm(todo_notes[0])
    assert len(fm["id"]) == 14 and fm["id"].isdigit()   # immutable timestamp id
    assert fm["origin"] == "human"                       # body is human-origin
    assert fm["meta_origin"] == "human"                  # tag-route, no AI metadata

    # wav → spoken #journal → 01-Journal, type journal.
    assert len(_notes_of_type(config.vault_path, "01-Journal", "journal")) == 1

    # plain.md (no tag) → stub Haiku → 03-Learnings, type learning, AI-added metadata.
    learn = _notes_of_type(config.vault_path, "03-Learnings", "learning")
    assert len(learn) == 1
    assert _fm(learn[0])["meta_origin"] == "ai"

    # Action items appended to a daily todo file (extra write, not the note body).
    todo_days = [p for p in (config.vault_path / "06-Todos").glob("*.md")
                 if p.stem.count("-") == 2 and "- [ ]" in p.read_text(encoding="utf-8")]
    assert todo_days, "expected action items appended to 06-Todos/<date>.md"

    # Sources archived (never deleted), inbox drained.
    assert not any(config.inbox_path.iterdir())
    assert len(list(config.archive_path.iterdir())) == 3

    # One ok archive event per file in the disposable event log.
    con = sqlite3.connect(tmp_path / "events.db")
    n = con.execute("SELECT COUNT(DISTINCT file) FROM events WHERE stage='archive' AND status='ok'").fetchone()[0]
    con.close()
    assert n == 3

    # Vault artifacts written.
    assert (config.vault_path / "_System" / "PIPELINE-STATUS.md").exists()
    assert (config.vault_path / "_System" / "capture_log.md").exists()
    assert (tmp_path / ".watcher-heartbeat").exists()
    events.close()


def test_mic_recording_runs_end_to_end(tmp_path, monkeypatch):
    """Pass V: a .webm from the cockpit's mic button is a first-class capture —
    the browser's format must reach a routed note like any phone recording."""
    from pipeline.events import EventLog

    vault, inbox, archive, failed = (tmp_path / d for d in ("vault", "inbox", "archive", "failed"))
    for d in (vault, inbox, archive, failed):
        d.mkdir()
    # exactly what api/notes.audio_capture_path stamps for a tagless recording
    (inbox / "2026-07-03-0900 voice-note.webm").write_bytes(b"\x1aE\xdf\xa3webm-ish bytes")

    monkeypatch.setattr(watcher, "DB_PATH", tmp_path / "events.db")
    monkeypatch.setattr(watcher, "HEARTBEAT_PATH", tmp_path / ".watcher-heartbeat")
    config = Config(vault_path=vault, inbox_path=inbox, archive_path=archive, failed_path=failed)
    events = EventLog(tmp_path / "events.db", vault)
    deps = watcher.Deps(transcriber=FakeTranscriber(), classifier_fn=stub_classifier)

    results = watcher.run_once(config, events, deps)

    assert len(results) == 1 and results[0].status != "failed", results[0].error
    # the FakeTranscriber speaks "#journal …" → free tag-route into 01-Journal
    notes = _notes_of_type(vault, "01-Journal", "journal")
    assert len(notes) == 1
    fm = _fm(notes[0])
    assert fm["source"] == "voice" and fm["origin"] == "human"
    assert len(fm["id"]) == 14 and fm["id"].isdigit()
    # source archived, never deleted; inbox drained
    assert not any(inbox.iterdir())
    assert len(list(archive.iterdir())) == 1


class HindiTranscriber(Transcriber):
    """What whisper returns for Hindi speech: Devanagari, not Roman."""

    def transcribe(self, audio_path: Path) -> str:
        return "#journal मैं कल दफ़्तर जाऊँगा और मुझे डॉक्टर को कॉल करना है"


def test_hindi_recording_becomes_one_note_with_both_scripts(tmp_path, monkeypatch):
    """Pass P: the body leads with Hinglish (what the owner reads), the
    Devanagari original stays in the SAME note, and one recording is still one
    note (SCHEMA-REFERENCE.md §8)."""
    from pipeline.events import EventLog
    from pipeline import transliterate

    vault, inbox, archive, failed = (tmp_path / d for d in ("vault", "inbox", "archive", "failed"))
    for d in (vault, inbox, archive, failed):
        d.mkdir()
    (inbox / "2026-07-03-0900 morning note.m4a").write_bytes(b"fake audio")

    monkeypatch.setattr(watcher, "DB_PATH", tmp_path / "events.db")
    monkeypatch.setattr(watcher, "HEARTBEAT_PATH", tmp_path / ".watcher-heartbeat")
    # a 3-minute recording: long enough to stamp duration, short enough not to chunk
    monkeypatch.setattr(watcher.transcribe_mod, "probe_duration_seconds", lambda p: 180.0)

    config = Config(vault_path=vault, inbox_path=inbox, archive_path=archive, failed_path=failed,
                    raw={"transliteration": {"engine": "ollama",
                                             "ollama": {"url": "http://x", "model": "m"}}})
    events = EventLog(tmp_path / "events.db", vault)
    seen = []

    def fake_transliterator(text, block):
        seen.append(text)
        # a real engine keeps the spoken #tag (see transliterate.PROMPT) — the
        # tag is what free-routes the note, so the stub must keep it too
        return "#journal main kal daftar jaunga aur mujhe doctor ko call karna hai"

    deps = watcher.Deps(transcriber=HindiTranscriber(), classifier_fn=stub_classifier,
                        transliterate_fn=fake_transliterator)
    results = watcher.run_once(config, events, deps)
    assert len(results) == 1 and results[0].status != "failed", results[0].error

    notes = _notes_of_type(vault, "01-Journal", "journal")
    assert len(notes) == 1, "one recording is one note, in either script"
    text = notes[0].read_text(encoding="utf-8")
    body = text.split("---\n", 2)[2]

    assert body.strip().startswith("#journal main kal daftar jaunga")   # Hinglish leads
    assert transliterate.ORIGINAL_HEADING in body             # original kept
    assert "मैं कल दफ़्तर जाऊँगा" in body
    assert _fm(notes[0])["duration_min"] == "3"


def test_a_dead_transliteration_engine_still_writes_the_note(tmp_path, monkeypatch):
    """The capture is the point. If Ollama is off, the note is written in
    Devanagari and the event log says why — nothing is quarantined."""
    from pipeline.events import EventLog

    vault, inbox, archive, failed = (tmp_path / d for d in ("vault", "inbox", "archive", "failed"))
    for d in (vault, inbox, archive, failed):
        d.mkdir()
    (inbox / "2026-07-03-0900 morning note.m4a").write_bytes(b"fake audio")
    monkeypatch.setattr(watcher, "DB_PATH", tmp_path / "events.db")
    monkeypatch.setattr(watcher, "HEARTBEAT_PATH", tmp_path / ".watcher-heartbeat")
    monkeypatch.setattr(watcher.transcribe_mod, "probe_duration_seconds", lambda p: 120.0)

    config = Config(vault_path=vault, inbox_path=inbox, archive_path=archive, failed_path=failed,
                    raw={"transliteration": {"engine": "ollama",
                                             "ollama": {"url": "http://x", "model": "m"}}})
    events = EventLog(tmp_path / "events.db", vault)

    def dead_engine(text, block):
        raise OSError("connection refused")

    deps = watcher.Deps(transcriber=HindiTranscriber(), classifier_fn=stub_classifier,
                        transliterate_fn=dead_engine)
    results = watcher.run_once(config, events, deps)
    assert len(results) == 1 and results[0].status != "failed"

    notes = _notes_of_type(vault, "01-Journal", "journal")
    assert len(notes) == 1
    assert "मैं कल दफ़्तर जाऊँगा" in notes[0].read_text(encoding="utf-8")

    rows = sqlite3.connect(tmp_path / "events.db").execute(
        "SELECT status, message FROM events WHERE stage = 'transliterate'").fetchall()
    assert rows and rows[0][0] == "failed" and "Devanagari" in rows[0][1]


def test_run_once_drains_watched_folders_too(tmp_path, monkeypatch):
    """D1: watch_folders used to only sweep inside --loop. Every entry point
    (one-shot run, --backlog, POST /api/run which shells out to `python -m
    pipeline`) must pick up recordings sitting in an app-owned folder."""
    from pipeline.events import EventLog

    vault, inbox, archive, failed = (tmp_path / d for d in ("vault", "inbox", "archive", "failed"))
    watched = tmp_path / "PlaudDesktop"
    for d in (vault, inbox, archive, failed, watched):
        d.mkdir()
    old = (watched / "meeting.m4a")
    old.write_bytes(b"fake audio")
    import os
    import time
    stale = time.time() - 300
    os.utime(old, (stale, stale))

    monkeypatch.setattr(watcher, "DB_PATH", tmp_path / "events.db")
    monkeypatch.setattr(watcher, "HEARTBEAT_PATH", tmp_path / ".watcher-heartbeat")
    config = Config(vault_path=vault, inbox_path=inbox, archive_path=archive, failed_path=failed,
                    raw={"watch_folders": [{"path": str(watched), "source": "plaud"}]})
    events = EventLog(tmp_path / "events.db", vault)
    deps = watcher.Deps(transcriber=FakeTranscriber(), classifier_fn=stub_classifier)

    results = watcher.run_once(config, events, deps)

    assert old.exists(), "the watched folder's own copy is never touched"
    assert len(results) == 1 and results[0].status != "failed"
    assert not any((inbox / "plaud").glob("*")), "the copy was processed and archived, not left behind"
