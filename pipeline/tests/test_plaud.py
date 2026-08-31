"""pipeline/plaud.py — bundle recognition and transcript parsing.

Every parser here has one job above all: fail soft. A bundle we don't
recognise, a transcript we can't parse, a JSON shape that changed — each must
give back None/empty rather than raise, because the caller's fallback is
"transcribe the audio as before", and that fallback only works if this module
never explodes into the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from pipeline import plaud

AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".opus", ".ogg", ".oga", ".flac",
             ".webm", ".mp4"}


# ---- folder-name parsing ----------------------------------------------------

def test_parse_folder_name_applaud_shape():
    captured, title, pid = plaud.parse_folder_name("2026-04-11_My_meeting_title__74560101")
    assert captured == datetime(2026, 4, 11)
    assert title == "My meeting title"
    assert pid == "74560101"


def test_parse_folder_name_without_a_plaud_id():
    captured, title, pid = plaud.parse_folder_name("2026-04-11_Quick_chat")
    assert captured == datetime(2026, 4, 11)
    assert title == "Quick chat"
    assert pid == ""


def test_parse_folder_name_unrecognised_falls_back_to_the_name_itself():
    captured, title, pid = plaud.parse_folder_name("not-a-date-prefixed-folder")
    assert captured is None
    assert title == "not-a-date-prefixed-folder"
    assert pid == ""


# ---- bundle detection: applaud's directory shape ----------------------------

def _write_applaud_bundle(root, name="2026-04-11_Product_sync__74560101",
                          audio=True, transcript="[00:01] Ana: hi\n[00:04] Ben: hey",
                          transcript_json=None, summary=None, metadata=None):
    folder = root / name
    folder.mkdir()
    if audio:
        (folder / "audio.ogg").write_bytes(b"\x00\x01")
    if transcript is not None:
        (folder / "transcript.txt").write_text(transcript, encoding="utf-8")
    if transcript_json is not None:
        (folder / "transcript.json").write_text(transcript_json, encoding="utf-8")
    if summary is not None:
        (folder / "summary.md").write_text(summary, encoding="utf-8")
    if metadata is not None:
        (folder / "metadata.json").write_text(metadata, encoding="utf-8")
    return folder


def test_from_directory_recognises_a_full_applaud_bundle(tmp_path):
    folder = _write_applaud_bundle(tmp_path, summary="Talked about the roadmap.",
                                   metadata='{"id": "74560101"}')
    bundle = plaud.from_directory(folder, AUDIO_EXT)
    assert bundle is not None
    assert bundle.audio.name == "audio.ogg"
    assert bundle.transcript.name == "transcript.txt"
    assert bundle.summary.name == "summary.md"
    assert bundle.metadata.name == "metadata.json"
    assert bundle.title == "Product sync"
    assert bundle.captured == datetime(2026, 4, 11)
    assert bundle.plaud_id == "74560101"
    assert set(bundle.files) == {bundle.audio, bundle.transcript, bundle.summary, bundle.metadata}


def test_from_directory_recognises_a_transcript_only_bundle(tmp_path):
    """Not every recording has finished transcoding to audio.ogg yet, or the
    audio may never sync — the transcript alone is still importable."""
    folder = _write_applaud_bundle(tmp_path, audio=False)
    bundle = plaud.from_directory(folder, AUDIO_EXT)
    assert bundle is not None
    assert bundle.audio is None
    assert bundle.transcript is not None


def test_from_directory_rejects_a_plain_audio_only_folder(tmp_path):
    """Audio with nothing alongside it is not a Plaud bundle — ingest.sweep's
    ordinary file-copy path should see this folder as unrecognised, not steal
    it from whatever else handles bare audio drops."""
    folder = tmp_path / "2026-04-11_random"
    folder.mkdir()
    (folder / "audio.ogg").write_bytes(b"\x00")
    assert plaud.from_directory(folder, AUDIO_EXT) is None


def test_from_directory_rejects_an_empty_or_unrelated_folder(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert plaud.from_directory(empty, AUDIO_EXT) is None

    other = tmp_path / "not-plaud"
    other.mkdir()
    (other / "readme.md").write_text("hello", encoding="utf-8")
    assert plaud.from_directory(other, AUDIO_EXT) is None


def test_from_directory_on_a_file_not_a_folder_is_none(tmp_path):
    f = tmp_path / "plain.txt"
    f.write_text("x", encoding="utf-8")
    assert plaud.from_directory(f, AUDIO_EXT) is None


def test_from_directory_unreadable_folder_fails_soft(tmp_path, monkeypatch):
    folder = _write_applaud_bundle(tmp_path)

    class Boom:
        def is_dir(self): return True
        def iterdir(self): raise OSError("permission denied")

    assert plaud.from_directory(Boom(), AUDIO_EXT) is None


def test_from_directory_picks_the_single_odd_named_audio_file(tmp_path):
    """applaud always names it audio.<ext>, but a hand-placed bundle might not."""
    folder = tmp_path / "2026-04-11_manual_drop"
    folder.mkdir()
    (folder / "my-recording.m4a").write_bytes(b"\x00")
    (folder / "transcript.txt").write_text("Ana: hi", encoding="utf-8")
    bundle = plaud.from_directory(folder, AUDIO_EXT)
    assert bundle is not None and bundle.audio.name == "my-recording.m4a"


# ---- Plaud Desktop's flat sidecar shape -------------------------------------

def test_sidecars_for_finds_a_matching_transcript(tmp_path):
    audio = tmp_path / "meeting.m4a"
    audio.write_bytes(b"\x00")
    (tmp_path / "meeting.txt").write_text("Ana: hello", encoding="utf-8")
    bundle = plaud.sidecars_for(audio)
    assert bundle.audio == audio
    assert bundle.transcript.name == "meeting.txt"
    assert bundle.title == "meeting"


def test_sidecars_for_prefers_txt_over_srt(tmp_path):
    audio = tmp_path / "meeting.m4a"
    audio.write_bytes(b"\x00")
    (tmp_path / "meeting.txt").write_text("plaintext version", encoding="utf-8")
    (tmp_path / "meeting.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nsrt version",
                                          encoding="utf-8")
    bundle = plaud.sidecars_for(audio)
    assert bundle.transcript.suffix == ".txt"


def test_sidecars_for_with_nothing_alongside_is_still_a_bundle(tmp_path):
    """Audio with no sidecars must not error — it just has nothing extra;
    the watcher falls back to whisper on this exact shape."""
    audio = tmp_path / "lone.m4a"
    audio.write_bytes(b"\x00")
    bundle = plaud.sidecars_for(audio)
    assert bundle.audio == audio
    assert bundle.transcript is None
    assert bundle.summary is None


# ---- plaintext transcript parsing -------------------------------------------

def test_parse_text_transcript_extracts_speakers_in_order_of_first_appearance():
    text = "[00:01] Ana Silva: hello everyone\n[00:04] Ben: hi Ana\n[00:09] Ana Silva: how's it going"
    t = plaud.parse_text_transcript(text)
    assert t.speakers == ["Ana Silva", "Ben"]
    assert t.is_conversation
    assert t.body == text.strip()          # kept VERBATIM (§8)


def test_parse_text_transcript_one_speaker_is_not_a_conversation():
    t = plaud.parse_text_transcript("[00:01] Me: talking to myself\n[00:05] Me: still am")
    assert t.speakers == ["Me"]
    assert not t.is_conversation


def test_parse_text_transcript_without_timestamps():
    t = plaud.parse_text_transcript("Ana: hi\nBen: hey there")
    assert t.speakers == ["Ana", "Ben"]


def test_parse_text_transcript_ignores_url_like_and_untagged_lines():
    text = "Ana: check http://example.com: it has a colon\njust a plain line\nBen: ok"
    t = plaud.parse_text_transcript(text)
    assert "http" not in [s.lower()[:4] for s in t.speakers]
    assert "Ben" in t.speakers


def test_parse_text_transcript_of_empty_text_is_empty_not_an_error():
    t = plaud.parse_text_transcript("")
    assert t.body == "" and t.speakers == []


# ---- SRT parsing -------------------------------------------------------------

SRT = """1
00:00:01,000 --> 00:00:04,000
Ana: Let's get started.

