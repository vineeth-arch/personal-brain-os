"""Brain Cockpit API — Pass 2. Imports the pipeline engine (never duplicates it)
and serves the built cockpit from web/dist so the whole app is one process.

Run from anywhere: `uvicorn api.main:app` — all paths are anchored to the repo
root, not the CWD. Auth is a single shared bearer token (config api.auth_token)
on every route except GET /api/health (the connect screen must be able to tell
"server down" from "bad token") and the static app shell.

Errors: every non-2xx body is {"error": {what, cause, todo}} in plain English
(CLAUDE.md §5). Stack traces and exception types go to the server log only.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from pipeline import classify, config as config_mod, enrich, intake, llm, route as proute, todos as ptodos, watcher
from pipeline.events import EventLog

from . import (build_status, google, integrations, notes, people as people_mod,
               push as push_mod, selfcheck, service, watchdog)

log = logging.getLogger("api")
logging.basicConfig(level=logging.INFO)

# Two different roots, deliberately. In a dev checkout they are the same
# directory, which is why they were conflated; in the container they are not.
#
#   STATE root — config.json, events.db, the heartbeat, backups/. Mutable,
#     persisted, mounted (/data). BRAIN_COCKPIT_ROOT points here, and the
#     watcher anchors to the same env var (pipeline/watcher.py) so both
#     processes read and write ONE database.
#   APP root — web/dist, checks.json, and the source files the build probes
#     inspect. Read-only, shipped inside the image (/app), and always found
#     relative to this file rather than to a mount or the CWD.
DEFAULT_ROOT = Path(os.environ.get("BRAIN_COCKPIT_ROOT") or Path(__file__).resolve().parents[1])
APP_ROOT = Path(__file__).resolve().parents[1]


class Envelope(Exception):
    """Raise anywhere in a handler to return a plain-English error."""

    def __init__(self, status: int, what: str, cause: str, todo: str):
        self.status = status
        self.body = {"error": {"what": what, "cause": cause, "todo": todo}}
        super().__init__(what)


def _google_page(title: str, body: str) -> str:
    """The one HTML page this API renders itself: the tab Google redirects
    back to after consent. It can't be part of the React app (that tab is
    outside the cockpit shell), so it's self-contained — no assets, no fonts
    to fetch, dark-mode indigo per DESIGNSYSTEM.md, flush-left, one accent."""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Brain Cockpit</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; min-height: 100vh; display: flex; align-items: center;
         background: #1F006E; color: #E2D8F5;
         font-family: "Hanken Grotesk", ui-sans-serif, system-ui, sans-serif; }}
  main {{ max-width: 34rem; margin: 0 auto; padding: 2rem 1.5rem; }}
  p.eyebrow {{ margin: 0; color: #00FFCF; font-size: 11px; font-weight: 700;
              letter-spacing: 0.18em; text-transform: uppercase; }}
  h1 {{ margin: 0.75rem 0 0; color: #FFFFFF; font-size: 2rem; line-height: 0.95;
       letter-spacing: -0.02em; font-weight: 800;
       font-family: "Bricolage Grotesque", ui-sans-serif, system-ui, sans-serif; }}
  p.body {{ margin: 1rem 0 0; font-size: 0.95rem; line-height: 1.5; }}
</style></head>
<body><main>
  <p class="eyebrow">Brain Cockpit · Google</p>
  <h1>{title}</h1>
  <p class="body">{body}</p>
</main></body></html>"""


def _generic_envelope(status: int) -> dict:
    if status == 404:
        return {"error": {
            "what": "The server doesn't know that request.",
            "cause": "No route matches — the app and the API may be out of sync.",
            "todo": "Check API-CONTRACT.md and update whichever side is behind."}}
    if status == 405:
        return {"error": {
            "what": "That request used the wrong method.",
            "cause": "The route exists but not for this kind of request.",
            "todo": "Check API-CONTRACT.md for the route's method."}}
    return {"error": {
        "what": "The server couldn't complete that request.",
        "cause": f"It hit an unexpected condition (error {status}).",
        "todo": "Try again; if it keeps happening, check the server log."}}


class ApproveBody(BaseModel):
    type: str
    # confirmed 07-People ids — the pipeline only ever SUGGESTS attendees
    # (via events.db, GET /api/review); a human confirming here is what
    # actually writes them (CLAUDE.md §3). Ignored for anything but a
    # conversation note — omitting it behaves exactly as before this field
    # existed.
    attendees: list[str] = []


class CaptureBody(BaseModel):
    text: str | None = None
    url: str | None = None
    insight: str | None = None
    tag: str | None = None


class EngineBody(BaseModel):
    engine: str


class ConfigBody(BaseModel):
    engine: str | None = None
    language: str | None = None
    confidence_threshold: float | None = None
    ntfy_topic: str | None = None
    ntfy_url: str | None = None
    transliteration_engine: str | None = None
    transliteration_ollama_url: str | None = None
    transliteration_ollama_model: str | None = None
    transliteration_openrouter_model: str | None = None


class StatusBody(BaseModel):
    status: str


class InsightBody(BaseModel):
    text: str


class DraftBody(BaseModel):
    to: str
    subject: str
    text: str


class PersonDraftBody(BaseModel):
    channel: str | None = None


class ContactBody(BaseModel):
    note: str = ""
    channel: str = ""


class StageBody(BaseModel):
    stage: str


class VoiceBody(BaseModel):
    samples: list[str]


class PushPreviewBody(BaseModel):
    target: str


class PushBody(BaseModel):
    target: str
    text: str


class ChannelBody(BaseModel):
    kind: str
    value: str


class NewPersonBody(BaseModel):
    name: str
    channel: ChannelBody


class ResurfacedResponseBody(BaseModel):
    action: str
    # the card's own title, sent by the client rather than looked up
    # server-side — see the route below for why
    title: str = ""


class BreakdownBody(BaseModel):
    feel: int


# ---- micro-step breakdown (B10): prompt + validator, mirroring
# classify.py's build_prompt/validate_classification split ----------------

def _breakdown_prompt(task: str) -> str:
    return (
        "Break this todo into 2 to 4 short concrete steps. Return ONLY JSON "
        'with one key: steps (a list of 2-4 short strings, each 80 characters or less).\n\n'
        f"TODO: {task}"
    )


