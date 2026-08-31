"""Plaud recording bundles — the device's own transcript, read instead of re-heard.

A Plaud Note Pro recording does not arrive as a lone audio file. Whether it
comes from Plaud Desktop's export or from applaud's local sync, it arrives as a
BUNDLE: the audio, plus a transcript the device already produced, plus (often) a
summary and a metadata blob. applaud's layout is a directory per recording:

    2026-04-11_My_meeting_title__74560101/
        audio.ogg
        transcript.txt      speaker-labelled, timestamped: "[00:01] Ana: ..."
        transcript.json     raw segments with speaker embeddings
        summary.md          the device's AI summary
        metadata.json       the full API response

Plaud Desktop instead exports siblings that share a filename stem
(`meeting.m4a` + `meeting.txt`), so both shapes are recognised here.

Why this module exists at all: re-transcribing a recording that already HAS a
transcript costs money, takes minutes, and is strictly worse than the file
sitting next to it — whisper returns one undifferentiated wall of text, while
the device returns "who said what". Reading the bundle is what retires the
speaker-diarization item DEFERRED.md parked as "needs a second model".

Everything here FAILS SOFT. A bundle we don't recognise, a transcript we can't
parse, a JSON shape that changed — each returns None or an empty result, and the
caller falls back to transcribing the audio exactly as before. A capture is
never lost to a parsing disappointment.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger("pipeline")

# What a bundle may hold. Audio extensions come from intake so the two lists
# cannot drift; .ogg is what applaud writes.
TRANSCRIPT_NAMES = ("transcript.txt", "transcript.srt")
TRANSCRIPT_JSON = "transcript.json"
SUMMARY_NAME = "summary.md"
METADATA_NAME = "metadata.json"
AUDIO_STEM = "audio"

# applaud names its folders "YYYY-MM-DD_<title>__<plaud id>"
_FOLDER_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})[_ ](?P<title>.*?)(?:__(?P<pid>\w+))?$")

# "[00:01] Ana Silva: hello"  /  "[01:02:03] Ana: hello"  /  "Ana: hello"
_LINE_RE = re.compile(
    r"^\s*(?:\[(?P<stamp>\d{1,2}:\d{2}(?::\d{2})?)\]\s*)?"
    r"(?P<speaker>[^:\[\]]{1,60}?)\s*:\s*(?P<text>.*)$")

# an SRT cue: index line, "00:00:01,000 --> 00:00:04,000", then text lines
_SRT_TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})[,.]\d{3}\s*-->")


@dataclass
class Transcript:
    """A parsed transcript: the body to keep, and who was heard in it."""
    body: str
    speakers: list[str] = field(default_factory=list)

    @property
    def is_conversation(self) -> bool:
        """Two or more voices makes it a conversation (SCHEMA-REFERENCE.md §7).
        One voice is a self voice memo and goes to the normal classifier."""
        return len(self.speakers) >= 2


@dataclass
class Bundle:
    """One recording and whatever the device produced alongside it."""
    audio: Path | None
    transcript: Path | None = None
    transcript_json: Path | None = None
    summary: Path | None = None
    metadata: Path | None = None
    title: str = ""
    captured: datetime | None = None
    plaud_id: str = ""

    @property
    def files(self) -> list[Path]:
        return [p for p in (self.audio, self.transcript, self.transcript_json,
                            self.summary, self.metadata) if p is not None]


# ---- finding a bundle -----------------------------------------------------

def _first(directory: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def parse_folder_name(name: str) -> tuple[datetime | None, str, str]:
    """('2026-04-11_Product_sync__74560101') -> (date, 'Product sync', '74560101').

    Unrecognised names give back (None, name, "") — the caller then falls back
    to the file's mtime, exactly as intake does for an unstamped recording.
    """
    m = _FOLDER_RE.match(name.strip())
    if not m:
        return None, name.strip(), ""
    try:
        captured = datetime.strptime(m["date"], "%Y-%m-%d")
    except ValueError:
        return None, name.strip(), ""
    title = (m["title"] or "").replace("_", " ").strip() or name.strip()
    return captured, title, m["pid"] or ""


def from_directory(directory: Path, audio_ext: set[str]) -> Bundle | None:
    """A bundle if this directory looks like one, else None.

    Deliberately loose: audio alone — no sidecars at all — still counts. This
    used to require at least one sidecar, which silently dropped a recording
    still being processed on Plaud's side (audio synced, transcript not written
    yet): ingest.sweep would see the folder, call this, get None, and move on —
    the capture never arrived, and there was nothing in the event log to say
    so. This function is only ever pointed at a watched folder the user
    configured for Plaud (ingest.folders), so "a directory with just an audio
    file in it" has one realistic reading here: import it, and let the caller
    fall back to whisper exactly as it would for any other lone recording.
    """
    try:
        if not directory.is_dir():
            return None
        entries = [p for p in directory.iterdir() if p.is_file()]
    except OSError:
        return None

    audio = next((p for p in entries
                  if p.stem.lower() == AUDIO_STEM and p.suffix.lower() in audio_ext), None)
    if audio is None:                    # any single audio file will do
        candidates = [p for p in entries if p.suffix.lower() in audio_ext]
        audio = candidates[0] if len(candidates) == 1 else None

    transcript = _first(directory, TRANSCRIPT_NAMES)
    transcript_json = _first(directory, (TRANSCRIPT_JSON,))
    summary = _first(directory, (SUMMARY_NAME,))
    metadata = _first(directory, (METADATA_NAME,))

    if audio is None and transcript is None and transcript_json is None:
        return None                      # nothing here we know how to import

    captured, title, plaud_id = parse_folder_name(directory.name)
    return Bundle(audio=audio, transcript=transcript, transcript_json=transcript_json,
                  summary=summary, metadata=metadata, title=title,
                  captured=captured, plaud_id=plaud_id)


def sidecars_for(audio: Path) -> Bundle:
    """Plaud Desktop's flat shape: siblings sharing a filename stem.

    `meeting.m4a` picks up `meeting.txt` / `meeting.srt` / `meeting.md` when
    they sit beside it. Always returns a Bundle (possibly with no sidecars at
    all), because the audio is importable either way.
    """
    stem, folder = audio.stem, audio.parent
    def sibling(*suffixes: str) -> Path | None:
        for suffix in suffixes:
            candidate = folder / f"{stem}{suffix}"
            if candidate.is_file():
                return candidate
        return None
    return Bundle(
        audio=audio,
        transcript=sibling(".txt", ".srt"),
        transcript_json=sibling(".json"),
        summary=sibling(".summary.md", ".md"),
        title=stem,
    )


# ---- reading the transcript ------------------------------------------------

def parse_text_transcript(text: str) -> Transcript:
    """Plaud's speaker-labelled plaintext. Body is kept VERBATIM (§8) — the
    only thing extracted is the cast list."""
    speakers: list[str] = []
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        speaker = m["speaker"].strip()
        # a bare "Note:" or "http" prefix is not a person
        if not speaker or speaker.lower().startswith(("http", "www.")):
            continue
        if speaker not in speakers:
            speakers.append(speaker)
    return Transcript(body=text.strip(), speakers=speakers)


def parse_srt(text: str) -> Transcript:
    """SRT is one of Plaud's export formats. Rendered into the same
    "[HH:MM] Speaker: line" shape the plaintext export already uses, so
    everything downstream sees one format."""
    lines_out: list[str] = []
    speakers: list[str] = []
    marker = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.isdigit():
            continue
        m = _SRT_TIME_RE.match(line)
        if m:
            h, mnt, sec = (int(g) for g in m.groups())
            total = h * 3600 + mnt * 60 + sec
            marker = f"[{total // 3600:02d}:{(total % 3600) // 60:02d}]"
            continue
        cue = _LINE_RE.match(line)
        if cue and cue["speaker"] and not cue["speaker"].lower().startswith(("http", "www.")):
            speaker = cue["speaker"].strip()
            if speaker not in speakers:
                speakers.append(speaker)
            lines_out.append(f"{marker} {speaker}: {cue['text']}".strip())
        else:
            lines_out.append(f"{marker} {line}".strip() if marker else line)
    return Transcript(body="\n".join(lines_out).strip(), speakers=speakers)


def parse_json_transcript(raw: str) -> Transcript | None:
    """applaud's transcript.json — raw Plaud segments with speaker embeddings.

    The exact shape is not publicly documented, so this reads defensively: any
    list of objects carrying something text-shaped and something speaker-shaped
    is accepted, under several plausible key names. An unrecognised shape gives
    None and the plaintext transcript (or the audio) is used instead.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    segments = data
    if isinstance(data, dict):
        for key in ("segments", "transcript", "transcription", "results", "data", "items"):
            if isinstance(data.get(key), list):
                segments = data[key]
                break
    if not isinstance(segments, list) or not segments:
        return None

    speakers: list[str] = []
    lines: list[str] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        text = next((str(seg[k]) for k in ("text", "content", "sentence", "transcript")
                     if seg.get(k)), "")
        if not text.strip():
            continue
        speaker = next((str(seg[k]) for k in
                        ("speaker", "speaker_name", "speakerName", "speaker_label", "speakerId")
                        if seg.get(k) not in (None, "")), "")
        start = next((seg[k] for k in ("start", "start_time", "startTime", "begin", "offset")
                      if isinstance(seg.get(k), (int, float))), None)
        marker = ""
        if start is not None:
            total = int(start / 1000) if start > 10_000 else int(start)   # ms or s
            marker = f"[{total // 3600:02d}:{(total % 3600) // 60:02d}]"
        if speaker and speaker not in speakers:
            speakers.append(speaker)
        lines.append(" ".join(p for p in (marker, f"{speaker}:" if speaker else "", text.strip()) if p))
    if not lines:
        return None
    return Transcript(body="\n".join(lines).strip(), speakers=speakers)


