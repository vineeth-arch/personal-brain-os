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
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import (archive, classify as classify_mod, config as config_mod, echo as echo_mod,
               enrich, errors, extract, ingest, intake, plaud, related, relationships, route,
               todos, transliterate, vaultsync, vision as vision_mod)
from .events import EventLog
from . import transcribe as transcribe_mod
from .transcribe import Transcriber, build_transcriber

log = logging.getLogger("pipeline")

POLL_SECONDS = 5 * 60
BATCH_SIZE = 25

# Where the pipeline's own state lives. This MUST resolve to the same directory
# the API uses (api/main.py's state root), or the API reads one events.db while
# the watcher writes another — which is precisely what the container did:
# BRAIN_COCKPIT_ROOT=/data for the API, WORKDIR=/app for the watcher, so the
# cockpit showed "the pipeline has never checked in" forever and the ingest
# de-dupe table was thrown away on every restart.
#
# Unset (the launchd path), both fall back to the CWD — the plists set
# WorkingDirectory to the repo for the API and the watcher alike, so they have
# always agreed there.
DB_NAME = "events.db"
HEARTBEAT_NAME = ".watcher-heartbeat"


def state_root() -> Path:
    return Path(os.environ.get("BRAIN_COCKPIT_ROOT") or ".")


DB_PATH = state_root() / DB_NAME
HEARTBEAT_PATH = state_root() / HEARTBEAT_NAME

RETRY_ATTEMPTS = 3          # total tries for a transient failure before quarantine
RETRY_BASE_SECONDS = 2      # backoff: 2s, then 4s, between tries


@dataclass
class Deps:
    """Injectable seams so the e2e test runs hermetically (no binaries, no API)."""
    transcriber: Transcriber
    classifier_fn: object = None      # llm_fn(transcript, config) -> dict; None = real Haiku
    extract_llm: object = None        # llm_fn(prompt, config) -> str; None = real Haiku
    enrich_fetch: object = None       # fetch(url, data=, timeout=) -> bytes; None = real HTTP
    enrich_router: object = None      # router(prompt, config, validate) -> (data, provider, attempts)
    transliterate_fn: object = None   # caller(text, block) -> str; None = the configured engine
    vision_caller: object = None      # caller(image_path, mime, key) -> raw text; None = real Claude
    sleep: object = time.sleep        # retry backoff seam (tests inject a recorder)


@dataclass
class Result:
    name: str
    type: str = "-"
    dest: str = "-"
    confidence: float = 0.0
    status: str = "ok"                # ok | needs_review | failed
    error: str = ""


def _transcribe(item, deps: Deps, events: EventLog | None = None,
                duration: float | None = None) -> str:
    """Text passes through; an image has no transcript at all — the watcher
    reads it directly via vision, never as text (Pass V2/V3); audio goes to
    the engine — whole for a normal recording, in stitched 10-minute segments
    once it is long enough that one request would be refused or crawl (Pass P).
    `duration` is probed once by the caller and threaded through (a retry
    re-enters this function without re-probing)."""
    if item.kind == "image":
        return ""
    if item.kind in ("text", "link"):
        return item.path.read_text(encoding="utf-8")
    if transcribe_mod.is_long(item.path, duration):
        def on_event(message, ok):
            if events:
                events.log(str(item.path), "transcribe", "ok" if ok else "failed", message=message)
        return transcribe_mod.transcribe_long(
            item.path, deps.transcriber, sleep=deps.sleep, on_event=on_event,
            attempts=RETRY_ATTEMPTS, backoff_base=RETRY_BASE_SECONDS)
    return deps.transcriber.transcribe(item.path)


