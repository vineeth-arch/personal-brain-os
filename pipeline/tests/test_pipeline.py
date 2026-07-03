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
    text = note_path.read_text()
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
            if p.read_text().startswith("---\n") and _fm(p).get("type") == ntype]


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

    # plain.md (no tag) → stub Haiku → 02-Wiki, type learning, AI-added metadata.
    learn = _notes_of_type(config.vault_path, "02-Wiki", "learning")
    assert len(learn) == 1
    assert _fm(learn[0])["meta_origin"] == "ai"

    # Action items appended to a daily todo file (extra write, not the note body).
    todo_days = [p for p in (config.vault_path / "06-Todos").glob("*.md")
                 if p.stem.count("-") == 2 and "- [ ]" in p.read_text()]
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
