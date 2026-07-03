"""Watcher — CLI entry point and per-file stage orchestration.

Modes:
  (default)    process the inbox once, exit
  --loop       poll every 5 minutes
  --backlog    oldest-first in batches of 25, git-commit the vault before each
               batch, print a summary table and pause for review after each

One bad file never stops the run: any stage failure quarantines that file,
logs a plain-English event, pushes one ntfy, and the loop moves on.
"""
from __future__ import annotations

import argparse
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import archive, classify as classify_mod, config as config_mod, errors, extract, intake, route, todos
from .events import EventLog
from .transcribe import Transcriber, build_transcriber

POLL_SECONDS = 5 * 60
BATCH_SIZE = 25
DB_PATH = Path("events.db")
HEARTBEAT_PATH = Path(".watcher-heartbeat")


@dataclass
class Deps:
    """Injectable seams so the e2e test runs hermetically (no binaries, no API)."""
    transcriber: Transcriber
    classifier_fn: object = None      # llm_fn(transcript, config) -> dict; None = real Haiku
    extract_llm: object = None        # llm_fn(prompt, config) -> str; None = real Haiku


@dataclass
class Result:
    name: str
    type: str = "-"
    dest: str = "-"
    confidence: float = 0.0
    status: str = "ok"                # ok | needs_review | failed
    error: str = ""


def _transcribe(item, deps: Deps) -> str:
    if item.kind == "text":
        return item.path.read_text()
    return deps.transcriber.transcribe(item.path)


def process_file(item, config, events: EventLog, deps: Deps) -> Result:
    fkey = str(item.path)
    res = Result(name=item.path.name)
    try:
        # Stage 2 — transcribe (text skips inside _transcribe)
        t0 = time.monotonic()
        transcript = _transcribe(item, deps)
        events.log(fkey, "transcribe", "ok", int((time.monotonic() - t0) * 1000))

        # Stage 3 — classify
        t0 = time.monotonic()
        cls = classify_mod.classify(item, transcript, config, deps.classifier_fn)
        status = "needs_review" if cls.needs_review else "ok"
        events.log(fkey, "classify", status, int((time.monotonic() - t0) * 1000),
                   message=f"type={cls.type} confidence={cls.confidence:.2f} by={cls.routed_by}")

        # Stage 4 — route
        t0 = time.monotonic()
        paths = route.route(item, cls, transcript, config.vault_path)
        events.log(fkey, "route", "ok", int((time.monotonic() - t0) * 1000),
                   message=f"wrote {', '.join(p.name for p in paths)}")

        # Stage 5 — extract action items (append only)
        t0 = time.monotonic()
        note_id = item.captured.strftime("%Y%m%d%H%M%S")
        n = extract.extract(transcript, note_id, item.captured, config, llm_fn=deps.extract_llm)
        events.log(fkey, "extract", "ok", int((time.monotonic() - t0) * 1000),
                   message=f"{len(n)} action item(s)")

        # Stage 6 — archive the source
        t0 = time.monotonic()
        archive.archive(item.path, config.archive_path)
        events.log(fkey, "archive", "ok", int((time.monotonic() - t0) * 1000))

        res.type = cls.type
        res.dest = paths[0].parent.name
        res.confidence = cls.confidence
        res.status = status
        events.append_capture_log(
            f"{'⚠️ needs-review' if cls.needs_review else '✅'} {item.path.name} → "
            f"{cls.type} → {res.dest} (conf {cls.confidence:.2f})")
        return res

    except errors.StageError as e:
        return _fail(item, config, events, res, e.what, e.plain(), stage_msg=e.what)
    except Exception as e:  # unknown failure — still plain-English, still keep going
        what = "Something went wrong processing this file."
        plain = (f"What happened: {what}\nLikely cause: an unexpected error "
                 f"({type(e).__name__}).\nWhat to do: check events.db for detail, then re-run.")
        return _fail(item, config, events, res, what, plain, stage_msg=str(e))


