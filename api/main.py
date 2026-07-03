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
import secrets
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from pipeline import classify, config as config_mod, intake, watcher

from . import integrations, notes, service

log = logging.getLogger("api")
logging.basicConfig(level=logging.INFO)

# repo root by default; BRAIN_COCKPIT_ROOT overrides it (deploy/test knob)
DEFAULT_ROOT = Path(os.environ.get("BRAIN_COCKPIT_ROOT") or Path(__file__).resolve().parents[1])


class Envelope(Exception):
    """Raise anywhere in a handler to return a plain-English error."""

    def __init__(self, status: int, what: str, cause: str, todo: str):
        self.status = status
        self.body = {"error": {"what": what, "cause": cause, "todo": todo}}
        super().__init__(what)


def _generic_envelope(status: int) -> dict:
    if status == 404:
        return {"error": {
            "what": "The server doesn't know that request.",
            "cause": "No route matches — the app and the API may be out of sync.",
            "todo": "Check API-CONTRACT.md and update whichever side is behind."}}
    if status == 405:
        return {"error": {
            "what": "That request used the wrong method.",
            "cause": "The route exists but not for this HTTP verb.",
            "todo": "Check API-CONTRACT.md for the route's method."}}
    return {"error": {
        "what": "The server couldn't complete that request.",
        "cause": f"It hit an unexpected condition (HTTP {status}).",
        "todo": "Try again; if it keeps happening, check the server log."}}


class ApproveBody(BaseModel):
    type: str


class CaptureBody(BaseModel):
    text: str
    tag: str | None = None


class EngineBody(BaseModel):
    engine: str


class ConfigBody(BaseModel):
    engine: str | None = None
    confidence_threshold: float | None = None
    ntfy_topic: str | None = None
    ntfy_url: str | None = None


def create_app(root: Path | None = None) -> FastAPI:
    root = Path(root or DEFAULT_ROOT)
    app = FastAPI(title="Brain Cockpit API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.root = root
    app.state.run_proc = None
    app.state.run_lock = threading.Lock()
    app.state.integrations_cache = {}
    app.state.integrations_state = {}

    config_path = root / "config.json"
    db_path = root / watcher.DB_PATH
    heartbeat_path = root / watcher.HEARTBEAT_PATH

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
            "todo": "Try again; if it repeats, check the server log for the traceback."}},
            status_code=500)

    # ---- read routes -------------------------------------------------------------

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/status")
    def status(config=Depends(require_token)):
        heartbeat = None
        if heartbeat_path.exists():
            heartbeat = heartbeat_path.read_text().strip() or None
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
        return {"items": notes.list_review(config.vault_path, db_path)}

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
        return {"note": notes.resurface(config.vault_path)}

    # ---- write routes (each git-commits the vault) ---------------------------------

    @app.post("/api/review/{note_id}/approve")
    def approve(note_id: str, body: ApproveBody, config=Depends(require_token)):
        if body.type not in classify.NOTE_TYPES:
            raise Envelope(
                400, "That's not a note type the vault knows.",
                f"'{body.type}' isn't one of the 11 types in SCHEMA-REFERENCE.md.",
                "Pick one of the type chips and try again.")
        try:
            moved_to = notes.approve(config.vault_path, note_id, body.type)
        except LookupError:
            raise Envelope(
                404, "That note isn't waiting for review anymore.",
                "It was already approved (possibly from another device), or the id is unknown.",
                "Refresh the triage queue.")
        return {"ok": True, "moved_to": moved_to}

    @app.post("/api/capture", status_code=201)
    def capture(body: CaptureBody, config=Depends(require_token)):
        text = body.text.strip()
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
            app.state.run_proc = subprocess.Popen(
                [sys.executable, "-m", "pipeline"], cwd=root,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"started": True}

    # ---- config (safe subset only — key values never leave the server) --------------

    @app.get("/api/config")
    def get_config(config=Depends(require_token)):
        return integrations.safe_config(config)

    @app.put("/api/config")
    def put_config(body: ConfigBody, config=Depends(require_token)):
        try:
            integrations.write_config(config_path, config, body.model_dump(exclude_none=True))
        except integrations.ConfigError as e:
            raise Envelope(400, **e.envelope)
        integrations.bust_cache(app.state)
        return integrations.safe_config(load_config())

    # ---- integrations ----------------------------------------------------------------

    @app.get("/api/integrations")
    def get_integrations(fresh: int = 0, config=Depends(require_token)):
        return integrations.get_integrations(app.state, config, heartbeat_path, bool(fresh))

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
        except integrations.ConfigError as e:
            raise Envelope(400, **e.envelope)
        app.state.integrations_state["ntfy_tested"] = True
        integrations.bust_cache(app.state)
        return {"ok": True}

    # ---- static app shell (mounted last so /api/* wins) ------------------------------

    dist = root / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="app")
    else:
        @app.get("/")
        def no_frontend():
            return PlainTextResponse(
                "Brain Cockpit API is running, but the cockpit isn't built yet.\n"
                "Build it with: cd web && npm ci && npm run build — then restart.\n",
                status_code=200)

    return app


app = create_app()

