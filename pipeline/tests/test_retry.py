"""Retry policy (Pass 5): transient failures back off and retry before
quarantine; permanent ones quarantine immediately. All failures are simulated
transcribers — no network, no binaries; the backoff sleep is a recorder."""
from __future__ import annotations

import io
import urllib.error
from pathlib import Path

import pytest

from pipeline import watcher
from pipeline.config import Config
from pipeline.errors import StageError
from pipeline.events import EventLog
from pipeline.transcribe import OpenAITranscriber, Transcriber


@pytest.fixture
def env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    failed = tmp_path / "failed"
    for d in (vault, inbox, archive, failed):
        d.mkdir()
    # one audio capture waiting in the inbox
    (inbox / "2026-07-03-0900 memo.wav").write_bytes(b"RIFFfake")
    monkeypatch.setattr(watcher, "DB_PATH", tmp_path / "events.db")
    monkeypatch.setattr(watcher, "HEARTBEAT_PATH", tmp_path / ".watcher-heartbeat")
    config = Config(vault_path=vault, inbox_path=inbox, archive_path=archive, failed_path=failed)
    events = EventLog(tmp_path / "events.db", vault)
    return config, events, failed


class FlakyTranscriber(Transcriber):
    """Fails transiently N times, then succeeds."""

    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0

    def transcribe(self, audio_path: Path) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise StageError("Could not transcribe the recording.",
                             "The service couldn't be reached.",
                             "It will be retried automatically.", transient=True)
        return "#journal recovered on a later try"


class AlwaysFailing(Transcriber):
    def __init__(self, transient: bool):
        self.transient = transient
        self.calls = 0

    def transcribe(self, audio_path: Path) -> str:
        self.calls += 1
        raise StageError("Could not transcribe the recording.",
                         "It keeps failing.", "See the log.", transient=self.transient)


def _events_rows(events, status=None):
    where = "WHERE status = ?" if status else ""
    cur = events.conn.execute(
        f"SELECT stage, status, message, plain_english_error FROM events {where}",
        (status,) if status else ())
    return cur.fetchall()


def test_transient_retries_then_succeeds(env):
    config, events, failed = env
    transcriber = FlakyTranscriber(failures=2)
    slept = []
    deps = watcher.Deps(transcriber=transcriber, sleep=slept.append)
    results = watcher.run_once(config, events, deps)
    assert results[0].status != "failed"
    assert transcriber.calls == 3
    assert slept == [2, 4]  # exponential backoff between tries
    assert list(failed.iterdir()) == []  # nothing quarantined
    assert _events_rows(events, "failed") == []


def test_transient_exhausted_quarantines_with_attempts(env):
    config, events, failed = env
    transcriber = AlwaysFailing(transient=True)
    slept = []
    deps = watcher.Deps(transcriber=transcriber, sleep=slept.append)
    results = watcher.run_once(config, events, deps)
    assert results[0].status == "failed"
    assert transcriber.calls == 3          # 3 tries before giving up
    assert slept == [2, 4]
    assert len(list(failed.iterdir())) == 1  # then quarantined
    (row,) = _events_rows(events, "failed")
    assert "kind=transient attempts=3" in row[2]
    assert "tried 3 times" in row[3]       # the envelope says so in plain English


def test_permanent_quarantines_immediately(env):
    config, events, failed = env
    transcriber = AlwaysFailing(transient=False)
    slept = []
    deps = watcher.Deps(transcriber=transcriber, sleep=slept.append)
    results = watcher.run_once(config, events, deps)
    assert results[0].status == "failed"
    assert transcriber.calls == 1          # no retry
    assert slept == []                     # no backoff wait
    assert len(list(failed.iterdir())) == 1
    (row,) = _events_rows(events, "failed")
    assert "kind=permanent attempts=1" in row[2]
    assert "doesn't fix itself" in row[3]


def test_openai_error_classification(monkeypatch, tmp_path):
    """The OpenAI transcriber's failure taxonomy: 5xx/429/network → transient,
    other 4xx → permanent."""
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFFfake")
    t = OpenAITranscriber(api_key="sk-test")

    def raise_http(code):
        def _open(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, code, "boom", {}, io.BytesIO(b""))
        return _open

    import pipeline.transcribe as tr
    for code, transient in ((500, True), (429, True), (408, True), (400, False), (401, False)):
        monkeypatch.setattr(tr.urllib.request, "urlopen", raise_http(code))
        with pytest.raises(StageError) as exc:
            t.transcribe(audio)
        assert exc.value.transient is transient, f"HTTP {code}"

    def raise_url(req, timeout=0):
        raise urllib.error.URLError("no route to host")
    monkeypatch.setattr(tr.urllib.request, "urlopen", raise_url)
    with pytest.raises(StageError) as exc:
        t.transcribe(audio)
    assert exc.value.transient is True
