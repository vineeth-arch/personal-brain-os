#!/usr/bin/env python3
"""scripts/plaud_pull.py — the cloud-pull lane (Pass PN): backfill recordings
that never reached a watched folder.

The folder lane (pipeline/ingest.py) covers applaud's local sync and Plaud
Desktop's export — both land files on disk, which ingest.sweep already
watches. This script covers the third case: a recording that only exists in
Plaud's cloud, pulled on demand. It writes into the EXACT SAME bundle layout
applaud uses — a folder named "YYYY-MM-DD_<title>__<id>" holding audio.<ext>,
transcript.txt and summary.md — so pipeline/plaud.from_directory() recognises
it without any changes, and the very next `ingest.sweep()` tick (or a manual
`python -m pipeline`) imports it exactly like a local applaud sync would. This
script itself never touches the vault, the classifier, or events.db — it only
ever writes bundle folders to `plaud_cloud.destination`.

Two backends behind one narrow interface (PlaudBackend), so a wrong guess
about Plaud's exact command/tool names is a CONFIG edit here, not a rewrite:

  "cli"  (default) shells out to the official @plaud-ai/cli
         (`npm i -g @plaud-ai/cli`, then `plaud login` once in this machine's
         shell). This is CLAUDE.md §7's one explicit dependency exception for
         this pass — it needs node — approved for this pass specifically.
  "mcp"  connects to Plaud's official MCP server over stdio, using the `mcp`
         package this repo already depends on for its OWN server
         (scripts/cockpit_mcp.py) — no new dependency at all.

HONESTY NOTE — read this before relying on either backend. docs.plaud.ai was
not reachable while this was written (this environment's egress proxy blocks
it), so the exact CLI flags and MCP tool names below are BEST-EFFORT, drawn
from search-result summaries and one unofficial third-party toolkit's source
— not verified against a live Plaud account. Every one of them is a
config.json value (plaud_cloud.cli.* / plaud_cloud.mcp.*), never a hardcoded
string, specifically so that correcting a wrong guess is editing config, not
this file. Both backends are written to fail soft and skip-and-log rather
than crash, and pull_missing() never deletes or overwrites anything — a wrong
guess costs you an empty pull and a log line, not a corrupted bundle.

Configuration (config.example.json has the full block, field for field —
JSON has no comments, so the annotations live here instead):

    "plaud_cloud": {
      "enabled": false,
      "backend": "cli",
      "destination": "",
      "since_days": 30,
      "cli": { "binary": "plaud", ... },
      "mcp": { "command": "npx", "args": ["-y", "@plaud-ai/mcp-server"], ... }
    }

Usage:
    python scripts/plaud_pull.py                  # backfill since_days
    python scripts/plaud_pull.py --since 2026-01-01
    python scripts/plaud_pull.py --dry-run         # list what would be pulled

Standalone and on-demand, like scripts/seed_people.py — the watcher loop
never runs this automatically, so a network hiccup here can never cost a
capture that's already on disk.
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline import config as config_mod  # noqa: E402

log = logging.getLogger("plaud_pull")

DEFAULT_SINCE_DAYS = 30
DEFAULT_AUDIO_TIMEOUT = 60


# ---- the narrow interface every backend implements --------------------------

@dataclass
class RemoteRecording:
    """One recording as a cloud backend describes it — just enough to build
    a local bundle folder in pipeline.plaud.from_directory's shape."""
    id: str
    title: str
    created: datetime


class PlaudBackend(Protocol):
    def list_recordings(self, since: datetime) -> list[RemoteRecording]: ...
    def transcript_text(self, recording_id: str) -> str | None: ...
    def summary_text(self, recording_id: str) -> str | None: ...
    def download_audio(self, recording_id: str, dest_dir: Path) -> Path | None:
        """Write `audio.<ext>` into dest_dir, return the path written, or
        None if there is no audio yet (still uploading, or never recorded)."""
        ...


def _overlay(cls, data: dict | None):
    """A dataclass instance with its own defaults, overridden by whatever
    keys `data` (a config.json sub-block) actually sets. Unknown keys in the
    config are ignored rather than raising — an older config.json against a
    newer script must still boot."""
    instance = cls()
    for key, value in (data or {}).items():
        if hasattr(instance, key):
            setattr(instance, key, value)
    return instance


