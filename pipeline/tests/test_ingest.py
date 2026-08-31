"""Pass P — auto-ingest. Plaud Desktop, the Note Pro's export folder and Voice
Memos each own their own directory. The sweep copies new recordings into the
inbox and leaves the originals exactly where they were: those folders belong to
other apps, and the user may still want the file there."""
from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import ingest
from pipeline.events import EventLog


@pytest.fixture
def env(tmp_path):
    inbox = tmp_path / "inbox"
    watched = tmp_path / "PlaudDesktop"
    vault = tmp_path / "vault"
    for d in (inbox, watched, vault):
        d.mkdir()
    events = EventLog(tmp_path / "events.db", vault)
    return SimpleNamespace(tmp=tmp_path, inbox=inbox, watched=watched, events=events)


def cfg(env, source="plaud"):
    return SimpleNamespace(
        inbox_path=env.inbox,
        raw={"watch_folders": [{"path": str(env.watched), "source": source}]})


def _recording(folder: Path, name: str, age_seconds: int = 300) -> Path:
    p = folder / name
    p.write_bytes(b"fake audio bytes")
    old = time.time() - age_seconds
    os.utime(p, (old, old))
    return p


def test_a_new_recording_is_copied_once(env):
    src = _recording(env.watched, "meeting.m4a")

    copied = ingest.sweep(cfg(env), env.events)
    assert len(copied) == 1
    # plaud recordings land in the subfolder intake stamps as source: plaud
    assert copied[0].parent == env.inbox / "plaud"
    assert copied[0].read_bytes() == b"fake audio bytes"
    # the app's own folder is untouched — never moved, never deleted
    assert src.exists()

    # a second tick must not re-import the same recording
    assert ingest.sweep(cfg(env), env.events) == []
    assert len(list((env.inbox / "plaud").iterdir())) == 1


def test_non_plaud_sources_land_in_the_inbox_root(env):
    _recording(env.watched, "memo.m4a")
    copied = ingest.sweep(cfg(env, source="voice"), env.events)
    assert len(copied) == 1 and copied[0].parent == env.inbox


def test_copied_name_is_the_stamp_intake_parses(env):
    from pipeline import intake
    _recording(env.watched, "Client call.m4a")
    ingest.sweep(cfg(env), env.events)

    items = intake.poll(env.inbox)
    assert len(items) == 1
    assert items[0].kind == "audio" and items[0].source == "plaud"
    assert items[0].name == "Client call"


def test_files_still_being_written_are_left_for_the_next_tick(env):
    _recording(env.watched, "in-progress.m4a", age_seconds=1)
    assert ingest.sweep(cfg(env), env.events) == []
    # once it settles, it comes in
    _recording(env.watched, "in-progress.m4a", age_seconds=300)
    assert len(ingest.sweep(cfg(env), env.events)) == 1


def test_non_audio_and_hidden_files_are_ignored(env):
    _recording(env.watched, "notes.txt")
    _recording(env.watched, "cover.jpg")
    _recording(env.watched, ".DS_Store")
    assert ingest.sweep(cfg(env), env.events) == []


def test_an_edited_recording_is_re_imported(env):
    """Same path, new mtime+size = a different recording as far as we can tell;
    importing it again is the safe side of the trade."""
    _recording(env.watched, "take.m4a")
    assert len(ingest.sweep(cfg(env), env.events)) == 1
    p = env.watched / "take.m4a"
    p.write_bytes(b"a longer second take of the same file")
    old = time.time() - 300
    os.utime(p, (old, old))
    assert len(ingest.sweep(cfg(env), env.events)) == 1


def test_a_missing_folder_never_stops_the_tick(env):
    config = SimpleNamespace(
        inbox_path=env.inbox,
        raw={"watch_folders": [
            {"path": str(env.tmp / "not-mounted"), "source": "plaud"},
            {"path": str(env.watched), "source": "voice"}]})
    _recording(env.watched, "memo.m4a")
    # the unreachable drive is skipped; the working folder still imports
    assert len(ingest.sweep(config, env.events)) == 1


def test_malformed_entries_are_skipped(env):
    config = SimpleNamespace(inbox_path=env.inbox, raw={"watch_folders": [
        {"source": "plaud"},                                  # no path
        {"path": str(env.watched), "source": "nonsense"},     # not a schema source
    ]})
    _recording(env.watched, "memo.m4a")
    assert ingest.sweep(config, env.events) == []


def test_no_watch_folders_configured_is_a_quiet_no_op(env):
    assert ingest.sweep(SimpleNamespace(inbox_path=env.inbox, raw={}), env.events) == []


def test_recordings_nested_in_date_subfolders_are_found(env):
    """D19: Plaud Desktop (and others) nest exports by date — a shallow scan
    would leave them invisible forever."""
    nested = env.watched / "2026" / "08-20"
    nested.mkdir(parents=True)
    _recording(nested, "meeting.m4a")
    copied = ingest.sweep(cfg(env), env.events)
    assert len(copied) == 1 and copied[0].parent == env.inbox / "plaud"


def test_hidden_subfolders_are_never_descended_into(env):
    hidden = env.watched / ".Trash"
    hidden.mkdir()
    _recording(hidden, "deleted.m4a")
    assert ingest.sweep(cfg(env), env.events) == []