def _fail(item, config, events, res: Result, what: str, plain: str, stage_msg: str) -> Result:
    fkey = str(item.path)
    # The source may already be quarantined if it moved; guard on existence.
    if item.path.exists():
        errors.quarantine(item.path, config.failed_path)
    events.log(fkey, "pipeline", "failed", message=what, plain_english_error=plain)
    errors.ntfy(config.ntfy_url, config.ntfy_topic, plain, title="Brain Cockpit — file failed")
    events.append_capture_log(f"❌ {item.path.name} — {what}")
    res.status, res.error = "failed", what
    return res


def _print_summary(results: list[Result]) -> None:
    print(f"\n{'file':<32} {'type':<12} {'destination':<16} {'conf':>5}  status")
    print("-" * 78)
    for r in results:
        print(f"{r.name[:32]:<32} {r.type:<12} {r.dest:<16} {r.confidence:>5.2f}  {r.status}")
    print()


def run_once(config, events: EventLog, deps: Deps) -> list[Result]:
    events.heartbeat(HEARTBEAT_PATH)
    items = intake.poll(config.inbox_path)
    results = [process_file(it, config, events, deps) for it in items]
    events.write_status(pending=len(intake.poll(config.inbox_path)))
    return results


def run_loop(config, events, deps) -> None:
    print(f"Watching {config.inbox_path} — polling every {POLL_SECONDS // 60} min. Ctrl-C to stop.")
    while True:
        results = run_once(config, events, deps)
        if results:
            print(f"Processed {len(results)} file(s).")
        todos.tick(config, events)   # reminders + optional digest, same process
        time.sleep(POLL_SECONDS)


def _git_commit_vault(vault_path: Path, n: int) -> None:
    try:
        inside = subprocess.run(["git", "-C", str(vault_path), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True)
        if inside.returncode != 0:
            print(f"  (vault is not a git repo — skipping pre-batch commit for batch {n})")
            return
        subprocess.run(["git", "-C", str(vault_path), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(vault_path), "commit", "-m", f"pre-backlog batch {n}",
                        "--allow-empty"], check=True, capture_output=True)
        print(f"  Committed vault before batch {n}.")
    except Exception as e:  # a git hiccup must not abort the backlog
        print(f"  (could not commit vault before batch {n}: {e})")


def run_backlog(config, events: EventLog, deps: Deps) -> None:
    events.heartbeat(HEARTBEAT_PATH)
    items = intake.poll(config.inbox_path)  # already oldest-first
    if not items:
        print("Inbox empty — nothing to backlog.")
        return
    batches = [items[i:i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]
    for n, batch in enumerate(batches, start=1):
        print(f"\n=== Batch {n}/{len(batches)} ({len(batch)} files) ===")
        _git_commit_vault(config.vault_path, n)  # commit BEFORE writes (revertible)
        results = [process_file(it, config, events, deps) for it in batch]
        events.write_status(pending=len(intake.poll(config.inbox_path)))
        _print_summary(results)
        if n < len(batches):
            input("Review the batch above, then press Enter to continue to the next batch... ")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="pipeline", description="Brain Cockpit capture watcher.")
    parser.add_argument("--config", default="config.json", help="path to config.json")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--loop", action="store_true", help="poll every 5 minutes")
    group.add_argument("--backlog", action="store_true", help="process oldest-first in gated batches of 25")
    args = parser.parse_args(argv)

    config = config_mod.load(args.config)
    events = EventLog(DB_PATH, config.vault_path)
    deps = Deps(transcriber=build_transcriber(config))
    try:
        if args.loop:
            run_loop(config, events, deps)
        elif args.backlog:
            run_backlog(config, events, deps)
        else:
            results = run_once(config, events, deps)
            print(f"Processed {len(results)} file(s). See _System/PIPELINE-STATUS.md.")
    finally:
        events.close()


if __name__ == "__main__":
    main()