def _parse_timestamp(value: object) -> datetime | None:
    """Best-effort: a backend's timestamp could plausibly be an ISO string, a
    bare date, or epoch seconds/milliseconds. Unparseable -> None, which the
    caller treats as "can't confirm this is recent enough — skip it" rather
    than guessing a date."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            seconds = value / 1000 if value > 10**11 else value
            return datetime.fromtimestamp(seconds)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                   "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value.strip(), fmt)
            except ValueError:
                continue
    return None


# ---- bundle folder writing (shared by every backend) -------------------------

def _folder_name(rec: RemoteRecording) -> str:
    """The exact applaud shape pipeline.plaud.parse_folder_name reads:
    'YYYY-MM-DD_<title>__<id>'."""
    title = "_".join(rec.title.split()) or "recording"
    return f"{rec.created:%Y-%m-%d}_{title}__{rec.id}"


def pull_one(backend: PlaudBackend, rec: RemoteRecording, destination: Path) -> Path | None:
    """Write one recording's bundle folder atomically (build it under a
    hidden staging name, rename into place — ingest.sweep must never see a
    half-written bundle). Returns the folder written, or None when there was
    nothing usable at all (no audio, no transcript — most likely a recording
    still uploading; try again on a later run)."""
    folder = destination / _folder_name(rec)
    tmp = destination / f".pulling-{folder.name}"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)   # a previous run's aborted attempt
    tmp.mkdir(parents=True)
    wrote_anything = False
    try:
        if backend.download_audio(rec.id, tmp) is not None:
            wrote_anything = True

        transcript = backend.transcript_text(rec.id)
        if transcript and transcript.strip():
            (tmp / "transcript.txt").write_text(transcript, encoding="utf-8")
            wrote_anything = True

        summary = backend.summary_text(rec.id)
        if summary and summary.strip():
            (tmp / "summary.md").write_text(summary, encoding="utf-8")

        if not wrote_anything:
            return None
        tmp.rename(folder)
        return folder
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def pull_missing(backend: PlaudBackend, destination: Path, since: datetime,
                 *, dry_run: bool = False) -> list[Path]:
    """Pull every recording since `since` that doesn't already have a bundle
    folder in `destination`. Never raises: a backend outage or one bad
    recording costs that recording, not the whole run — the same "one bad
    file never stops the batch" contract the rest of the pipeline keeps."""
    destination.mkdir(parents=True, exist_ok=True)
    existing = {p.name for p in destination.iterdir() if p.is_dir()}

    try:
        recordings = backend.list_recordings(since)
    except Exception:
        log.exception("could not list recordings from the Plaud backend")
        return []

    written: list[Path] = []
    for rec in recordings:
        name = _folder_name(rec)
        if name in existing:
            continue
        if dry_run:
            log.info("would pull: %s", name)
            written.append(destination / name)
            continue
        try:
            folder = pull_one(backend, rec, destination)
        except Exception:
            log.exception("failed to pull recording %s (%r) — will retry next run",
                          rec.id, rec.title)
            continue
        if folder is not None:
            log.info("pulled %s", folder.name)
            written.append(folder)
        else:
            log.info("nothing usable yet for %s (%r) — still processing?", rec.id, rec.title)
    return written


# ---- backend: the official @plaud-ai/cli, shelled out ------------------------

@dataclass
class CliConfig:
    binary: str = "plaud"
    list_args: list[str] = field(default_factory=lambda: ["files", "--json"])
    transcript_args: list[str] = field(default_factory=lambda: ["transcript", "{id}", "--json"])
    summary_args: list[str] = field(default_factory=lambda: ["summary", "{id}", "--json"])
    audio_url_args: list[str] = field(default_factory=lambda: ["files", "{id}", "--json"])
    timeout: int = 30