def _validate_breakdown(data: object) -> str | None:
    if not isinstance(data, dict):
        return "not a JSON object"
    steps = data.get("steps")
    if not isinstance(steps, list) or not (2 <= len(steps) <= 4):
        return "steps must be a list of 2 to 4 items"
    for s in steps:
        if not isinstance(s, str) or not s.strip() or len(s) > 80 or "\n" in s:
            return "each step must be a single non-empty line of 80 characters or fewer"
    return None


def create_app(root: Path | None = None, app_root: Path | None = None) -> FastAPI:
    root = Path(root or DEFAULT_ROOT)          # state: config, db, heartbeat, backups
    app_root = Path(app_root or APP_ROOT)      # code: web/dist, checks.json, probes

    # Startup self-check (Pass 5): refuse to boot on structural problems and
    # print exactly what to fix. Softer conditions (no whisper binary, no ntfy)
    # never block the boot — they're Integrations-card material.
    report = selfcheck.run(root)
    if not report["ok"]:
        message = selfcheck.refusal_message(report["problems"])
        log.error("%s", message)
        raise SystemExit(message)

    app = FastAPI(title="Brain Cockpit API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.root = root
    app.state.app_root = app_root
    app.state.run_proc = None
    app.state.run_lock = threading.Lock()
    app.state.integrations_cache = {}
    app.state.integrations_state = {}
    app.state.google_states = {}   # pending OAuth CSRF states → (expiry, redirect_uri)
    app.state.google_tokens = {}   # in-memory access-token cache — never persisted
    app.state.build_cache = {}

    config_path = root / "config.json"
    # by NAME, not by the watcher's resolved path — the watcher anchors those to
    # BRAIN_COCKPIT_ROOT, and joining an already-absolute path onto `root` here
    # would silently ignore an explicit create_app(root=...) in a test
    db_path = root / watcher.DB_NAME
    heartbeat_path = root / watcher.HEARTBEAT_NAME

    def load_config():
        try:
            return config_mod.load(config_path)
        except FileNotFoundError:
            raise Envelope(
                500, "The server has no configuration yet.",
                f"{config_path} doesn't exist.",
                "Copy config.example.json to config.json and fill in the paths.")
        except Exception:
            log.exception("config load failed")
            raise Envelope(
                500, "The server configuration couldn't be read.",
                "config.json exists but isn't valid JSON or is missing required paths.",
                "Fix config.json (compare with config.example.json), then retry.")

    # ---- auth ----------------------------------------------------------------

    def require_token(request: Request):
        config = load_config()
        expected = str((config.raw.get("api") or {}).get("auth_token") or "")
        header = request.headers.get("Authorization", "")
        supplied = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        # an empty configured token rejects everything — never accept-all
        if not expected or not supplied or not secrets.compare_digest(supplied, expected):
            raise Envelope(
                401, "The server rejected the access token.",
                "The token doesn't match api.auth_token in the server's config.json."
                if expected else "No api.auth_token is set in the server's config.json.",
                "Re-enter the token from your config on the connect screen."
                if expected else "Set a random string as api.auth_token in config.json, then reconnect.")
        return config

    # ---- error handlers --------------------------------------------------------

    @app.exception_handler(Envelope)
    async def _envelope_handler(_req, exc: Envelope):
        return JSONResponse(exc.body, status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(_req, exc: StarletteHTTPException):
        # covers unknown /api/* paths falling through the static mount too
        if isinstance(exc.detail, dict) and {"what", "cause", "todo"} <= set(exc.detail):
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        return JSONResponse(_generic_envelope(exc.status_code), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_req, exc: RequestValidationError):
        log.info("validation error: %s", exc.errors())
        return JSONResponse({"error": {
            "what": "The request body wasn't what the server expected.",
            "cause": "A required field is missing or has the wrong shape.",
            "todo": "Check API-CONTRACT.md for the route's request format."}}, status_code=400)

    @app.exception_handler(Exception)
    async def _catchall_handler(_req, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse({"error": {
            "what": "The server hit an unexpected error.",
            "cause": "A bug in the API, not your pipeline or your notes.",
            "todo": "Try again; if it repeats, check the server log for the technical detail."}},
            status_code=500)

    # ---- read routes -------------------------------------------------------------

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/status")
    def status(config=Depends(require_token)):
        heartbeat = None
        if heartbeat_path.exists():
            heartbeat = heartbeat_path.read_text(encoding="utf-8").strip() or None
        try:
            pending = len(intake.poll(config.inbox_path))
        except OSError:
            pending = 0
        return {
            "vault": Path(config.vault_path).name,
            "engine": config.engine,
            "heartbeat": heartbeat,
            "last_run": heartbeat,
            "counts": {
                "pending": pending,
                "processed_today": service.processed_today(db_path),
                "needs_review": notes.count_review(config.vault_path),
                "failed": len(service.failed_items(db_path)),
            },
        }

    @app.get("/api/review")
    def review(config=Depends(require_token)):
        items = notes.list_review(config.vault_path, db_path)
        return {
            "items": items,
            "queue_total": len(items),
            "accuracy": service.accuracy(db_path),
            "trust": service.trust(db_path),
        }

    @app.get("/api/failed")
    def failed(config=Depends(require_token)):
        return {"items": service.failed_items(db_path)}

    @app.get("/api/events")
    def events(status: str | None = None, limit: int = 100, before_id: int | None = None,
               config=Depends(require_token)):
        return {"events": service.events_list(db_path, status, min(max(limit, 1), 500), before_id)}

    @app.get("/api/streak")
    def streak(config=Depends(require_token)):
        return service.streak(db_path)

    @app.get("/api/resurfaced")
    def resurfaced(config=Depends(require_token)):
        notes_list = notes.resurface(config.vault_path, db_path, k=2)
        return {"note": notes_list[0] if notes_list else None, "notes": notes_list}

    @app.post("/api/resurfaced/{note_id}/response")
    def resurfaced_response(note_id: str, body: ResurfacedResponseBody,
                            config=Depends(require_token)):
        if body.action not in ("connect", "act", "archive"):
            raise Envelope(
                400, "That's not a response this screen understands.",
                f"'{body.action}' isn't connect, act, or archive.",
                "Use one of the three resurfaced-note buttons.")
        # title is needed for the todo line's text on "act". A server-side
        # re-pick (pipeline.resurface.pick again) would NOT reliably find this
        # note: pick() stamps last_shown/shows on every call it makes,
        # including the GET that put this card on screen — so by the time the
        # user taps a button, the note is already inside its OWN cooldown
        # window and a fresh pick() call would exclude it. The client already
        # has the title (it's rendering the card), so it rides along in the
        # request body instead — simpler and doesn't depend on picker timing.
        title = body.title.strip() or note_id
        todo_block = notes.resurface_respond(config.vault_path, db_path, note_id,
                                             body.action, title)
        return {"ok": True, "todo_block": todo_block}

    TODO_RANGES = ("today", "tomorrow", "week", "overdue")

    @app.get("/api/todos")
    def todos_list(range: str = "today", config=Depends(require_token)):
        if range not in TODO_RANGES:
            raise Envelope(
                400, "That's not a todo range the server knows.",
                f"'{range}' isn't one of today, tomorrow, week, overdue.",
                "Use one of the four ranges.")
        items = [
            {
                "id": t.block_id,
                "task": t.task,
                "due": t.due,
                "time": t.time,
                "done": t.done,
                "overdue": ptodos.in_range(t, "overdue"),
                "file": str(t.file.relative_to(config.vault_path)),
                "feel": t.feel,
                "children": [{"id": c.block_id, "task": c.task, "done": c.done} for c in t.children],
            }
            for t in ptodos.scan(config.vault_path)
            if t.block_id and ptodos.in_range(t, range)
        ]
        items.sort(key=lambda i: (i["due"] or "", i["time"] or "99:99"))
        return {"items": items}

    @app.post("/api/todos/{block_id}/toggle")
    def todos_toggle(block_id: str, config=Depends(require_token)):
        try:
            done = ptodos.toggle(config.vault_path, block_id)
        except LookupError:
            raise Envelope(
                404, "That todo isn't in the daily notes anymore.",
                "Its line was edited or removed in Obsidian, or the id is unknown.",
                "Refresh the agenda.")
        notes.git_commit_vault(
            config.vault_path,
            f"api: todo {block_id} marked {'done' if done else 'open'}")
        return {"ok": True, "done": done}

    @app.post("/api/todos/{block_id}/breakdown")
    def todos_breakdown(block_id: str, body: BreakdownBody, config=Depends(require_token)):
        if not (1 <= body.feel <= 5):
            raise Envelope(
                400, "That's not a feel-dial value this screen understands.",
                f"'{body.feel}' isn't 1 through 5.",
                "Tap one of the five dots.")
        try:
            existing = next(t for t in ptodos.scan(config.vault_path) if t.block_id == block_id)
        except StopIteration:
            raise Envelope(
                404, "That todo isn't in the daily notes anymore.",
                "Its line was edited or removed in Obsidian, or the id is unknown.",
                "Refresh the agenda.")
        if existing.children:
            raise Envelope(
                409, "This todo's already broken down.",
                "It has steps under it already — breaking it down twice would duplicate them.",
                "Check the steps already there.")

        data, provider, attempts = llm.complete_json(
            _breakdown_prompt(existing.task), config, _validate_breakdown)
        if data is None:
            raise Envelope(
                503, "Couldn't break this down right now.",
                "Every model in the chain failed or is unreachable.",
                "Try again in a bit, or just do it as one step.")

        updated = ptodos.add_breakdown(config.vault_path, block_id, body.feel, data["steps"])
        notes.git_commit_vault(config.vault_path, f"api: broke down {block_id}")
        return {
            "id": updated.block_id,
            "task": updated.task,
            "feel": updated.feel,
            "children": [{"id": c.block_id, "task": c.task, "done": c.done} for c in updated.children],
        }

    @app.get("/api/build")
    def build(fresh: int = 0, config=Depends(require_token)):
        cache = app.state.build_cache
        now = time.monotonic()
        if not fresh and cache.get("payload") and now - cache.get("ts", 0) < 60:
            return cache["payload"]
        try:
            items = build_status.run_probes(app_root, config, db_path)
        except build_status.ManifestError as e:
            raise Envelope(500, **e.envelope)
        unfinished = next((i for i in items if not i["done"]), None)
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "next": ({"label": unfinished["label"], "next_action": unfinished["next_action"]}
                     if unfinished else None),
            "items": items,
        }
        cache["ts"] = now
        cache["payload"] = payload
        return payload

    @app.get("/api/providers")
    def providers(config=Depends(require_token)):
        stats: dict[str, dict] = {}
        for row in service.events_list(db_path, None, 5000, None):
            if row["stage"] != "llm":
                continue
            msg = row["message"] or ""
            fields = dict(p.split("=", 1) for p in msg.split() if "=" in p)
            name = fields.get("provider")
            if not name:
                continue
            st = stats.setdefault(name, {"provider": name, "served": 0, "fell_through": 0,
                                         "invalid_json": 0, "confidences": []})
            outcome = fields.get("outcome", "")
            if outcome == "served":
                st["served"] += 1
                # the message is parsed log text, not a schema — a malformed
                # token must not 500 the whole stats page
                try:
                    st["confidences"].append(float(fields["confidence"]))
                except (KeyError, ValueError):
                    pass
            elif outcome in ("invalid-json", "schema"):
                st["invalid_json"] += 1
                st["fell_through"] += 1
            else:
                st["fell_through"] += 1
        out = []
        for st in stats.values():
            confs = st.pop("confidences")
            st["avg_confidence"] = round(sum(confs) / len(confs), 2) if confs else None
            out.append(st)
        out.sort(key=lambda s: -s["served"])
        return {"providers": out}

    # ---- write routes (each git-commits the vault) ---------------------------------

    @app.post("/api/review/{note_id}/approve")
    def approve(note_id: str, body: ApproveBody, config=Depends(require_token)):
        if body.type not in classify.NOTE_TYPES:
            raise Envelope(
                400, "That's not a note type the vault knows.",
                f"'{body.type}' isn't one of the 13 types in SCHEMA-REFERENCE.md.",
                "Pick one of the type chips and try again.")
        events = EventLog(db_path, Path(config.vault_path))
        try:
            moved_to = notes.approve(config.vault_path, note_id, body.type, body.attendees,
                                     events=events)
        except LookupError:
            raise Envelope(
                404, "That note isn't waiting for review anymore.",
                "It was already approved (possibly from another device), or the id is unknown.",
                "Refresh the triage queue.")
        finally:
            events.close()
        return {"ok": True, "moved_to": moved_to}

    @app.post("/api/capture", status_code=201)
    def capture(body: CaptureBody, config=Depends(require_token)):
        # Two shapes, same route (Pass S): {text} is the quick-capture box;
        # {url, insight?} is a share (the "→ Brain Cloud" Shortcut, or a
        # future in-app share button) — the insight rides ALONGSIDE the URL
        # rather than being mashed into one blob the pipeline has to unpick.
        if body.url is not None:
            url = body.url.strip()
            if not re.match(r"^https?://", url, re.IGNORECASE):
                raise Envelope(
                    400, "That doesn't look like a link.",
                    "The url field has to start with http:// or https://.",
                    "Share the link itself, not just a caption or a search term.")
            insight = (body.insight or "").strip()
            text = f"{insight}\n\n{url}" if insight else url
        else:
            text = (body.text or "").strip()
            if not text:
                raise Envelope(
                    400, "There was nothing to capture.",
                    "The capture text was empty.",
                    "Type a thought first, then press Capture.")
        if not notes.valid_tag(body.tag):
            raise Envelope(
                400, "That's not a capture tag the pipeline knows.",
                f"'{body.tag}' isn't one of the 8 capture tags in SCHEMA-REFERENCE.md.",
                "Pick one of the tag chips, or send no tag and let the classifier decide.")
        # the inbox is outside the vault — nothing to git-commit here; the
        # watcher's processing (and any approve) is where vault history is made
        note_id = notes.capture(Path(config.inbox_path), text, body.tag)
        return {"id": note_id, "status": "captured"}

    @app.post("/api/capture/audio", status_code=201)
    async def capture_audio(request: Request, config=Depends(require_token)):
        """A recording from the cockpit's mic button. The body is the raw audio
        (no multipart — python-multipart isn't a locked dependency, CLAUDE.md
        §7), streamed to the inbox so a long recording never sits in memory."""
        ext = notes.audio_extension(request.headers.get("content-type"))
        if ext is None:
            raise Envelope(
                400, "That recording isn't in a format the pipeline can read.",
                f"The upload's Content-Type was '{request.headers.get('content-type') or 'missing'}'.",
                "Record again with the mic button, or drop the audio file into the inbox folder instead.")
        tag = request.query_params.get("tag") or None
        if not notes.valid_tag(tag):
            raise Envelope(
                400, "That's not a capture tag the pipeline knows.",
                f"'{tag}' isn't one of the 8 capture tags in SCHEMA-REFERENCE.md.",
                "Pick one of the tag chips, or send no tag and let the classifier decide.")

        inbox = Path(config.inbox_path)
        path, note_id = notes.audio_capture_path(inbox, ext, request.query_params.get("name"), tag)
        fd, tmp = tempfile.mkstemp(dir=inbox, prefix=".capture-", suffix=".tmp")
        written = 0
        try:
            with os.fdopen(fd, "wb") as f:
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > notes.MAX_AUDIO_BYTES:
                        raise Envelope(
                            413, "That recording is too large to upload.",
                            f"The upload passed the {notes.MAX_AUDIO_BYTES // (1024 * 1024)} MB limit "
                            "this server accepts.",
                            "Record in shorter takes, or copy the file straight into the inbox folder "
                            "— the watcher picks it up from there with no size limit.")
                    f.write(chunk)
            if written == 0:
                raise Envelope(
                    400, "There was nothing to capture.",
                    "The recording arrived empty — the mic may have been blocked mid-recording.",
                    "Check the microphone permission, then record again.")
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        # a streamed .webm/.mp4 has no duration in its header — a best-effort
        # stream-copy remux writes one, so duration_min isn't silently absent
        # on every mic capture (D7); never blocks the response either way
        notes.remux_for_duration(path)
        # the inbox is outside the vault — nothing to git-commit here
        return {"id": note_id, "status": "captured"}

    @app.post("/api/capture/image", status_code=201)
    async def capture_image(request: Request, config=Depends(require_token)):
        """A photo from the "→ Brain Cloud" Shortcut or the cockpit's own photo
        button (Pass V2). Same raw-body streaming as capture_audio. The client
        is always the one that resizes and converts — HEIC/large originals
        never reach this server, so there's nothing to decode here."""
        ext = notes.image_extension(request.headers.get("content-type"))
        if ext is None:
            ctype = request.headers.get("content-type") or "missing"
            heic = "heic" in ctype.lower() or "heif" in ctype.lower()
            raise Envelope(
                400, "That photo isn't in a format the pipeline can read.",
                f"The upload's Content-Type was '{ctype}'."
                + (" HEIC photos need converting first." if heic else ""),
                "Convert it to JPEG on the device — the Shortcut's Convert Image "
                "step (or the cockpit's own photo button) does this automatically."
                if heic else
                "Accepted formats are JPEG, PNG, and WebP.")
        tag = request.query_params.get("tag") or None
        if not notes.valid_tag(tag):
            raise Envelope(
                400, "That's not a capture tag the pipeline knows.",
                f"'{tag}' isn't one of the 8 capture tags in SCHEMA-REFERENCE.md.",
                "Pick one of the tag chips, or send no tag and let it become a resource.")

        inbox = Path(config.inbox_path)
        path, note_id = notes.image_capture_path(
            inbox, ext, request.query_params.get("name"), tag)
        fd, tmp = tempfile.mkstemp(dir=inbox, prefix=".capture-", suffix=".tmp")
        written = 0
        try:
            with os.fdopen(fd, "wb") as f:
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > notes.MAX_IMAGE_BYTES:
                        raise Envelope(
                            413, "That photo is too large to upload.",
                            f"The upload passed the {notes.MAX_IMAGE_BYTES // (1024 * 1024)} MB "
                            "limit this server accepts.",
                            "Resize on the device first — the Shortcut and the cockpit's photo "
                            "button both do this automatically before sending.")
                    f.write(chunk)
            if written == 0:
                raise Envelope(
                    400, "There was nothing to capture.",
                    "The upload arrived empty.",
                    "Try sharing the photo again.")
            # the insight sidecar (if any) is written BEFORE the image is
            # renamed into place, so the watcher — which ignores dotfiles —
            # never sees the image without it
            insight = request.query_params.get("insight") or ""
            if insight.strip():
                notes.image_insight_sidecar(path).write_text(insight.strip(), encoding="utf-8")
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            sidecar = notes.image_insight_sidecar(path)
            if sidecar.exists():
                sidecar.unlink(missing_ok=True)
            raise
        # the inbox is outside the vault — nothing to git-commit here
        return {"id": note_id, "status": "captured"}

    # ---- people (Relationship OS) --------------------------------------------

    def _person_or_404(config, person_id: str):
        found = people_mod.detail(Path(config.vault_path), person_id)
        if found is None:
            raise Envelope(
                404, "That person isn't in the vault.",
                f"No note in 07-People has the id {person_id}.",
                "Refresh the People screen — the note may have been renamed or removed.")
        return found

    @app.get("/api/people")
    def people_list(config=Depends(require_token)):
        return {"items": people_mod.list_people(Path(config.vault_path))}

    @app.post("/api/people", status_code=201)
    def people_create(body: NewPersonBody, config=Depends(require_token)):
        """Quick-add a warm-up target (Pass X): one name, one channel. Feeding
        the warm-up engine should not require opening Obsidian."""
        try:
            return people_mod.create_target(Path(config.vault_path), body.name,
                                            body.channel.kind, body.channel.value)
        except ValueError as e:
            raise Envelope(
                400, "That target couldn't be added.",
                str(e).capitalize() + ".",
                "Give them a name and one way to reach you — WhatsApp, email, or LinkedIn.")

    @app.get("/api/people/voice")
    def people_voice(config=Depends(require_token)):
        return people_mod.voice_status(Path(config.vault_path))

    @app.post("/api/people/voice")
    def people_voice_write(body: VoiceBody, config=Depends(require_token)):
        try:
            return people_mod.write_voice(Path(config.vault_path), body.samples)
        except ValueError:
            raise Envelope(
                400, "There were no writing samples to learn from.",
                "Every sample in the list was empty.",
                "Paste 3–5 messages you actually sent, then save again.")

    @app.get("/api/people/{person_id}")
    def person_detail(person_id: str, config=Depends(require_token)):
        return _person_or_404(config, person_id)

    @app.post("/api/people/{person_id}/draft")
    def person_draft(person_id: str, body: PersonDraftBody, config=Depends(require_token)):
        # a short-lived connection just for this request — the watcher holds
        # the long-lived one; both go through sqlite's default busy timeout
        events = EventLog(db_path, Path(config.vault_path))
        try:
            result = people_mod.draft(Path(config.vault_path), person_id, body.channel,
                                      config, events=events)
        except LookupError:
            raise Envelope(
                409, "Drafts need your own voice on file first.",
                "_System/my-voice.md doesn't exist yet, and a draft written without "
                "it would sound like a chatbot, not like you.",
                "Paste 3–5 messages you've actually sent in Settings → My voice, then try again.")
        finally:
            events.close()
        if result is None:
            raise Envelope(
                404, "That person isn't in the vault.",
                f"No note in 07-People has the id {person_id}.",
                "Refresh the People screen.")
        if not result["text"]:
            raise Envelope(
                502, "No model could write the draft.",
                "Every provider in the chain failed or has no key set.",
                "Check the model keys in the server's shell, then try again.")
        return result

    @app.post("/api/people/{person_id}/contact")
    def person_contact(person_id: str, body: ContactBody, config=Depends(require_token)):
        updated = people_mod.log_contact(Path(config.vault_path), person_id,
                                         body.note, body.channel)
        if updated is None:
            raise Envelope(
                404, "That person isn't in the vault.",
                f"No note in 07-People has the id {person_id}.",
                "Refresh the People screen.")
        return updated

    @app.post("/api/people/{person_id}/warmth")
    def person_warmth(person_id: str, body: StageBody, config=Depends(require_token)):
        from pipeline import relationships
        if body.stage not in relationships.WARMTH_STAGES:
            raise Envelope(
                400, "That's not a warmth stage the vault knows.",
                f"'{body.stage}' isn't one of the six stages in SCHEMA-REFERENCE.md.",
                "Pick one of the stage chips on the person's card.")
        updated = people_mod.set_stage(Path(config.vault_path), person_id, body.stage)
        if updated is None:
            raise Envelope(
                404, "That person isn't in the vault.",
                f"No note in 07-People has the id {person_id}.",
                "Refresh the People screen.")
        return updated

    @app.post("/api/people/{person_id}/enrich")
    def person_enrich(person_id: str, config=Depends(require_token)):
        if not people_mod.pdl_configured():
            raise Envelope(
                503, "Enrichment isn't set up yet.",
                "PDL_API_KEY isn't set in the server's shell, so there's nothing to ask.",
                "Add a People Data Labs key to the server's environment and restart the API "
                "— everything else on this card keeps working without it.")
        try:
            updated = people_mod.enrich(Path(config.vault_path), person_id)
        except Exception:
            log.exception("pdl enrich failed")
            raise Envelope(
                502, "People Data Labs didn't answer.",
                "The lookup failed or the monthly free credits are used up.",
                "Try again later; the note is unchanged.")
        if updated is None:
            raise Envelope(
                404, "That person isn't in the vault.",
                f"No note in 07-People has the id {person_id}.",
                "Refresh the People screen.")
        return updated

    # ---- profile push (Pass D) -------------------------------------------------
    # Writes PROFILE DATA into the owner's own CRM/address book. Nothing is
    # delivered to another person here (CLAUDE.md §4), and nothing is written
    # without a human confirming the exact text first (CLAUDE.md §3).

    def _push_event_log(config):
        from pipeline.events import EventLog
        return EventLog(db_path, Path(config.vault_path))

    @app.post("/api/people/{person_id}/push/preview")
    def person_push_preview(person_id: str, body: PushPreviewBody,
                            config=Depends(require_token)):
        try:
            return push_mod.preview(Path(config.vault_path), person_id, body.target,
                                    config, app.state.google_tokens)
        except push_mod.PushError as e:
            raise Envelope(e.status, **e.envelope)

    @app.post("/api/people/{person_id}/push")
    def person_push(person_id: str, body: PushBody, config=Depends(require_token)):
        event_log = None
        try:
            event_log = _push_event_log(config)
        except Exception:
            log.exception("push event log unavailable")   # never block the push on bookkeeping
        try:
            return push_mod.push(Path(config.vault_path), person_id, body.target,
                                 body.text, config, app.state.google_tokens,
                                 event_log=event_log)
        except push_mod.PushError as e:
            raise Envelope(e.status, **e.envelope)
        finally:
            if event_log is not None:
                event_log.close()

    @app.get("/api/push/queue")
    def push_queue(config=Depends(require_token)):
        return {"items": push_mod.queue(Path(config.vault_path), db_path, config),
                "available": push_mod.availability(config)}

    @app.post("/api/failed/{event_id}/retry")
    def retry(event_id: int, config=Depends(require_token)):
        row = service.failed_row(db_path, event_id)
        if row is None or row[1] != "failed":
            raise Envelope(
                404, "That failed item isn't in the log.",
                "The failure id is unknown, or the file already succeeded on a later run.",
                "Refresh the pipeline screen.")
        original = Path(row[0]).name
        failed_dir = Path(config.failed_path)
        candidate = failed_dir / original
        if not candidate.exists():
            # quarantine's collision rename: <stem>-<mtime_ns><suffix>
            stem, suffix = Path(original).stem, Path(original).suffix
            renamed = [p for p in failed_dir.glob(f"{stem}-*{suffix}")
                       if p.name.removeprefix(f"{stem}-").removesuffix(suffix).isdigit()]
            candidate = max(renamed, default=None, key=lambda p: p.stat().st_mtime)  # type: ignore[arg-type]
        if candidate is None or not candidate.exists():
            raise Envelope(
                404, "The quarantined file isn't there anymore.",
                "It was already retried or moved out of the failed folder by hand.",
                "Refresh the pipeline screen.")
        dest = Path(config.inbox_path) / original
        if dest.exists():
            raise Envelope(
                409, "A file with that name is already waiting in the inbox.",
                "This item may already have been retried.",
                "Let the next pipeline pass process it, then check again.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        candidate.rename(dest)  # failed/ and inbox/ are outside the vault — no commit
        # a photo's .insight sidecar (if any) is untouched by this move — it
        # already lives in the inbox and was never moved to failed/ in the
        # first place, so it's right where the retried image expects it
        return {"ok": True}

    @app.post("/api/run", status_code=202)
    def run(config=Depends(require_token)):
        with app.state.run_lock:
            proc = app.state.run_proc
            if proc is not None and proc.poll() is None:
                raise Envelope(
                    409, "A pipeline run is already in flight.",
                    "The previous run hasn't finished yet.",
                    "Wait for it to finish — the status card updates when it does.")
            # ponytail: a --loop watcher may poll the same inbox concurrently;
            # sqlite's busy timeout covers the db, double-processing is a
            # pre-existing pipeline property, not an API concern.
            #
            # cwd is the APP root: `-m pipeline` has to import the package, and
            # in the container the state root (/data) holds no code, so running
            # from there died with ModuleNotFoundError into a DEVNULL'd stderr —
            # "Run now" silently did nothing. The state root travels in the
            # environment and in --config instead, so this run writes the same
            # events.db the loop and the API use.
            env = {**os.environ, "BRAIN_COCKPIT_ROOT": str(root)}
            app.state.run_proc = subprocess.Popen(
                [sys.executable, "-m", "pipeline", "--config", str(config_path)],
                cwd=app_root, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"started": True}

    @app.post("/api/resources/{note_id}/enrich")
    def resource_enrich(note_id: str, config=Depends(require_token)):
        folder = Path(config.vault_path) / proute.TYPE_FOLDER["resource"]
        target = None
        target_fm: dict = {}
        if folder.is_dir():
            for path in folder.glob("*.md"):
                fm, _ = notes.parse_frontmatter(path.read_text(encoding="utf-8"))
                if fm.get("id") == note_id and fm.get("type") == "resource":
                    target, target_fm = path, fm
                    break
        if target is None:
            raise Envelope(
                404, "That resource isn't in the vault.",
                "No resource note in 04-Resources has that id.",
                "Refresh the resource list.")
        # a photo resource has no source_url to re-fetch — it re-runs vision
        # on its own attachment instead (Pass V3)
        if target_fm.get("platform") == "photo":
            enriched = enrich.reenrich_image_note(target, config)
        else:
            enriched = enrich.reenrich_note(target, config)
        notes.git_commit_vault(config.vault_path, f"api: enriched {note_id}")
        return {"ok": True, "enriched": enriched}

    # ---- Resource OS (Pass 6) --------------------------------------------------------
    # Reads/writes 04-Resources. Every mutation git-commits the vault. Insight
    # lives in a '## Insight' body section carrying the human-origin guarantee.

    RESOURCE_SORTS = ("created", "oldest", "title")

    @app.get("/api/resources")
    def resources_list(category: str | None = None, status: str | None = None,
                       q: str | None = None, has_insight: bool | None = None,
                       sort: str = "created", config=Depends(require_token)):
        if sort not in RESOURCE_SORTS:
            raise Envelope(
                400, "That's not a sort the resource list knows.",
                f"'{sort}' isn't one of created, oldest, title.",
                "Use one of the three sort values, or omit it for newest-first.")
        return {"items": notes.list_resources(
            config.vault_path, category=category, status=status, q=q,
            has_insight=has_insight, sort=sort)}

    @app.get("/api/search")
    def search(q: str = "", limit: int = notes.SEARCH_DEFAULT_LIMIT, config=Depends(require_token)):
        """Pass Q: whole-vault search — a filesystem scan, never a SQLite
        index of note content (CLAUDE.md §1)."""
        if len(q.strip()) < notes.SEARCH_MIN_QUERY_LEN:
            raise Envelope(
                400, "That search is too short to be useful.",
                f"Search needs at least {notes.SEARCH_MIN_QUERY_LEN} characters.",
                "Type a bit more, then search again.")
        capped = max(1, min(limit, notes.SEARCH_MAX_LIMIT))
        return {"items": notes.search_vault(config.vault_path, q.strip(), limit=capped)}

    # 'sample' routes are declared before '/{note_id}' so the literal path wins.
    @app.get("/api/resources/sample/count")
    def sample_count(older_than: str = "all", config=Depends(require_token)):
        if older_than not in notes.SAMPLE_SCOPES:
            raise Envelope(
                400, "That's not a cleanup scope the server knows.",
                f"'{older_than}' isn't one of 1d, 1w, 1m, all.",
                "Pick one of the four scopes.")
        matching = notes.sample_matching(config.vault_path, older_than)
        return {"count": len(matching), "scope": older_than}

    @app.delete("/api/resources/sample")
    def sample_purge(older_than: str = "all", config=Depends(require_token)):
        if older_than not in notes.SAMPLE_SCOPES:
            raise Envelope(
                400, "That's not a cleanup scope the server knows.",
                f"'{older_than}' isn't one of 1d, 1w, 1m, all.",
                "Pick one of the four scopes.")
        vault = config.vault_path
        targets = notes.sample_matching(vault, older_than)  # sample:true ONLY
        titles = notes.sample_titles(targets)
        n = len(targets)
        # Commit BEFORE deleting so the whole purge is one `git revert` away.
        notes.git_commit_vault(vault, f"pre-purge: {n} sample notes, scope={older_than}")
        for path in targets:
            path.unlink()
        if n:
            notes.git_commit_vault(vault, f"api: purged {n} sample notes (scope={older_than})")
        scope_phrase = {"1d": "older than a day", "1w": "older than a week",
                        "1m": "older than a month", "all": "of any age"}[older_than]
        message = (
            f"Removed {n} sample note{'' if n == 1 else 's'} {scope_phrase}. "
            "Your real notes were never touched, and the vault was git-committed first."
            if n else
            f"No sample notes {scope_phrase} to remove. Nothing was changed.")
        return {"removed": n, "titles": titles, "scope": older_than, "message": message}

    @app.get("/api/resources/{note_id}")
    def resource_detail(note_id: str, config=Depends(require_token)):
        detail = notes.resource_detail(config.vault_path, note_id)
        if detail is None:
            raise Envelope(
                404, "That resource isn't in the vault.",
                "No resource note in 04-Resources has that id.",
                "Refresh the resource list.")
        return detail

    @app.post("/api/resources/{note_id}/status")
    def resource_status(note_id: str, body: StatusBody, config=Depends(require_token)):
        if body.status not in notes.RESOURCE_LIFECYCLE:
            raise Envelope(
                400, "That's not a resource status the vault knows.",
                f"'{body.status}' isn't one of {', '.join(notes.RESOURCE_LIFECYCLE)} "
                "(SCHEMA-REFERENCE.md §6).",
                "Advance to one of the lifecycle statuses.")
        try:
            return notes.set_resource_status(config.vault_path, note_id, body.status)
        except LookupError:
            raise Envelope(
                404, "That resource isn't in the vault.",
                "No resource note in 04-Resources has that id.",
                "Refresh the resource list.")

    @app.post("/api/resources/{note_id}/insight")
    def resource_insight(note_id: str, body: InsightBody, config=Depends(require_token)):
        try:
            return notes.set_resource_insight(config.vault_path, note_id, body.text)
        except LookupError:
            raise Envelope(
                404, "That resource isn't in the vault.",
                "No resource note in 04-Resources has that id.",
                "Refresh the resource list.")

    # ---- config (safe subset only — key values never leave the server) --------------

    def config_payload(config) -> dict:
        last_ig = None
        for row in service.events_list(db_path, None, 2000, None):
            if row["stage"] == "enrich" and "platform=instagram" in (row["message"] or ""):
                last_ig = row["timestamp"]
                break
        safe = integrations.safe_config(config)
        safe["enrichment"] = {
            "apify_token": bool(os.environ.get("APIFY_TOKEN")),
            "apify_actor_set": bool((config.raw.get("apify") or {}).get("actor_id")),
            "apify_last_call": last_ig,
            "youtube_keyless": True,
        }
        safe["push"] = push_mod.availability(config)   # presence booleans only
        return safe

    @app.get("/api/config")
    def get_config(config=Depends(require_token)):
        return config_payload(config)

    @app.put("/api/config")
    def put_config(body: ConfigBody, config=Depends(require_token)):
        try:
            integrations.write_config(config_path, config, body.model_dump(exclude_none=True))
        except integrations.ConfigError as e:
            raise Envelope(400, **e.envelope)
        integrations.bust_cache(app.state)
        # same shape as GET, re-read so the response reflects the write
        return config_payload(load_config())

    # ---- selfcheck + backup (Pass 5) ---------------------------------------------------

    @app.get("/api/selfcheck")
    def selfcheck_route(config=Depends(require_token)):
        # re-run live: paths can vanish after boot (disk unmounted mid-flight)
        return selfcheck.run(root)

    backups_dir = root / "backups"

    @app.post("/api/backup")
    def backup(config=Depends(require_token)):
        committed = notes.git_commit_vault(config.vault_path, "api: manual backup")
        copied = False
        if db_path.exists():
            backups_dir.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = backups_dir / f"events-{stamp}.db"
            import sqlite3
            src = sqlite3.connect(db_path)
            try:
                dst = sqlite3.connect(dest)
                try:
                    src.backup(dst)  # safe against a concurrently-writing watcher
                finally:
                    dst.close()
            finally:
                src.close()
            copied = True
        return {"ok": True, "at": datetime.now().isoformat(timespec="seconds"),
                "vault_committed": committed, "events_db_copied": copied}

    @app.get("/api/backup")
    def backup_status(config=Depends(require_token)):
        last_backup = None
        if backups_dir.is_dir():
            copies = sorted(backups_dir.glob("events-*.db"))
            if copies:
                last_backup = datetime.fromtimestamp(
                    copies[-1].stat().st_mtime).isoformat(timespec="seconds")
        last_commit = None
        head = subprocess.run(["git", "-C", str(config.vault_path), "log", "-1", "--format=%cI"],
                              capture_output=True, text=True)
        if head.returncode == 0 and head.stdout.strip():
            last_commit = head.stdout.strip()
        return {"last_backup": last_backup, "last_vault_commit": last_commit}

    # ---- vault git-sync (Pass H1 — the island fix) ---------------------------------

    @app.post("/api/vault/sync")
    def vault_sync_route(config=Depends(require_token)):
        from pipeline import vaultsync
        from pipeline.events import EventLog

        if vaultsync.remote_config(config) is None:
            raise Envelope(
                400, "Vault sync isn't configured.",
                "Neither VAULT_GIT_REMOTE nor vault_sync.remote in config.json is set.",
                "Set VAULT_GIT_REMOTE (and VAULT_GIT_TOKEN) as service variables, or "
                "vault_sync.remote in config.json, then try again.")
        result = vaultsync.sync(Path(config.vault_path), config)
        events = EventLog(db_path, Path(config.vault_path))
        try:
            events.log(str(config.vault_path), "vault_sync",
                      "ok" if result.status == "ok" else "failed",
                      message=f"status={result.status} ahead={result.ahead} behind={result.behind}"
                              + (f" — {result.detail}" if result.detail else ""))
        finally:
            events.close()
        integrations.bust_cache(app.state)
        return {"ok": result.status == "ok", "status": result.status, "detail": result.detail,
                "ahead": result.ahead, "behind": result.behind}

    # ---- integrations ----------------------------------------------------------------

    @app.get("/api/integrations")
    def get_integrations(fresh: int = 0, config=Depends(require_token)):
        return integrations.get_integrations(app.state, config, heartbeat_path, bool(fresh), db_path)

    @app.post("/api/integrations/engine")
    def set_engine(body: EngineBody, config=Depends(require_token)):
        try:
            integrations.write_config(config_path, config, {"engine": body.engine})
        except integrations.ConfigError as e:
            raise Envelope(400, **e.envelope)
        integrations.bust_cache(app.state)
        return {"ok": True, "engine": body.engine}

    @app.post("/api/integrations/ntfy/test")
    def ntfy_test(config=Depends(require_token)):
        try:
            integrations.send_test_push(config)
        except integrations.PushFailed as e:
            # the send was attempted and failed — the card must say so too
            app.state.integrations_state["ntfy_tested"] = "failed"
            integrations.bust_cache(app.state)
            raise Envelope(502, **e.envelope)
        except integrations.ConfigError as e:
            raise Envelope(400, **e.envelope)
        app.state.integrations_state["ntfy_tested"] = "ok"
        integrations.bust_cache(app.state)
        return {"ok": True}

    # ---- google (read + draft — the API has no send route, by rule 4) ------------

    @app.get("/api/google/connect")
    def google_connect(redirect_uri: str, config=Depends(require_token)):
        try:
            return {"url": google.begin_connect(app.state.google_states, redirect_uri)}
        except google.GoogleError as e:
            raise Envelope(e.status, **e.envelope)

    @app.get("/api/google/callback")
    def google_callback(state: str = "", code: str = "", error: str = ""):
        # No bearer here — this is Google's browser redirect. The one-time
        # state minted by /connect (which DID require the token) is the proof
        # this flow started from the cockpit.
        if error or not code:
            return HTMLResponse(_google_page(
                "Sign-in cancelled",
                "Google didn't complete the sign-in. Nothing was connected — "
                "you can close this tab and try again from Integrations."))
        try:
            google.finish_connect(app.state.google_states, config_path, state, code)
        except google.GoogleError as e:
            return HTMLResponse(_google_page(
                e.envelope["what"], f"{e.envelope['cause']} {e.envelope['todo']}"))
        app.state.google_tokens.clear()
        integrations.bust_cache(app.state)
        return HTMLResponse(_google_page(
            "Google connected",
            "Gmail and Calendar are now linked. Close this tab and head back "
            "to the cockpit — the Integrations screen will show them live."))

    @app.get("/api/google/inbox")
    def google_inbox(config=Depends(require_token)):
        try:
            return {"items": google.unread(config, app.state.google_tokens)}
        except google.GoogleError as e:
            raise Envelope(e.status, **e.envelope)

    @app.get("/api/google/events")
    def google_events(config=Depends(require_token)):
        try:
            return {"items": google.events(config, app.state.google_tokens)}
        except google.GoogleError as e:
            raise Envelope(e.status, **e.envelope)

    @app.post("/api/google/draft")
    def google_draft(body: DraftBody, config=Depends(require_token)):
        try:
            return google.create_draft(config, app.state.google_tokens,
                                       body.to, body.subject, body.text)
        except google.GoogleError as e:
            raise Envelope(e.status, **e.envelope)

    @app.post("/api/google/disconnect")
    def google_disconnect(config=Depends(require_token)):
        google.disconnect(config_path, app.state.google_tokens)
        integrations.bust_cache(app.state)
        return {"ok": True}

    # ---- static app shell (mounted last so /api/* wins) ------------------------------

    dist = app_root / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="app")
    else:
        @app.get("/")
        def no_frontend():
            return PlainTextResponse(
                "Brain Cockpit API is running, but the cockpit isn't built yet.\n"
                "Build it with: cd web && npm ci && npm run build — then restart.\n",
                status_code=200)

    # Watchdog (Pass 5): notices a stopped --loop and pushes once per 6h window.
    app.state.watchdog_thread = watchdog.start(
        db_path, heartbeat_path, lambda: config_mod.load(config_path))

    return app


def __getattr__(name: str):
    # `uvicorn api.main:app` resolves the app lazily (PEP 562), so the startup
    # self-check runs — and can refuse with its numbered list — at serve time,
    # while `from api.main import create_app` (tests) never trips it.
    if name == "app":
        return create_app()
    raise AttributeError(name)

