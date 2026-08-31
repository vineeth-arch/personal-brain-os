"""scripts/plaud_pull.py — the parts that don't require a live Plaud account.

pull_missing/pull_one are the real value here: given ANY backend implementing
the narrow PlaudBackend protocol, they must write bundle folders
pipeline.plaud.from_directory() recognises, dedupe by folder name, never
leave a half-written folder behind, and never let one bad recording or a
dead backend abort the run. The CLI/MCP backends themselves talk to a real
service this environment cannot reach — see the module's honesty note — so
only their pure parsing helpers are unit tested directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import plaud_pull as pp
from pipeline import plaud


# ---- a fake backend, for testing the orchestration in isolation -------------

@dataclass
class FakeBackend:
    recordings: list[pp.RemoteRecording] = field(default_factory=list)
    transcripts: dict[str, str] = field(default_factory=dict)
    summaries: dict[str, str] = field(default_factory=dict)
    audio: dict[str, bytes] = field(default_factory=dict)
    list_raises: bool = False
    raise_for_id: str | None = None

    def list_recordings(self, since: datetime) -> list[pp.RemoteRecording]:
        if self.list_raises:
            raise RuntimeError("backend is down")
        return [r for r in self.recordings if r.created >= since]

    def transcript_text(self, recording_id: str) -> str | None:
        if recording_id == self.raise_for_id:
            raise RuntimeError("boom")
        return self.transcripts.get(recording_id)

    def summary_text(self, recording_id: str) -> str | None:
        return self.summaries.get(recording_id)

    def download_audio(self, recording_id: str, dest_dir: Path) -> Path | None:
        data = self.audio.get(recording_id)
        if data is None:
            return None
        dest = dest_dir / "audio.mp3"
        dest.write_bytes(data)
        return dest


def _rec(id_: str, title: str, created: datetime) -> pp.RemoteRecording:
    return pp.RemoteRecording(id=id_, title=title, created=created)


# ---- folder naming ------------------------------------------------------------

def test_folder_name_matches_the_applaud_shape_plaud_py_reads():
    rec = _rec("74560101", "Product sync", datetime(2026, 4, 11))
    name = pp._folder_name(rec)
    assert name == "2026-04-11_Product_sync__74560101"
    # round-trips through the reader this whole thing exists to feed
    captured, title, pid = plaud.parse_folder_name(name)
    assert captured == datetime(2026, 4, 11)
    assert title == "Product sync"
    assert pid == "74560101"


def test_folder_name_falls_back_to_recording_when_title_is_blank():
    rec = _rec("1", "   ", datetime(2026, 1, 1))
    assert pp._folder_name(rec) == "2026-01-01_recording__1"


# ---- pull_one: one recording, atomically ------------------------------------

def test_pull_one_writes_a_bundle_from_directory_recognises(tmp_path):
    rec = _rec("1", "Weekly sync", datetime(2026, 4, 11))
    backend = FakeBackend(
        transcripts={"1": "[00:01] Ana: hi\n[00:02] Ben: hey"},
        summaries={"1": "Talked about the roadmap."},
        audio={"1": b"fake audio bytes"},
    )
    folder = pp.pull_one(backend, rec, tmp_path)
    assert folder == tmp_path / "2026-04-11_Weekly_sync__1"
    assert (folder / "audio.mp3").read_bytes() == b"fake audio bytes"
    assert "Ana: hi" in (folder / "transcript.txt").read_text(encoding="utf-8")
    assert (folder / "summary.md").read_text(encoding="utf-8") == "Talked about the roadmap."

    bundle = plaud.from_directory(folder, {".mp3"})
    assert bundle is not None and bundle.audio is not None and bundle.transcript is not None


def test_pull_one_with_only_a_transcript_still_writes_a_bundle(tmp_path):
    """Audio not synced yet — the transcript alone is still importable
    (mirrors pipeline.plaud.from_directory's transcript-only recognition)."""
    rec = _rec("2", "Quick note", datetime(2026, 4, 11))
    backend = FakeBackend(transcripts={"2": "just me talking"})
    folder = pp.pull_one(backend, rec, tmp_path)
    assert folder is not None
    assert not (folder / "audio.mp3").exists()
    assert (folder / "transcript.txt").exists()


def test_pull_one_with_nothing_usable_writes_no_folder(tmp_path):
    """Still processing on Plaud's side, or genuinely empty — must not leave
    a stray empty directory behind for from_directory to choke on."""
    rec = _rec("3", "Empty", datetime(2026, 4, 11))
    folder = pp.pull_one(FakeBackend(), rec, tmp_path)
    assert folder is None
    assert list(tmp_path.iterdir()) == []


def test_pull_one_never_leaves_a_staging_directory_behind_on_failure(tmp_path):
    class ExplodingBackend(FakeBackend):
        def download_audio(self, recording_id, dest_dir):
            raise RuntimeError("network died mid-download")
    rec = _rec("4", "Boom", datetime(2026, 4, 11))
    with pytest.raises(RuntimeError):
        pp.pull_one(ExplodingBackend(), rec, tmp_path)
    assert list(tmp_path.iterdir()) == []          # no ".pulling-..." left over


def test_pull_one_cleans_up_a_stale_staging_dir_from_a_previous_crash(tmp_path):
    rec = _rec("5", "Retry me", datetime(2026, 4, 11))
    stale = tmp_path / f".pulling-{pp._folder_name(rec)}"
    stale.mkdir(parents=True)
    (stale / "leftover.txt").write_text("junk", encoding="utf-8")
    backend = FakeBackend(transcripts={"5": "the real content"})
    folder = pp.pull_one(backend, rec, tmp_path)
    assert folder is not None
    assert not (folder / "leftover.txt").exists()
    assert "the real content" in (folder / "transcript.txt").read_text(encoding="utf-8")


# ---- pull_missing: the batch, dedupe, and fail-soft guarantees --------------

def test_pull_missing_skips_recordings_already_pulled(tmp_path):
    rec = _rec("1", "Already here", datetime(2026, 4, 11))
    (tmp_path / pp._folder_name(rec)).mkdir(parents=True)
    backend = FakeBackend(recordings=[rec], transcripts={"1": "would overwrite"})
    written = pp.pull_missing(backend, tmp_path, datetime(2026, 1, 1))
    assert written == []
    # and it truly wasn't touched
    assert list((tmp_path / pp._folder_name(rec)).iterdir()) == []


def test_pull_missing_only_pulls_recordings_on_or_after_since():
    backend = FakeBackend(recordings=[
        _rec("1", "old", datetime(2025, 1, 1)),
        _rec("2", "new", datetime(2026, 6, 1)),
    ], transcripts={"1": "x", "2": "y"})
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        written = pp.pull_missing(backend, Path(d), datetime(2026, 1, 1))
    assert len(written) == 1 and "new" in written[0].name


def test_pull_missing_survives_a_backend_that_cannot_list_at_all(tmp_path):
    backend = FakeBackend(list_raises=True)
    assert pp.pull_missing(backend, tmp_path, datetime(2026, 1, 1)) == []


def test_pull_missing_one_bad_recording_does_not_abort_the_batch(tmp_path):
    backend = FakeBackend(
        recordings=[_rec("bad", "explodes", datetime(2026, 4, 11)),
                   _rec("good", "fine", datetime(2026, 4, 12))],
        transcripts={"good": "text"},
        raise_for_id="bad",
    )
    written = pp.pull_missing(backend, tmp_path, datetime(2026, 1, 1))
    assert len(written) == 1 and "fine" in written[0].name


def test_pull_missing_dry_run_writes_nothing(tmp_path):
    backend = FakeBackend(recordings=[_rec("1", "would pull", datetime(2026, 4, 11))],
                          transcripts={"1": "text"})
    written = pp.pull_missing(backend, tmp_path, datetime(2026, 1, 1), dry_run=True)
    assert len(written) == 1
    assert list(tmp_path.iterdir()) == []           # dry run — nothing on disk


def test_pull_missing_recording_with_nothing_usable_is_silently_skipped(tmp_path):
    backend = FakeBackend(recordings=[_rec("1", "empty", datetime(2026, 4, 11))])
    assert pp.pull_missing(backend, tmp_path, datetime(2026, 1, 1)) == []
    assert list(tmp_path.iterdir()) == []


# ---- config overlay -----------------------------------------------------------

def test_overlay_keeps_defaults_for_unset_keys():
    cfg = pp._overlay(pp.CliConfig, {"binary": "custom-plaud"})
    assert cfg.binary == "custom-plaud"
    assert cfg.list_args == ["files", "--json"]       # untouched default


def test_overlay_ignores_unknown_keys_rather_than_raising():
    cfg = pp._overlay(pp.CliConfig, {"nonsense_field": "x"})
    assert cfg.binary == "plaud"


def test_overlay_with_no_data_is_pure_defaults():
    cfg = pp._overlay(pp.McpConfig, None)
    assert cfg.command == "npx"


def test_build_backend_selects_cli_by_default():
    backend = pp.build_backend({})
    assert isinstance(backend, pp.CliBackend)


def test_build_backend_selects_mcp_when_configured():
    backend = pp.build_backend({"plaud_cloud": {"backend": "mcp"}})
    assert isinstance(backend, pp.McpBackend)


def test_build_backend_threads_cli_config_through():
    backend = pp.build_backend({"plaud_cloud": {"backend": "cli", "cli": {"binary": "x"}}})
    assert backend.cfg.binary == "x"


# ---- timestamp parsing ---------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("2026-04-11T09:00:00Z", datetime(2026, 4, 11, 9, 0, 0)),
    ("2026-04-11T09:00:00.123Z", datetime(2026, 4, 11, 9, 0, 0, 123000)),
    ("2026-04-11", datetime(2026, 4, 11)),
    ("2026-04-11 09:00:00", datetime(2026, 4, 11, 9, 0, 0)),
])
def test_parse_timestamp_string_formats(raw, expected):
    assert pp._parse_timestamp(raw) == expected


