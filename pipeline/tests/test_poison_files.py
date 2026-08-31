"""Pass H3 — poison-file robustness. Railway restarts the container
ON_FAILURE×10 and the API + watcher share one process/container there, so an
unhandled exception from one bad file is a real crash-loop risk, not just an
ugly log line.

Audio: a permanent transcription failure (garbage bytes whisper.cpp can't
read) must quarantine to failed/ with a plain-English envelope — never raise
out of run_once.

Images: by design, the server never decodes an image at all (D-RESIZE,
CLAUDE.md §7 — no Pillow) — so "poison" bytes with a .jpg extension are not a
parseable/unparseable distinction the pipeline can even make. They move into
attachments/ exactly like a good photo, vision.describe() degrades to None on
whatever Claude makes of them (or on a missing key, hermetically here), and
an honest, undescribed note is still written. This is a STRONGER guarantee
than quarantine, not a gap: nothing about processing an image can fail."""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import errors, watcher
from pipeline.config import Config
from pipeline.events import EventLog
from pipeline.transcribe import Transcriber


class PoisonTranscriber(Transcriber):
    """Stands in for whisper.cpp choking on bytes that aren't real audio —
    a permanent (non-transient) failure, same shape transcribe.py raises for
    a corrupt file or a missing binary."""

    def transcribe(self, audio_path: Path) -> str:
        raise errors.StageError(
            "This recording couldn't be transcribed.",
            "The audio file appears to be corrupt or in a format whisper.cpp can't read.",
            "Check the original file; if it plays fine elsewhere, this stays a bug to report.",
            transient=False)


@pytest.fixture
def vault_env(tmp_path, monkeypatch):
    vault, inbox, archive, failed = (tmp_path / d for d in ("vault", "inbox", "archive", "failed"))
    for d in (vault, inbox, archive, failed):
        d.mkdir()
    monkeypatch.setattr(watcher, "DB_PATH", tmp_path / "events.db")
    monkeypatch.setattr(watcher, "HEARTBEAT_PATH", tmp_path / ".watcher-heartbeat")
    config = Config(vault_path=vault, inbox_path=inbox, archive_path=archive, failed_path=failed)
    return config, tmp_path


def test_poison_audio_is_quarantined_with_an_envelope_never_crashes(vault_env):
    config, tmp_path = vault_env
    (config.inbox_path / "2026-07-03-0900 corrupt.m4a").write_bytes(b"definitely not real audio")
    events = EventLog(tmp_path / "events.db", config.vault_path)
    deps = watcher.Deps(transcriber=PoisonTranscriber(), sleep=lambda s: None)

    # the call itself must not raise — that's the crash-loop this test exists for
    results = watcher.run_once(config, events, deps)

    assert len(results) == 1
    assert results[0].status == "failed"
    failed_files = list(config.failed_path.iterdir())
    assert len(failed_files) == 1 and failed_files[0].name.endswith(".m4a")
    assert not any(config.inbox_path.iterdir()), "the poison file must leave the inbox either way"

    row = events.conn.execute(
        "SELECT plain_english_error FROM events WHERE stage='pipeline' AND status='failed'"
    ).fetchone()
    assert row is not None
    envelope = row[0]
    assert envelope.startswith("What happened:")
    assert "Likely cause:" in envelope and "What to do:" in envelope
    events.close()


def test_a_second_poison_file_does_not_stop_the_batch(vault_env):
    """One bad file never stops the run — a good capture after a bad one in
    the same batch must still process."""
    config, tmp_path = vault_env
    (config.inbox_path / "2026-07-03-0900 corrupt.m4a").write_bytes(b"garbage")
    (config.inbox_path / "2026-07-03-0901 a-thought.txt").write_text("a fine text capture",
                                                                     encoding="utf-8")
    events = EventLog(tmp_path / "events.db", config.vault_path)
    deps = watcher.Deps(transcriber=PoisonTranscriber(), sleep=lambda s: None,
                        classifier_fn=lambda transcript, cfg: {
                            "type": "musing", "categories": [], "subjects": [], "tags": [],
                            "confidence": 0.9, "title": "a thought"})

    results = watcher.run_once(config, events, deps)
    assert len(results) == 2
    statuses = {r.name: r.status for r in results}
    assert statuses["2026-07-03-0900 corrupt.m4a"] == "failed"
    assert statuses["2026-07-03-0901 a-thought.txt"] != "failed"
    events.close()


def test_poison_image_bytes_still_produce_an_honest_note_not_a_crash(vault_env):
    config, tmp_path = vault_env
    (config.inbox_path / "2026-07-03-0900 corrupt.jpg").write_bytes(b"\xff\xd8not really jpeg data at all")
    events = EventLog(tmp_path / "events.db", config.vault_path)
    # no vision_caller — hermetic, and there's no ANTHROPIC_API_KEY in a test
    # environment, so vision.describe degrades to None regardless of the bytes
    deps = watcher.Deps(transcriber=None)

    results = watcher.run_once(config, events, deps)

    assert len(results) == 1 and results[0].status != "failed", results[0].error
    assert list(config.failed_path.iterdir()) == [], \
        "an image is never quarantined for its content — the server never decodes it"
    attachments = list((config.vault_path / "attachments").iterdir())
    assert len(attachments) == 1 and attachments[0].read_bytes().startswith(b"\xff\xd8")
    resources = list((config.vault_path / "04-Resources").glob("*.md"))
    assert len(resources) == 1
    text = resources[0].read_text(encoding="utf-8")
    assert "enriched: false" in text  # honest: no description, nothing invented
    events.close()