def _transcribe_with_retry(item, deps: Deps, events: EventLog | None = None,
                           duration: float | None = None) -> str:
    """Retry policy: transient failures (network, 5xx, rate limits) get
    RETRY_ATTEMPTS tries with exponential backoff BEFORE quarantine; permanent
    ones (bad audio, missing binary, bad key) escape on the first try.
    Transcription runs before any vault write, so retrying it is side-effect
    free — which is exactly why the retry lives here and not around the whole
    file."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return _transcribe(item, deps, events, duration)
        except errors.StageError as e:
            e.attempts = attempt
            if not e.transient or attempt == RETRY_ATTEMPTS:
                raise
            deps.sleep(RETRY_BASE_SECONDS * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


def process_file(item, config, events: EventLog, deps: Deps) -> Result:
    fkey = str(item.path)
    res = Result(name=item.path.name)
    try:
        if item.kind == "image-unsupported":
            # A raw HEIC/HEIF that bypassed the Shortcut/PWA (e.g. Syncthing
            # straight from an iPhone camera roll). This server never
            # attempts a decode (CLAUDE.md §7 — no Pillow, no libheif), and
            # the point of a distinct kind (vs. silently ignoring it, like an
            # unrecognized extension) is that the capture is never just lost:
            # it's quarantined with a next step the owner can actually take.
            raise errors.StageError(
                "This photo can't be processed.",
                f"{item.path.suffix.upper().lstrip('.')} (HEIC/HEIF) isn't a format this "
                "server reads — it never attempts to decode one.",
                "Convert it to JPEG on your phone and share it again — the iOS Shortcut and "
                "the cockpit's own photo button both do this automatically.")

        # Probed once up front (chunk routing AND duration_min both need it —
        # one ffprobe subprocess per file, not two).
        duration = (transcribe_mod.probe_duration_seconds(item.path)
                   if item.kind == "audio" else None)

        # Stage 2 — transcribe (text skips inside _transcribe); transient
        # failures are retried with backoff before they can reach quarantine.
        #
        # A Plaud sidecar transcript (ingest.sweep deposits it beside the
        # audio) short-circuits the engine entirely — no whisper, no OpenAI
        # call, no chunking. It carries speaker labels a re-transcription
        # would throw away, so it also decides Stage 3 below. No sidecar, or
        # an empty one, falls straight through to the ordinary path: a
        # recording still processing on Plaud's side still yields a note.
        t0 = time.monotonic()
        plaud_transcript = (plaud.read_inbox_sidecars(item.path)[0]
                            if item.kind == "audio" else None)
        if plaud_transcript is not None and plaud_transcript.body:
            transcript = plaud_transcript.body
            events.log(fkey, "transcribe", "ok", int((time.monotonic() - t0) * 1000),
                       message="source=plaud speakers=" + ",".join(plaud_transcript.speakers))
        else:
            transcript = _transcribe_with_retry(item, deps, events, duration)
            events.log(fkey, "transcribe", "ok", int((time.monotonic() - t0) * 1000))

        # Stage 2b — transliterate. Hindi speech comes back in Devanagari; the
        # note leads with Roman Hindi/Hinglish and keeps the original below it.
        # Everything downstream (classify, todos) reads the Hinglish text.
        duration_min = max(1, round(duration / 60)) if duration else None
        body = transcript
        if item.kind == "audio":
            if transliterate.has_devanagari(transcript):
                t0 = time.monotonic()
                hinglish = transliterate.to_hinglish(transcript, config,
                                                     caller=deps.transliterate_fn)
                if hinglish:
                    body = transliterate.compose_body(hinglish, transcript)
                    transcript = hinglish
                    events.log(fkey, "transliterate", "ok",
                               int((time.monotonic() - t0) * 1000),
                               message="devanagari → hinglish")
                else:
                    # never a lost capture: the note keeps the Devanagari body
                    events.log(fkey, "transliterate", "failed",
                               int((time.monotonic() - t0) * 1000),
                               message="no transliteration engine answered — "
                                       "note kept in Devanagari")

        # Hoisted here (rather than computed only where Stage 5 needs it)
        # because Stage 4b below (classify/route path only) also needs it, and
        # this way every kind computes it exactly once — same value either way,
        # since it depends only on item.captured.
        note_id = item.captured.strftime("%Y%m%d%H%M%S")

        # D13: a capture tag wins over automatic link-detection. Without this,
        # "#journal ... here's the article https://..." was silently pulled
        # off the journal and filed as an untitled resource — the tag the
        # user spoke or typed was thrown away. Only an ABSENT tag, or an
        # explicit #resource, lets a URL fall through to the link branch; any
        # other tag flows through the normal classify/route path below with
        # the URL left intact in the body.
        free_tag = classify_mod.free_route_tag(item, transcript)
        if item.kind == "link" and (free_tag is None or free_tag == "resource"):
            # A link IS a resource — no classify LLM, no review gate. Enrich
            # (best-effort) then route to 04-Resources. Enrichment never fails
            # the note (Pass L principle).
            t0 = time.monotonic()
            url = enrich.extract_url(transcript)
            existing = enrich.find_by_source_url(config.vault_path, url) if url else None
            if existing is not None:
                # Same link shared again (Pass S3) — the new thought is
                # appended to the note that's already there; no second note,
                # no re-enrichment (nothing about the LINK changed).
                enrich.append_insight(existing, transcript)
                events.log(fkey, "enrich", "ok", 0, message="status=duplicate")
                events.log(fkey, "route", "ok", int((time.monotonic() - t0) * 1000),
                           message=f"duplicate — appended insight to {existing.name}")
                paths = [existing]
                cls = classify_mod.Classification(type="resource", title=existing.stem,
                                                  confidence=1.0, needs_review=False, routed_by="link")
            else:
                enr = (enrich.enrich_url(url, config, fetch=deps.enrich_fetch) if url
                       else enrich.Enrichment("web", False, "", detail="No URL found in the capture."))
                structured = enrich.structure(transcript, enr, config, router=deps.enrich_router)
                paths = [enrich.route_link(item, transcript, enr, structured, config.vault_path)]
                events.log(fkey, "enrich", "ok", int((time.monotonic() - t0) * 1000),
                           message=f"platform={enr.platform} enriched={str(enr.enriched).lower()}")
                events.log(fkey, "route", "ok", 0, message=f"wrote {paths[0].name}")
                cls = classify_mod.Classification(type="resource", title=structured.get("title", item.name),
                                                  confidence=1.0, needs_review=False, routed_by="link")
            status = "ok"
        elif item.kind == "image":
            # A photo IS media, not something to classify — no LLM classify
            # call, no review gate. Filed as a resource by default (D-PHOTO),
            # or as the tagged type's note when a capture tag was attached at
            # share time. Vision description is best-effort decoration on top,
            # same principle as link enrichment (Pass L).
            t0 = time.monotonic()
            insight = enrich.take_image_insight(item.path)
            attachment = enrich.move_image_to_vault(item, config.vault_path)
            attachment_rel = str(attachment.relative_to(config.vault_path))
            events.log(fkey, "archive", "ok", int((time.monotonic() - t0) * 1000),
                       message=f"moved to {attachment_rel}")

            t0 = time.monotonic()
            vision_result = vision_mod.describe(attachment, config, caller=deps.vision_caller)
            events.log(fkey, "vision", "ok" if vision_result else "failed",
                       int((time.monotonic() - t0) * 1000),
                       message="described" if vision_result else "no description — note saved anyway")

            tag = (item.tag or "").lower() if item.tag else None
            note_type = classify_mod.TAG_TO_TYPE.get(tag)
            t0 = time.monotonic()
            if note_type and note_type != "resource":
                cls = classify_mod.Classification(
                    type=note_type,
                    title=(insight.splitlines()[0].strip() if insight else item.name) or "photo",
                    tags=[tag], confidence=1.0, needs_review=False, routed_by="tag")
                paths = [enrich.route_tagged_image(item, cls, vision_result, insight,
                                                   attachment_rel, config.vault_path)]
            else:
                paths = [enrich.route_image(item, vision_result, insight, attachment_rel,
                                            config.vault_path)]
                cls = classify_mod.Classification(type="resource", title=paths[0].stem,
                                                  confidence=1.0, needs_review=False,
                                                  routed_by="tag" if tag else "vision")
            events.log(fkey, "route", "ok", int((time.monotonic() - t0) * 1000),
                       message=f"wrote {paths[0].name}")
            status = "ok"
        else:
            # Stage 3 — classify. A Plaud transcript carrying two or more
            # speakers IS a conversation — deterministic, no model call spent
            # deciding it. Attendees are only SUGGESTED here (matched against
            # 07-People) and logged to events.db, never written to
            # frontmatter: confirming them is a human act in triage, which is
            # what actually appends the interaction-log line (CLAUDE.md §3 —
            # no AI bulk-write reaches a person note unreviewed). The note
            # still parks in 00-Inbox at needs-review — not because the TYPE
            # is in doubt, but because the attendee suggestions are.
            t0 = time.monotonic()
            is_conversation = plaud_transcript is not None and plaud_transcript.is_conversation
            if is_conversation:
                people = relationships.load_people(config.vault_path)
                suggested = plaud.match_people(plaud_transcript.speakers, people)
                cls = classify_mod.Classification(
                    type="conversation", title=item.name, confidence=1.0,
                    needs_review=True, routed_by="plaud", speakers=plaud_transcript.speakers)
                if suggested:
                    # JSON, not a hand-rolled "label:id,label:id" line — a
                    # speaker's name is untrusted, spoken-transcript text and
                    # can legitimately contain a comma or colon of its own.
                    events.log(fkey, "attendees", "ok",
                              message=json.dumps({"suggested": suggested}))
            else:
                cls = classify_mod.classify(item, transcript, config, deps.classifier_fn)
            status = "needs_review" if cls.needs_review else "ok"
            provider_note = f" provider={cls.provider}" if cls.provider else ""
            evidence_note = f' evidence="{cls.evidence}"' if cls.evidence else ""
            events.log(fkey, "classify", status, int((time.monotonic() - t0) * 1000),
                       message=f"type={cls.type} confidence={cls.confidence:.2f} by={cls.routed_by}"
                               + provider_note + evidence_note)
            for att in cls.attempts:   # router stats — aggregated by GET /api/providers
                conf_note = f" confidence={att.confidence:.2f}" if att.confidence is not None else ""
                events.log(fkey, "llm", "ok" if att.outcome == "served" else "failed",
                           message=f"provider={att.provider} outcome={att.outcome}" + conf_note)

            # Stage 4 — route
            t0 = time.monotonic()
            paths = route.route(item, cls, body, config.vault_path, duration_min,
                                transcript_source="plaud" if is_conversation else None)
            events.log(fkey, "route", "ok", int((time.monotonic() - t0) * 1000),
                       message=f"wrote {', '.join(p.name for p in paths)}")

            # Stage 4b — related note ("past-you thought this too", B7). This
            # is AI-suggested metadata, same provenance class as the note's
            # own meta_origin (already set by build_frontmatter from
            # cls.routed_by) — no separate origin field needed for one link.
            t0 = time.monotonic()
            match = related.find(config.vault_path, cls.title, body, note_id)
            if match:
                dest = paths[0]
                fm_block, sep, rest = dest.read_text(encoding="utf-8").partition("\n---\n")
                # route._wikilink only sanitizes an id (strips brackets/quotes
                # that would escape the field) — it does not add the
                # "[[...]]" wrapping itself; route._yaml_links does that for
                # LIST fields (`f'  - "[[{v}]]"'`). This is the single-scalar
                # equivalent, hand-formatted the same way.
                link_value = f'"[[{route._wikilink(match["id"])}]]"'
                dest.write_text(
                    route.stamp_field(fm_block, "related", link_value)
                    + sep + rest, encoding="utf-8")
                events.log(fkey, "related", "ok", int((time.monotonic() - t0) * 1000),
                           message=f'related_id={match["id"]} related_title="{match["title"]}"')
            else:
                events.log(fkey, "related", "ok", int((time.monotonic() - t0) * 1000),
                           message="related=none")

        if item.kind != "image":
            # Stage 5 — extract action items (append only). Images have no
            # transcript to extract from.
            t0 = time.monotonic()
            n = extract.extract(transcript, note_id, item.captured, config, llm_fn=deps.extract_llm)
            events.log(fkey, "extract", "ok", int((time.monotonic() - t0) * 1000),
                       message=f"{len(n)} action item(s)")

            # Stage 6 — archive the source. An image was already moved into
            # the vault's attachments/ above — that IS its permanent home, so
            # there's no separate external archive step for it.
            t0 = time.monotonic()
            archive.archive(item.path, config.archive_path)
            events.log(fkey, "archive", "ok", int((time.monotonic() - t0) * 1000))

        res.type = cls.type
        res.dest = paths[0].parent.name
        res.confidence = cls.confidence
        res.status = status

        echo = ""
        if item.kind == "audio":
            echo = echo_mod.first_words(transcript)
        elif item.kind == "image":
            # vision_mod.describe returns {description, resource_type,
            # extracted_text} or None (see pipeline/vision.py) — not raw
            # text, so the echo is the description field, same as the title
            # fallback in enrich.route_image.
            echo = echo_mod.first_words((vision_result or {}).get("description") or "")
        echo_clause = f' — heard: "{echo}"' if item.kind == "audio" and echo else (
            f' — saw: "{echo}"' if item.kind == "image" and echo else "")

        events.append_capture_log(
            f"{'⚠️ needs-review' if cls.needs_review else '✅'} {item.path.name} → "
            f"{cls.type} → {res.dest} (conf {cls.confidence:.2f}){echo_clause}")

        if item.kind == "audio" and echo:
            errors.ntfy(config.ntfy_url, config.ntfy_topic, f'Heard: "{echo}"',
                       title="Brain Cockpit — captured")

        return res

    except errors.StageError as e:
        # the envelope must say which kind of failure this was and how many
        # tries were made — fold it into the cause, in plain English
        if e.transient:
            e.cause += (f" The pipeline tried {e.attempts} times, waiting longer between "
                        "each try, before setting the file aside.")
        else:
            e.cause += " The pipeline didn't retry — this kind of failure doesn't fix itself."
        return _fail(item, config, events, res, e.what, e.plain(),
                     kind="transient" if e.transient else "permanent", attempts=e.attempts)
    except Exception as e:  # unknown failure — still plain-English, still keep going
        what = "Something went wrong processing this file."
        plain = (f"What happened: {what}\nLikely cause: an unexpected error the pipeline "
                 "doesn't recognise. The pipeline didn't retry — this kind of failure "
                 "doesn't fix itself.\nWhat to do: open the event's technical detail, "
                 "fix what it names, then retry from the Pipeline screen.")
        # the exception type is technical detail — it goes in the event message
        # (behind the disclosure), never in the plain-English parts
        return _fail(item, config, events, res, what, plain, detail=f"{type(e).__name__}: {e}")


def _fail(item, config, events, res: Result, what: str, plain: str,
          kind: str = "permanent", attempts: int = 1, detail: str = "") -> Result:
    """Record one file's failure and move on.

    This runs INSIDE process_file's except handler, so it must not raise: a full
    disk, a read-only failed/ folder or a locked database would otherwise escape
    the handler and abort the whole run_once comprehension — one unquarantinable
    file stopping the entire batch, which is the opposite of this module's
    "one bad file never stops the run" contract. Each step is therefore
    independently best-effort, and the Result is always returned."""
    fkey = str(item.path)
    res.status, res.error = "failed", what
    message = f"{what} kind={kind} attempts={attempts}"
    if detail:
        message += f" — {detail}"

    try:
        # The source may already be quarantined if it moved; guard on existence.
        if item.path.exists():
            errors.quarantine(item.path, config.failed_path)
    except Exception:
        log.exception("could not quarantine %s — it stays in the inbox", fkey)
        message += " — quarantine failed, the file is still in the inbox"

    for step, action in (
        ("event log", lambda: events.log(fkey, "pipeline", "failed", message=message,
                                         plain_english_error=plain)),
        ("ntfy", lambda: errors.ntfy(config.ntfy_url, config.ntfy_topic, plain,
                                     title="Brain Cockpit — file failed")),
        ("capture log", lambda: events.append_capture_log(f"❌ {item.path.name} — {what}")),
    ):
        try:
            action()
        except Exception:
            log.exception("failure bookkeeping (%s) failed for %s", step, fkey)
    return res


def _print_summary(results: list[Result]) -> None:
    print(f"\n{'file':<32} {'type':<12} {'destination':<16} {'conf':>5}  status")
    print("-" * 78)
    for r in results:
        print(f"{r.name[:32]:<32} {r.type:<12} {r.dest:<16} {r.confidence:>5.2f}  {r.status}")
    print()


def sync_vault(config, events: EventLog) -> None:
    """Push/pull the vault's own git history to its configured remote (Pass
    H1 — the island fix, F1). A quiet no-op when VAULT_GIT_REMOTE isn't set
    (most local/dev deploys); never raises — vaultsync.sync's own contract,
    same "a tick may fail, the loop may not" rule as everything else here."""
    if vaultsync.remote_config(config) is None:
        return
    result = vaultsync.sync(Path(config.vault_path), config)
    events.log(str(config.vault_path), "vault_sync",
              "ok" if result.status == "ok" else "failed",
              message=f"status={result.status} ahead={result.ahead} behind={result.behind}"
                      + (f" — {result.detail}" if result.detail else ""))


def drain_tick(config, events: EventLog) -> None:
    """Anti-guilt drain (Pass A, B5) — files stale triage items at best guess
    once a day. Lazy import: api/notes.py isn't a dependency of the pipeline
    package under normal operation (only the FastAPI app imports it), and a
    top-level import here would create a pipeline→api coupling nothing else
    in this package has. Never raises — same "a tick may fail, the loop may
    not" contract as every other run_loop step."""
    if not config.raw.get("triage", {}).get("drain", True):
        return
    today_key = f"drain-{date.today().isoformat()}"
    if events.reminder_fired(today_key):
        return
    try:
        from api import notes
        result = notes.drain_review(Path(config.vault_path), events.db_path)
        events.log(str(config.vault_path), "drain", "ok",
                  message=f"filed={result['filed']} parked={result['parked']}")
    except Exception:
        log.exception("drain tick failed — retrying at the next poll")
        return
    events.mark_reminder(today_key)