def test_parse_timestamp_epoch_seconds_and_milliseconds():
    dt_s = pp._parse_timestamp(1_700_000_000)
    dt_ms = pp._parse_timestamp(1_700_000_000_000)
    assert dt_s == dt_ms          # same instant, two encodings
    assert dt_s is not None


def test_parse_timestamp_garbage_is_none():
    assert pp._parse_timestamp("not a date") is None
    assert pp._parse_timestamp(None) is None
    assert pp._parse_timestamp({}) is None
    assert pp._parse_timestamp(True) is None       # bool is an int subclass — must not misparse


# ---- CLI output parsing (pure functions, no subprocess needed) --------------

def test_text_from_cli_output_prefers_known_json_keys():
    assert pp._text_from_cli_output('{"text": "hello"}') == "hello"
    assert pp._text_from_cli_output('{"transcript": "hi"}') == "hi"


def test_text_from_cli_output_falls_back_to_raw_stdout():
    assert pp._text_from_cli_output("plain transcript text, not json") == \
        "plain transcript text, not json"


def test_text_from_cli_output_empty_is_none():
    assert pp._text_from_cli_output("   ") is None


def test_audio_url_from_cli_output_known_keys():
    assert pp._audio_url_from_cli_output('{"url": "https://x/a.mp3"}') == "https://x/a.mp3"
    assert pp._audio_url_from_cli_output('{"mp3_url": "https://x/b.mp3"}') == "https://x/b.mp3"


def test_audio_url_from_cli_output_bare_url_string():
    assert pp._audio_url_from_cli_output("https://x/a.mp3") == "https://x/a.mp3"


def test_audio_url_from_cli_output_no_url_found_is_none():
    assert pp._audio_url_from_cli_output('{"unrelated": true}') is None
    assert pp._audio_url_from_cli_output("not a url or json") is None
