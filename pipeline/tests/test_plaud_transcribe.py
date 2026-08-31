"""Stage 2/3 for a Plaud item: read the device's transcript instead of
re-hearing it, and let a multi-speaker transcript decide "conversation"
without spending a model call. End to end against pipeline.watcher.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import plaud, relationships, watcher
from pipeline.config import Config
from pipeline.events import EventLog
from pipeline.transcribe import Transcriber


class ExplodingTranscriber(Transcriber):
    """Proves whisper is never called for a Plaud item with a transcript."""
    def transcribe(self, audio_path: Path) -> str:
        raise AssertionError("whisper must not run when a Plaud transcript exists")


class FakeTranscriber(Transcriber):
    def transcribe(self, audio_path: Path) -> str:
        return "#journal a plain whisper transcript, no speakers"


def stub_classifier(transcript: str, config) -> dict:
    return {"type": "musing", "categories": [], "subjects": [], "tags": [],
           "confidence": 0.95, "title": "a self memo"}


def _fm(note_path: Path) -> dict:
    text = note_path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    block = text.split("---\n", 2)[1]
    out: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    failed = tmp_path / "failed"
    for d in (vault, inbox, archive, failed):
        d.mkdir()
    monkeypatch.setattr(watcher, "DB_PATH", tmp_path / "events.db")
    monkeypatch.setattr(watcher, "HEARTBEAT_PATH", tmp_path / ".watcher-heartbeat")
    config = Config(vault_path=vault, inbox_path=inbox, archive_path=archive, failed_path=failed)
    events = EventLog(tmp_path / "events.db", vault)
    return config, events, inbox, vault


def _drop_bundle(inbox: Path, filename: str, transcript_body: str, summary: str = "") -> Path:
    """The exact on-disk shape ingest.sweep leaves: an audio file plus its
    hidden sidecars, written straight into the inbox — this test starts one
    stage later than the ingest tests, at the point the watcher sees it."""
    audio = inbox / "plaud" / filename
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"fake audio bytes")
    t_path, s_path = plaud.sidecar_paths(audio)
    t_path.write_text(transcript_body, encoding="utf-8")
    if summary:
        s_path.write_text(summary, encoding="utf-8")
    return audio


def test_a_multi_speaker_transcript_becomes_a_conversation_without_whisper(env):
    config, events, inbox, vault = env
    _drop_bundle(inbox, "2026-08-30-0900 Product sync.ogg",
                "[00:01] Ana Silva: let's get started\n[00:04] Ben Carter: sounds good")
    deps = watcher.Deps(transcriber=ExplodingTranscriber())   # would raise if called

    results = watcher.run_once(config, events, deps)
    assert len(results) == 1 and results[0].status == "needs_review"

    note = next((vault / "00-Inbox").glob("*.md"))
    fm = _fm(note)
    assert fm["type"] == "conversation"
    assert fm["status"] == "needs-review"
    assert fm["transcript_source"] == "plaud"
    assert fm["attendees"] == "[]"                 # never written by the pipeline
    body = note.read_text(encoding="utf-8")
    assert "let's get started" in body             # kept verbatim (§8)
    assert "Ana Silva" in body


def test_speakers_are_written_as_a_raw_list(env):
    config, events, inbox, vault = env
    _drop_bundle(inbox, "2026-08-30-0900 x.ogg",
                "[00:01] Ana: hi\n[00:02] Ben: hey")
    watcher.run_once(config, events, watcher.Deps(transcriber=ExplodingTranscriber()))
    note = next((vault / "00-Inbox").glob("*.md"))
    body = note.read_text(encoding="utf-8")
    assert "speakers:" in body and "- Ana" in body and "- Ben" in body


def test_note_title_comes_from_the_bundle_not_the_word_audio(env):
    """A regression guard on the ingest-side naming fix (da1ad02): the note's
    filename must reflect the recording's actual name, not applaud's own
    audio.ogg/transcript.txt naming."""
    config, events, inbox, vault = env
    _drop_bundle(inbox, "2026-08-30-0900 Weekly standup.ogg",
                "[00:01] Ana: hi\n[00:02] Ben: hey")
    watcher.run_once(config, events, watcher.Deps(transcriber=ExplodingTranscriber()))
    note = next((vault / "00-Inbox").glob("*.md"))
    assert "weekly-standup" in note.stem.lower()


def test_matched_speakers_are_suggested_but_never_written_to_the_person_note(env):
    config, events, inbox, vault = env
    (vault / "07-People").mkdir()
    (vault / "07-People" / "2026-01-01-ana-silva.md").write_text(
        "---\nid: 20260101000000\ntype: person\ncreated: 2026-01-01\nsource: manual\n"
        "origin: human\nrelationship:\ncompany:\nchannels: {}\ndex_id:\ndex_deeplink:\n"
        "cadence_days:\nlast_contact:\nwarmth_stage:\nstatus: active\ncategories: []\n"
        "subjects: []\ntags: []\n---\n\n# Ana Silva\n\n## Context\n\n\n## Needs\n\n\n"
        "## Interaction log\n\n\n## Next action\n\n\n", encoding="utf-8")
    before = (vault / "07-People" / "2026-01-01-ana-silva.md").read_text(encoding="utf-8")

    _drop_bundle(inbox, "2026-08-30-0900 sync.ogg",
                "[00:01] Ana Silva: hi everyone\n[00:04] Unknown Guest: hello")
    watcher.run_once(config, events, watcher.Deps(transcriber=ExplodingTranscriber()))

    # the person note is untouched — confirming attendees is a human act, not
    # something the pipeline does on write (CLAUDE.md §3)
    after = (vault / "07-People" / "2026-01-01-ana-silva.md").read_text(encoding="utf-8")
    assert before == after

    # but the match WAS made and surfaced, the same way classify confidence is
    # surfaced — through the event log, for the API/triage screen to read
    rows = events.conn.execute(
        "SELECT message FROM events WHERE stage='attendees'").fetchall()
    assert len(rows) == 1
    assert "Ana Silva:20260101000000" in rows[0][0]
    assert "Unknown Guest" not in rows[0][0]        # unmatched speakers aren't suggested


def test_a_single_speaker_plaud_memo_classifies_normally(env):
    """Most Plaud recordings are self voice memos, not meetings — one speaker
    must NOT trip the conversation path, and must carry none of its extra
    frontmatter."""
    config, events, inbox, vault = env
    _drop_bundle(inbox, "2026-08-30-0900 memo.ogg", "[00:01] Me: buy milk tomorrow")
    deps = watcher.Deps(transcriber=ExplodingTranscriber(), classifier_fn=stub_classifier)
    watcher.run_once(config, events, deps)

    note = next((vault / "02-Musings").glob("*.md"))
    fm = _fm(note)
    assert fm["type"] == "musing"
    assert "transcript_source" not in fm
    assert "speakers" not in fm
    assert "attendees" not in fm


def test_no_sidecar_falls_back_to_whisper_exactly_as_before(env):
    """The ordinary case, unaffected: a Plaud-sourced item with no transcript
    yet (still processing) is transcribed like any other recording."""
    config, events, inbox, vault = env
    audio = inbox / "plaud" / "2026-08-30-0900 memo.ogg"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"fake audio bytes")
    # no sidecars written at all

    deps = watcher.Deps(transcriber=FakeTranscriber())
    results = watcher.run_once(config, events, deps)
    assert len(results) == 1 and results[0].status != "failed"
    note = next((vault / "01-Journal").glob("*.md"))
    assert "a plain whisper transcript" in note.read_text(encoding="utf-8")


def test_text_and_link_items_are_never_treated_as_plaud_bundles(env):
    """The is_conversation gate is scoped to item.kind == 'audio' — a text
    capture must never read a sidecar meant for an audio file at all."""
    config, events, inbox, vault = env
    (inbox / "2026-08-30-0900 a text note.txt").write_text(
        "just some words, no colons here at all", encoding="utf-8")
    deps = watcher.Deps(transcriber=ExplodingTranscriber(), classifier_fn=stub_classifier)
    results = watcher.run_once(config, events, deps)
    assert len(results) == 1 and results[0].status != "failed"