def read_transcript(bundle: Bundle) -> Transcript | None:
    """The best transcript this bundle has, or None to fall back to whisper.

    Plaintext first (it is what Plaud exports by default and what applaud
    flattens for the UI), then JSON, which is richer but shape-unstable.
    """
    for path, parse in ((bundle.transcript, None), (bundle.transcript_json, parse_json_transcript)):
        if path is None:
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            log.warning("plaud: could not read %s", path)
            continue
        if not raw.strip():
            continue
        if parse is not None:
            parsed = parse(raw)
            if parsed and parsed.body:
                return parsed
            continue
        parsed = parse_srt(raw) if path.suffix.lower() == ".srt" else parse_text_transcript(raw)
        if parsed.body:
            return parsed
    return None


def read_summary(bundle: Bundle) -> str:
    """The device's own AI summary, or "". Marked as AI at the note-building
    step — it is not the human's words (SCHEMA-REFERENCE.md §1)."""
    if bundle.summary is None:
        return ""
    try:
        return bundle.summary.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


# ---- inbox sidecars ----------------------------------------------------------
# How a bundle's transcript/summary travel with the audio once ingest.sweep has
# copied it into the inbox. The sidecar holds the ALREADY-PARSED plaintext body
# (the same "[HH:MM] Speaker: text" shape read_transcript always returns) —
# never the original SRT/JSON — so nothing downstream re-parses a shape-
# unstable format a second time.
#
# It travels as a DOTFILE. intake.poll's inbox scan explicitly skips names
# starting with "." (pipeline/intake.py poll()); without that, a copied
# transcript.txt would be picked up a second time as an unrelated text
# capture — one recording becoming two notes.