def run_once(config, events: EventLog, deps: Deps) -> list[Result]:
    events.heartbeat(HEARTBEAT_PATH)
    # pull anything new out of the app-owned folders (Plaud Desktop, Note Pro
    # exports, Voice Memos) before polling the inbox — every entry point
    # (one-shot run, --loop, --backlog, POST /api/run) drains watched folders
    ingest.sweep(config, events)
    items = intake.poll(config.inbox_path)
    results = [process_file(it, config, events, deps) for it in items]
    events.write_status(pending=len(intake.poll(config.inbox_path)))
    return results


def run_loop(config, events, deps) -> None:
    """Poll forever. A tick may fail; the loop may not.

    An unmounted inbox makes intake.poll raise FileNotFoundError, which used to
    kill the watcher process outright — the pipeline then stayed dead until
    someone noticed, and the only signal was the API watchdog's push half an
    hour later. A transient condition must cost one tick, not the daemon."""
    print(f"Watching {config.inbox_path} — polling every {POLL_SECONDS // 60} min. Ctrl-C to stop.")
    while True:
        try:
            # ingest.sweep runs inside run_once now (D1) — every entry point
            # drains watched folders, not just the loop.
            results = run_once(config, events, deps)
            if results:
                print(f"Processed {len(results)} file(s).")
            todos.tick(config, events)              # reminders + optional digest
            drain_tick(config, events)                # Pass A: anti-guilt drain — best-guess filing
            enrich.retry_pending(config, events)    # one re-attempt for stale enriched:false notes
            intake.sweep_orphaned_sidecars(config.inbox_path)  # abandoned photo-insight dotfiles
            sync_vault(config, events)               # push/pull the vault's own git history
        except KeyboardInterrupt:
            raise
        except Exception:
            # the heartbeat deliberately is NOT refreshed on a failed tick, so a
            # persistently broken loop still reads as stale in the cockpit
            log.exception("watcher tick failed — retrying at the next poll")
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
    ingest.sweep(config, events)  # same as run_once — every entry point drains watched folders
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
        sync_vault(config, events)   # push this batch out before the review pause
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
