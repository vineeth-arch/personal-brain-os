"""One bad input must never take down a batch, a daemon, or a whole screen."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from api import notes as api_notes
from pipeline import errors, todos, watcher


class _Item:
    def __init__(self, path: Path):
        self.path = path
        self.kind = "text"
        self.captured = datetime(2026, 8, 30, 9, 0, 0)
        self.name = path.stem
        self.tag = None
        self.source = "manual"


class _Config:
    def __init__(self, tmp: Path):
        self.vault_path = tmp / "vault"
        self.inbox_path = tmp / "inbox"
        self.archive_path = tmp / "archive"
        self.failed_path = tmp / "failed"
        self.ntfy_url = self.ntfy_topic = ""
        self.raw = {}


class _Events:
    def __init__(self):
        self.rows = []

    def log(self, *a, **kw):
        self.rows.append((a, kw))

    def append_capture_log(self, line):
        pass

    def heartbeat(self, path):
        pass

    def write_status(self, pending):
        pass


# ---- _fail runs inside an except handler, so it may not raise ----------------

def test_fail_survives_a_quarantine_that_raises(tmp_path, monkeypatch):
    config = _Config(tmp_path)
    config.inbox_path.mkdir(parents=True)
    src = config.inbox_path / "a.md"
    src.write_text("x", encoding="utf-8")

    monkeypatch.setattr(errors, "quarantine",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("read-only fs")))
    events = _Events()
    res = watcher._fail(_Item(src), config, events, watcher.Result(name="a.md"),
                        "boom", "plain")
    assert res.status == "failed"
    # the failure is still recorded, and says the file stayed put
    message = events.rows[0][1]["message"]
    assert "quarantine failed" in message


def test_fail_survives_an_event_log_that_raises(tmp_path, monkeypatch):
    config = _Config(tmp_path)
    config.inbox_path.mkdir(parents=True)
    src = config.inbox_path / "a.md"
    src.write_text("x", encoding="utf-8")
    monkeypatch.setattr(errors, "quarantine", lambda *a, **kw: src)

    class Broken(_Events):
        def log(self, *a, **kw):
            raise RuntimeError("database is locked")

    res = watcher._fail(_Item(src), config, Broken(), watcher.Result(name="a.md"),
                        "boom", "plain")
    assert res.status == "failed"          # returned, not raised


def test_one_unquarantinable_file_does_not_stop_the_batch(tmp_path, monkeypatch):
    """The module's contract: one bad file never stops the run."""
    config = _Config(tmp_path)
    for d in (config.inbox_path, config.vault_path, config.archive_path):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(errors, "quarantine",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))

    def explode(*a, **kw):
        raise RuntimeError("stage blew up")

    monkeypatch.setattr(watcher, "_transcribe_with_retry", explode)
    items = [_Item(config.inbox_path / f"{n}.md") for n in ("a", "b", "c")]
    for it in items:
        it.path.write_text("x", encoding="utf-8")
    results = [watcher.process_file(it, config, _Events(), watcher.Deps(transcriber=None))
               for it in items]
    assert [r.status for r in results] == ["failed"] * 3   # all three, none aborted


# ---- the loop outlives a broken tick ----------------------------------------

def test_run_loop_survives_a_tick_that_raises(tmp_path, monkeypatch):
    """An unmounted inbox used to kill the watcher process outright."""
    calls = []

    def one_bad_tick(config, events, deps):
        calls.append(len(calls))
        if len(calls) == 1:
            raise FileNotFoundError("inbox is not mounted")
        raise KeyboardInterrupt          # end the loop on the second pass

    monkeypatch.setattr(watcher, "run_once", one_bad_tick)
    monkeypatch.setattr(watcher.ingest, "sweep", lambda *a, **kw: [])
    monkeypatch.setattr(watcher.time, "sleep", lambda s: None)
    with pytest.raises(KeyboardInterrupt):
        watcher.run_loop(_Config(tmp_path), _Events(), watcher.Deps(transcriber=None))
    assert len(calls) == 2, "the loop must poll again after a failed tick"


# ---- one unreadable note costs that note, not the screen --------------------

def test_unreadable_note_is_skipped_not_fatal(tmp_path):
    inbox = tmp_path / "00-Inbox"
    inbox.mkdir()
    (inbox / "good.md").write_text(
        "---\nid: 1\ntype: musing\nstatus: needs-review\n---\n\nbody\n", encoding="utf-8")
    (inbox / "bad.md").write_bytes(b"\xff\xfe\x00binary junk")

    items = api_notes.list_review(tmp_path, tmp_path / "nope.db")
    assert [i["id"] for i in items] == ["1"]
    # the Today badge must keep agreeing with the queue length
    assert api_notes.count_review(tmp_path) == len(items)


def test_unreadable_todo_file_is_skipped(tmp_path):
    folder = tmp_path / "06-Todos"
    folder.mkdir()
    (folder / "2026-08-30.md").write_text(
        "- [ ] real task 📅 2026-08-30 ^abc-1\n", encoding="utf-8")
    (folder / "2026-08-29.md").write_bytes(b"\xff\xfe binary")
    found = todos.scan(tmp_path)
    assert [t.task for t in found] == ["real task"]


def test_confidence_regex_rejects_a_malformed_token():
    assert api_notes._CONFIDENCE_RE.search("confidence=0.5.5 by=llm").group(1) == "0.5"
    assert float(api_notes._CONFIDENCE_RE.search("confidence=0.62 by=llm").group(1)) == 0.62