TRANSCRIPT_SIDECAR_SUFFIX = ".plaud-transcript.txt"
SUMMARY_SIDECAR_SUFFIX = ".plaud-summary.txt"


def sidecar_paths(audio_dest: Path) -> tuple[Path, Path]:
    """(transcript sidecar, summary sidecar) for an audio file at `audio_dest`
    — the paths, whether or not either exists yet."""
    folder, stem = audio_dest.parent, audio_dest.stem
    return (folder / f".{stem}{TRANSCRIPT_SIDECAR_SUFFIX}",
            folder / f".{stem}{SUMMARY_SIDECAR_SUFFIX}")


def read_inbox_sidecars(audio_path: Path) -> tuple[Transcript | None, str]:
    """(transcript, summary) ingest.sweep deposited beside `audio_path`, or
    (None, "") for an ordinary, non-Plaud recording — the common case.

    Speakers are RE-DERIVED from the sidecar body via parse_text_transcript
    rather than stored separately, so there is exactly one place that decides
    who was speaking and it can never drift from the text a human can open and
    read.
    """
    t_path, s_path = sidecar_paths(audio_path)
    transcript = None
    try:
        body = t_path.read_text(encoding="utf-8")
        if body.strip():
            transcript = parse_text_transcript(body)
    except (OSError, UnicodeDecodeError):
        pass
    summary = ""
    try:
        summary = s_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        pass
    return transcript, summary


# ---- matching speakers to the vault ----------------------------------------

def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def match_people(speakers: list[str], people: list) -> dict[str, str]:
    """{speaker label -> person id} for the speakers already in 07-People.

    Pure: takes people already loaded by relationships.load_people, so it is
    testable without a vault. Deliberately conservative — an exact normalised
    name, or an unambiguous first-name hit. Anything fuzzier would put words in
    a real person's mouth, and these are only SUGGESTIONS a human confirms.
    """
    matches: dict[str, str] = {}
    by_full: dict[str, list] = {}
    by_first: dict[str, list] = {}
    for person in people:
        full = _normalise(getattr(person, "name", "") or "")
        if not full:
            continue
        by_full.setdefault(full, []).append(person)
        by_first.setdefault(full.split()[0], []).append(person)

    for label in speakers:
        key = _normalise(label)
        if not key:
            continue
        hit = by_full.get(key) or ([] if " " in key else by_first.get(key, []))
        if len(hit) == 1:                       # never guess between two people
            matches[label] = hit[0].id
    return matches