2
00:01:05,000 --> 00:01:08,000
Ben: Sounds good.
"""


def test_parse_srt_extracts_speakers_and_markers():
    t = plaud.parse_srt(SRT)
    assert t.speakers == ["Ana", "Ben"]
    assert "[00:00] Ana: Let's get started." in t.body
    assert "[00:01] Ben: Sounds good." in t.body


def test_parse_srt_without_speaker_labels_still_produces_a_body():
    """A plain SRT export (no diarization) must not crash — it just has no
    speakers, and the caller treats it like any single-voice recording."""
    text = "1\n00:00:01,000 --> 00:00:03,000\nJust some narration.\n"
    t = plaud.parse_srt(text)
    assert t.speakers == []
    assert "Just some narration." in t.body


def test_parse_srt_garbage_input_does_not_raise():
    t = plaud.parse_srt("not an srt file at all\njust noise")
    assert isinstance(t.body, str)


# ---- JSON transcript parsing -------------------------------------------------

def test_parse_json_transcript_top_level_list():
    raw = '[{"speaker": "Ana", "text": "hi", "start": 1000}, {"speaker": "Ben", "text": "hey", "start": 4000}]'
    t = plaud.parse_json_transcript(raw)
    assert t is not None
    assert t.speakers == ["Ana", "Ben"]
    assert "hi" in t.body and "hey" in t.body


@pytest.mark.parametrize("key", ["segments", "transcript", "transcription", "results", "data", "items"])
def test_parse_json_transcript_nested_under_plausible_keys(key):
    raw = f'{{"{key}": [{{"speaker_name": "Ana", "content": "hello"}}]}}'
    t = plaud.parse_json_transcript(raw)
    assert t is not None and t.speakers == ["Ana"]


def test_parse_json_transcript_seconds_vs_milliseconds_start():
    # a start under 10_000 is treated as seconds, at/above as milliseconds
    raw = '[{"speaker": "A", "text": "early", "start": 5}, {"speaker": "A", "text": "later", "start": 65000}]'
    t = plaud.parse_json_transcript(raw)
    assert "[00:00]" in t.body           # 5 seconds
    assert "[00:01]" in t.body           # 65000ms = 65s = 1 minute


def test_parse_json_transcript_invalid_json_returns_none():
    assert plaud.parse_json_transcript("not json {") is None


@pytest.mark.parametrize("raw", ["{}", "[]", "null", "42", '{"unrelated": "shape"}',
                                 '[{"no_text_or_content_key": true}]'])
def test_parse_json_transcript_unrecognised_shapes_return_none(raw):
    assert plaud.parse_json_transcript(raw) is None


def test_parse_json_transcript_skips_segments_with_no_text():
    raw = '[{"speaker": "Ana"}, {"speaker": "Ben", "text": "actual line"}]'
    t = plaud.parse_json_transcript(raw)
    assert t is not None
    assert t.speakers == ["Ben"]


# ---- read_transcript: preference order + fail-soft --------------------------

def test_read_transcript_prefers_plaintext_over_json(tmp_path):
    folder = _write_applaud_bundle(
        tmp_path, transcript="Ana: from plaintext",
        transcript_json='[{"speaker": "Ben", "text": "from json"}]')
    bundle = plaud.from_directory(folder, AUDIO_EXT)
    t = plaud.read_transcript(bundle)
    assert t is not None and "from plaintext" in t.body


def test_read_transcript_falls_back_to_json_when_plaintext_is_empty(tmp_path):
    folder = _write_applaud_bundle(
        tmp_path, transcript="   ",
        transcript_json='[{"speaker": "Ben", "text": "from json"}]')
    bundle = plaud.from_directory(folder, AUDIO_EXT)
    t = plaud.read_transcript(bundle)
    assert t is not None and "from json" in t.body


def test_read_transcript_no_sidecars_at_all_returns_none(tmp_path):
    """This is THE fallback-to-whisper trigger: no transcript anywhere means
    the caller must transcribe the audio itself."""
    audio = tmp_path / "lone.m4a"
    audio.write_bytes(b"\x00")
    bundle = plaud.sidecars_for(audio)
    assert plaud.read_transcript(bundle) is None


def test_read_transcript_unparseable_json_and_no_plaintext_returns_none(tmp_path):
    folder = _write_applaud_bundle(tmp_path, transcript=None,
                                   transcript_json="{not valid json")
    bundle = plaud.from_directory(folder, AUDIO_EXT)
    assert plaud.read_transcript(bundle) is None


def test_read_transcript_unreadable_file_fails_soft(tmp_path):
    folder = _write_applaud_bundle(tmp_path, transcript="Ana: hi")
    bundle = plaud.from_directory(folder, AUDIO_EXT)
    bundle.transcript.unlink()
    bundle.transcript.mkdir()          # now a directory, not a file — read_text raises
    assert plaud.read_transcript(bundle) is None


def test_read_transcript_picks_srt_by_extension(tmp_path):
    folder = tmp_path / "2026-04-11_x"
    folder.mkdir()
    (folder / "audio.ogg").write_bytes(b"\x00")
    (folder / "transcript.srt").write_text(SRT, encoding="utf-8")
    bundle = plaud.from_directory(folder, AUDIO_EXT)
    t = plaud.read_transcript(bundle)
    assert t is not None and t.speakers == ["Ana", "Ben"]


# ---- summary ------------------------------------------------------------------

def test_read_summary_present(tmp_path):
    folder = _write_applaud_bundle(tmp_path, summary="The team agreed on the launch date.")
    bundle = plaud.from_directory(folder, AUDIO_EXT)
    assert plaud.read_summary(bundle) == "The team agreed on the launch date."


def test_read_summary_absent_is_empty_string(tmp_path):
    folder = _write_applaud_bundle(tmp_path)
    bundle = plaud.from_directory(folder, AUDIO_EXT)
    assert plaud.read_summary(bundle) == ""


def test_read_summary_unreadable_fails_soft(tmp_path):
    folder = _write_applaud_bundle(tmp_path, summary="x")
    bundle = plaud.from_directory(folder, AUDIO_EXT)
    bundle.summary.unlink()
    bundle.summary.mkdir()
    assert plaud.read_summary(bundle) == ""


# ---- matching speakers against the vault -------------------------------------

@dataclass
class _Person:
    id: str
    name: str


def test_match_people_exact_full_name():
    people = [_Person("1", "Ana Silva"), _Person("2", "Ben Carter")]
    matches = plaud.match_people(["Ana Silva", "Ben Carter"], people)
    assert matches == {"Ana Silva": "1", "Ben Carter": "2"}


def test_match_people_unambiguous_first_name():
    people = [_Person("1", "Ana Silva")]
    matches = plaud.match_people(["Ana"], people)
    assert matches == {"Ana": "1"}


def test_match_people_never_guesses_between_two_people_sharing_a_first_name():
    people = [_Person("1", "Ana Silva"), _Person("2", "Ana Costa")]
    matches = plaud.match_people(["Ana"], people)
    assert matches == {}, "an ambiguous first name must never be linked to either person"


def test_match_people_no_match_is_simply_absent():
    people = [_Person("1", "Ana Silva")]
    matches = plaud.match_people(["Someone Else"], people)
    assert matches == {}


def test_match_people_is_case_and_punctuation_insensitive():
    people = [_Person("1", "Ana Silva")]
    matches = plaud.match_people(["  ANA   silva  "], people)
    assert matches == {"  ANA   silva  ": "1"}


def test_match_people_ignores_speakers_and_people_with_no_name():
    people = [_Person("1", ""), _Person("2", "Ben")]
    matches = plaud.match_people(["", "Ben"], people)
    assert matches == {"Ben": "2"}


def test_match_people_handles_an_empty_people_list():
    assert plaud.match_people(["Anyone"], []) == {}