def _text_from_cli_output(raw: str) -> str | None:
    """Try JSON (a {"text": ...}-shaped response) first; if that fails,
    treat the raw stdout as already being the text — plausible for a CLI
    whose documented job is to "save transcripts to files"."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("text", "transcript", "summary", "content"):
            if isinstance(data.get(key), str):
                return data[key]
    return raw


def _audio_url_from_cli_output(raw: str) -> str | None:
    raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw if raw.startswith("http") else None
    if isinstance(data, dict):
        for key in ("url", "audio_url", "mp3_url", "download_url"):
            if isinstance(data.get(key), str):
                return data[key]
    return None


class CliBackend:
    """See the module docstring's honesty note: the subcommands below are
    the best available guess, not a verified integration, and every one of
    them is a CliConfig field (i.e. a config.json value)."""

    def __init__(self, cfg: CliConfig):
        self.cfg = cfg

    def _run(self, args: list[str], recording_id: str = "") -> str:
        resolved = [a.replace("{id}", recording_id) for a in args]
        try:
            proc = subprocess.run([self.cfg.binary, *resolved], capture_output=True,
                                  text=True, timeout=self.cfg.timeout)
        except FileNotFoundError:
            raise RuntimeError(
                f"the '{self.cfg.binary}' command isn't on PATH — "
                "npm i -g @plaud-ai/cli, then `plaud login` once") from None
        if proc.returncode != 0:
            raise RuntimeError(f"plaud CLI failed ({' '.join(resolved)}): "
                               f"{proc.stderr.strip()[:300]}")
        return proc.stdout

    def list_recordings(self, since: datetime) -> list[RemoteRecording]:
        raw = self._run(self.cfg.list_args)
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("plaud CLI list output wasn't JSON — got: %s", raw[:200])
            return []
        if isinstance(items, dict):
            for key in ("files", "recordings", "items", "data"):
                if isinstance(items.get(key), list):
                    items = items[key]
                    break
        if not isinstance(items, list):
            return []
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rec_id = str(item.get("id") or item.get("file_id") or "").strip()
            if not rec_id:
                continue
            created = _parse_timestamp(
                item.get("created_at") or item.get("createdAt") or item.get("start_time"))
            if created is None or created < since:
                continue
            title = str(item.get("title") or item.get("name") or rec_id)
            out.append(RemoteRecording(id=rec_id, title=title, created=created))
        return out

    def transcript_text(self, recording_id: str) -> str | None:
        try:
            return _text_from_cli_output(self._run(self.cfg.transcript_args, recording_id))
        except RuntimeError:
            log.exception("could not fetch a transcript for %s", recording_id)
            return None

    def summary_text(self, recording_id: str) -> str | None:
        try:
            return _text_from_cli_output(self._run(self.cfg.summary_args, recording_id))
        except RuntimeError:
            log.exception("could not fetch a summary for %s", recording_id)
            return None

    def download_audio(self, recording_id: str, dest_dir: Path) -> Path | None:
        try:
            url = _audio_url_from_cli_output(self._run(self.cfg.audio_url_args, recording_id))
        except RuntimeError:
            log.exception("could not get an audio URL for %s", recording_id)
            return None
        if not url:
            return None
        dest = dest_dir / "audio.mp3"
        try:
            with urllib.request.urlopen(url, timeout=DEFAULT_AUDIO_TIMEOUT) as resp:
                dest.write_bytes(resp.read())
        except OSError:
            log.exception("could not download audio for %s", recording_id)
            return None
        return dest


# ---- backend: Plaud's official MCP server, over stdio -------------------------

@dataclass
class McpConfig:
    command: str = "npx"
    args: list[str] = field(default_factory=lambda: ["-y", "@plaud-ai/mcp-server"])
    list_tool: str = "list_recordings"
    transcript_tool: str = "get_transcript"
    summary_tool: str = "get_summary"
    audio_url_tool: str = "get_audio_url"
    id_param: str = "recording_id"
    timeout: int = 30


class McpBackend:
    """Connects over stdio using the `mcp` package this repo already depends
    on for its OWN server (scripts/cockpit_mcp.py) — no new dependency
    (CLAUDE.md §7). Tool names are config (McpConfig), not hardcoded — see
    the module docstring's honesty note; the defaults here come from a
    third-party (unofficial) MCP server's published tool list, since the
    official one's exact names were not reachable to verify.

    Each call opens its own short-lived stdio connection rather than holding
    one open across a whole pull run — simpler, and one recording's failure
    can never wedge a session the rest of the run depends on. This script is
    an on-demand backfill, not a hot loop, so the extra process-launch cost
    per call is the right trade."""

    def __init__(self, cfg: McpConfig):
        self.cfg = cfg

    async def _call_async(self, tool: str, arguments: dict) -> object:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self.cfg.command, args=self.cfg.args)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments)
        if getattr(result, "is_error", False):
            detail = "; ".join(b.text for b in result.content if hasattr(b, "text"))
            raise RuntimeError(f"{tool} returned an error: {detail[:300]}")
        if getattr(result, "structured_content", None) is not None:
            return result.structured_content
        texts = [b.text for b in result.content if hasattr(b, "text")]
        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return joined

    def _call(self, tool: str, arguments: dict) -> object:
        import anyio
        return anyio.run(self._call_async, tool, arguments)

    def list_recordings(self, since: datetime) -> list[RemoteRecording]:
        try:
            data = self._call(self.cfg.list_tool, {})
        except Exception:
            log.exception("could not list recordings over MCP")
            return []
        items = data
        if isinstance(items, dict):
            for key in ("recordings", "files", "items", "data"):
                if isinstance(items.get(key), list):
                    items = items[key]
                    break
        if not isinstance(items, list):
            return []
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rec_id = str(item.get("id") or "").strip()
            if not rec_id:
                continue
            created = _parse_timestamp(item.get("created_at") or item.get("createdAt"))
            if created is None or created < since:
                continue
            title = str(item.get("title") or item.get("name") or rec_id)
            out.append(RemoteRecording(id=rec_id, title=title, created=created))
        return out

    def transcript_text(self, recording_id: str) -> str | None:
        try:
            data = self._call(self.cfg.transcript_tool, {self.cfg.id_param: recording_id})
        except Exception:
            log.exception("could not fetch a transcript for %s over MCP", recording_id)
            return None
        return _text_from_mcp_result(data)

    def summary_text(self, recording_id: str) -> str | None:
        try:
            data = self._call(self.cfg.summary_tool, {self.cfg.id_param: recording_id})
        except Exception:
            log.exception("could not fetch a summary for %s over MCP", recording_id)
            return None
        return _text_from_mcp_result(data)

    def download_audio(self, recording_id: str, dest_dir: Path) -> Path | None:
        try:
            data = self._call(self.cfg.audio_url_tool, {self.cfg.id_param: recording_id})
        except Exception:
            log.exception("could not get an audio URL for %s over MCP", recording_id)
            return None
        url = None
        if isinstance(data, str):
            url = data
        elif isinstance(data, dict):
            for key in ("url", "audio_url", "mp3_url"):
                if isinstance(data.get(key), str):
                    url = data[key]
                    break
        if not url:
            return None
        dest = dest_dir / "audio.mp3"
        try:
            with urllib.request.urlopen(url, timeout=DEFAULT_AUDIO_TIMEOUT) as resp:
                dest.write_bytes(resp.read())
        except OSError:
            log.exception("could not download audio for %s", recording_id)
            return None
        return dest


def _text_from_mcp_result(data: object) -> str | None:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("text", "transcript", "summary", "content"):
            if isinstance(data.get(key), str):
                return data[key]
    return None


# ---- wiring config.json to a backend -----------------------------------------

def build_backend(raw_config: dict) -> PlaudBackend:
    block = raw_config.get("plaud_cloud") or {}
    name = (block.get("backend") or "cli").strip().lower()
    if name == "mcp":
        return McpBackend(_overlay(McpConfig, block.get("mcp")))
    return CliBackend(_overlay(CliConfig, block.get("cli")))


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        prog="plaud_pull", description="Backfill Plaud recordings from the cloud.")
    parser.add_argument("--config", default="config.json", help="path to config.json")
    parser.add_argument("--since", help="YYYY-MM-DD — overrides plaud_cloud.since_days")
    parser.add_argument("--dry-run", action="store_true", help="list what would be pulled")
    args = parser.parse_args(argv)

    config = config_mod.load(args.config)
    block = config.raw.get("plaud_cloud") or {}
    if not block.get("enabled") and not args.dry_run:
        print("plaud_cloud.enabled is false in config.json — nothing to do.\n"
              "(Set it to true once plaud_cloud.destination and .backend are "
              "configured, or pass --dry-run to preview without changing that.)")
        return 0
    destination = block.get("destination")
    if not destination:
        print("plaud_cloud.destination isn't set in config.json — "
              "nowhere to write pulled recordings. See config.example.json.")
        return 1
    destination = Path(destination).expanduser()

    since = (datetime.strptime(args.since, "%Y-%m-%d") if args.since
            else datetime.now() - timedelta(days=int(block.get("since_days") or DEFAULT_SINCE_DAYS)))

    watch_folders = [(wf or {}).get("path") for wf in (config.raw.get("watch_folders") or [])]
    if str(destination) not in [str(Path(p).expanduser()) for p in watch_folders if p]:
        print(f"Note: {destination} isn't in config.json's watch_folders yet — "
              "pulled recordings will sit there until you add it there "
              '(source: "plaud") so the pipeline actually imports them.\n')

    backend = build_backend(config.raw)
    written = pull_missing(backend, destination, since, dry_run=args.dry_run)
    verb = "Would pull" if args.dry_run else "Pulled"
    print(f"{verb} {len(written)} recording(s) into {destination}")
    for path in written:
        print(f"  {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
