#!/usr/bin/env python3
"""Reference mock of API-CONTRACT.md — NOT the real API (that's Pass 2 in api/).

Stdlib only. Serves canned responses so the cockpit frontend can be developed
and verified without the backend. The real FastAPI app must match these shapes.

Usage:
    python3 web/mock-api.py [--port 8000] [--empty | --fail | --stale | --attention]

    --empty      inbox-zero everywhere (empty review queue, no failures)
    --attention  needs_review = 3 (Today shows the ATTENTION state)
    --stale      heartbeat 40 minutes old (Today shows the PROBLEM state)
    --fail       every endpoint answers 500 with a three-part error envelope

Auth: expects "Authorization: Bearer mock-token" on everything except /api/health.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8000
if "--port" in sys.argv:
    PORT = int(sys.argv[sys.argv.index("--port") + 1])
MODE_EMPTY = "--empty" in sys.argv
MODE_FAIL = "--fail" in sys.argv
MODE_STALE = "--stale" in sys.argv
MODE_ATTENTION = "--attention" in sys.argv
MODE_INT_DEGRADED = "--integrations-degraded" in sys.argv
MODE_INT_EMPTY = "--integrations-empty" in sys.argv

TOKEN = "mock-token"

now = datetime.now()
iso = lambda dt: dt.isoformat(timespec="seconds")

HEARTBEAT = iso(now - timedelta(minutes=40)) if MODE_STALE else iso(now - timedelta(minutes=2))
NEEDS_REVIEW = 0 if MODE_EMPTY else (3 if MODE_ATTENTION else 2)
# --stale isolates the heartbeat branch of the traffic light, so no failures there
FAILED_COUNT = 0 if (MODE_EMPTY or MODE_ATTENTION or MODE_STALE) else 1

REVIEW_ITEMS = [
    {
        "id": "20260703054000",
        "file": "00-Inbox/2026-07-03-morning-walk-thought.md",
        "title": "morning-walk-thought",
        "excerpt": (
            "Was thinking about how the brand voice for the Dubai project keeps "
            "drifting formal whenever we write case studies. Maybe the fix is a "
            "banned-words list rather than more tone guidelines — constraints beat "
            "aspirations when you're tired..."
        ),
        "suggested_type": "learning",
        "confidence": 0.70,
        "created": "2026-07-03",
    },
    {
        "id": "20260702213000",
        "file": "00-Inbox/2026-07-02-late-idea.md",
        "title": "late-idea",
        "excerpt": (
            "What if the weekly reflection auto-included the three notes I linked "
            "to most this week? Kind of a gravity map of attention..."
        ),
        "suggested_type": "musing",
        "confidence": 0.55,
        "created": "2026-07-02",
    },
    {
        "id": "20260702101500",
        "file": "00-Inbox/2026-07-02-podcast-mention.md",
        "title": "podcast-mention",
        "excerpt": (
            "That podcast guest mentioned a book about pricing psychology — "
            "something like 'priceless'? Worth checking whether it's the William "
            "Poundstone one..."
        ),
        "suggested_type": "resource",
        "confidence": 0.62,
        "created": "2026-07-02",
    },
][: NEEDS_REVIEW]

FAILED_ITEMS = (
    []
    if FAILED_COUNT == 0
    else [
        {
            "id": 42,
            "file": "2026-07-03-0512-voicememo.m4a",
            "timestamp": iso(now - timedelta(hours=4)),
            "error": {
                "what": "Could not transcribe the recording.",
                "cause": "whisper.cpp exited with an error — the audio file may be truncated.",
                "todo": "Play the file in _failed/; if it's intact, press Retry. If it's corrupt, re-record.",
            },
        }
    ]
)

EVENTS = []
_eid = 100
for i in range(18):
    _eid -= 1
    stage = ["intake", "transcribe", "classify", "route", "extract", "archive"][i % 6]
    status = "ok"
    plain = ""
    msg = f"{stage} finished"
    if not MODE_EMPTY and i == 2:
        status = "needs_review"
        msg = "type=learning confidence=0.62 by=llm"
    if FAILED_COUNT and i == 4:
        status = "failed"
        msg = "whisper.cpp exit 1"
        plain = (
            "What happened: Could not transcribe the recording.\n"
            "Likely cause: whisper.cpp exited with an error — the audio file may be truncated.\n"
            "What to do: Play the file in _failed/; if it's intact, press Retry."
        )
    EVENTS.append(
        {
            "id": _eid,
            "timestamp": iso(now - timedelta(minutes=7 * i)),
            "file": f"2026-07-0{(i % 3) + 1}-voicememo-{i}.m4a",
            "stage": stage,
            "status": status,
            "duration_ms": 400 + i * 37,
            "message": msg,
            "plain_english_error": plain,
        }
    )

STREAK_DAYS = []
today = date.today()
for i in range(29, -1, -1):
    d = today - timedelta(days=i)
    captured = (d.toordinal() % 7) not in (2,) if not MODE_EMPTY else (i < 12)
    STREAK_DAYS.append({"date": d.isoformat(), "captured": captured})
CURRENT_STREAK = 0
for day in reversed(STREAK_DAYS):
    if day["captured"]:
        CURRENT_STREAK += 1
    else:
        break

RESURFACED = (
    None
    if MODE_EMPTY
    else {
        "id": "20260214093000",
        "title": "constraints-beat-aspirations",
        "file": "02-Wiki/2026-02-14-constraints-beat-aspirations.md",
        "excerpt": (
            "A banned-words list changes writing faster than a tone-of-voice deck. "
            "Negative rules are checkable in the moment; aspirations require taste "
            "you don't have at 11pm."
        ),
        "type": "insight",
        "created": "2026-02-14",
    }
)

FAIL_ENVELOPE = {
    "error": {
        "what": "The pipeline database couldn't be read.",
        "cause": "The events.db file is locked by another process.",
        "todo": "Close other pipeline runs, then refresh. If it persists, restart the watcher.",
    }
}



# ---- Integrations (Pass 4) --------------------------------------------------
ENGINE = "whispercpp"  # module-level so the engine toggle is observable across requests
NTFY_TESTED = None      # None -> "ok" after a test push succeeds, "failed" after one fails

# The editable safe-config subset (GET/PUT /api/config). Module-level so a PUT
# from Settings is observable on the next GET, like the real API.
CONFIG = {
    "engine": ENGINE,
    "confidence_threshold": 0.7,
    "ntfy_url": "https://ntfy.sh",
    "ntfy_topic": "brain-cockpit",
    "providers": ["gemini-flash", "groq-llama-3.3-70b", "openrouter-free", "claude-haiku"],
    "keys": {"anthropic": True, "openai": not MODE_INT_DEGRADED},
    "enrichment": {
        "apify_token": not MODE_INT_DEGRADED,
        "apify_actor_set": not MODE_INT_DEGRADED,
        "apify_last_call": None if MODE_EMPTY else iso(now - timedelta(hours=6)),
        "youtube_keyless": True,
    },
}

PROVIDERS = [] if MODE_EMPTY else [
    {"provider": "gemini-flash", "served": 41, "fell_through": 3, "invalid_json": 2, "avg_confidence": 0.84},
    {"provider": "groq-llama-3.3-70b", "served": 2, "fell_through": 1, "invalid_json": 0, "avg_confidence": 0.71},
    {"provider": "claude-haiku", "served": 1, "fell_through": 0, "invalid_json": 0, "avg_confidence": 0.9},
]

BUILD_ITEMS = [
    {"id": "pass1", "label": "Pass 1 — pipeline core", "phase": "Build passes",
     "done": True, "detail": "pipeline/watcher.py exists.", "next_action": None},
    {"id": "wire-whisper", "label": "whisper.cpp installed and runnable", "phase": "Wiring",
     "done": not MODE_INT_DEGRADED, "detail": "Probe of the configured binary.",
     "next_action": None if not MODE_INT_DEGRADED
     else "brew install whisper-cpp, download the small.en model, put both paths in config.json."},
]

TODO_ITEMS = [] if MODE_EMPTY else [
    {"id": "20260703140000-1", "task": "call the dentist", "due": date.today().isoformat(),
     "time": "14:00", "done": False, "overdue": False,
     "file": f"06-Todos/{date.today().isoformat()}.md"},
]

LINK_CARDS = [
    {"id": "obsidian", "group": "link", "name": "Obsidian", "icon": "obsidian",
     "description": "Open your vault in Obsidian.", "status": "unknown", "badge": None,
     "url": "obsidian://open?vault=Brain"},
    {"id": "dex", "group": "link", "name": "Dex", "icon": "link",
     "description": "Your personal CRM for people and relationships.", "status": "unknown",
     "badge": None, "url": "https://getdex.com/"},
    {"id": "gmail", "group": "link", "name": "Gmail", "icon": "mail",
     "description": "Email inbox.", "status": "unknown", "badge": None,
     "url": "https://mail.google.com/"},
    {"id": "gcal", "group": "link", "name": "Google Calendar", "icon": "calendar",
     "description": "Your calendar.", "status": "unknown", "badge": None,
     "url": "https://calendar.google.com/"},
    {"id": "caldiy", "group": "link", "name": "cal.diy", "icon": "calendar",
     "description": "Scheduling links.", "status": "unknown", "badge": None,
     "url": "https://cal.diy/"},
    {"id": "n8n", "group": "link", "name": "n8n", "icon": "server",
     "description": "Your automation workflows.", "status": "unknown", "badge": None,
     "url": "http://zimaos.local:5678/"},
    {"id": "zima", "group": "link", "name": "ZimaOS admin", "icon": "server",
     "description": "Home-server dashboard.", "status": "unknown", "badge": None,
     "url": "http://zimaos.local/"},
    {"id": "supabase", "group": "link", "name": "Supabase", "icon": "database",
     "description": "Outreach cockpit database console.", "status": "unknown", "badge": None,
     "url": "https://app.supabase.com/project/_"},
    # an unknown links key — exercises the frontend's lettermark icon fallback
    {"id": "notion", "group": "link", "name": "Notion", "icon": "notion",
     "description": "Pinned from the links section of config.json.", "status": "unknown",
     "badge": None, "url": "https://www.notion.so/"},
]


def _integration_cards():
    degraded = MODE_INT_DEGRADED
    whisper_active = ENGINE == "whispercpp"
    openai_active = ENGINE == "openai"

    whisper = {
        "id": "transcription-whispercpp", "group": "health",
        "name": "Transcription — whisper.cpp", "icon": "waveform",
        "description": "Turns your voice memos into text, all on this machine.",
    }
    if degraded:
        whisper.update(status="problem", badge="Not found",
                       detail="The whisper.cpp binary path is missing or not executable.",
                       error={
                           "what": "Local transcription can't run.",
                           "cause": "The whisper.cpp binary path in config.json is missing or wrong.",
                           "todo": "Set transcription.whispercpp.binary_path to your whisper-cli binary, then Recheck."},
                       meta={"engine_active": whisper_active})
    else:
        whisper.update(status="ok",
                       badge="Ready · active" if whisper_active else "Ready",
                       detail=("Local transcription is ready and is the engine in use."
                               if whisper_active else "Local transcription is ready (not the active engine)."),
                       meta={"model": "ggml-base.en.bin", "engine_active": whisper_active})

    openai = {
        "id": "transcription-openai", "group": "health",
        "name": "Transcription — OpenAI", "icon": "cloud",
        "description": "Cloud fallback that sends audio to OpenAI to transcribe.",
        "status": "ok" if openai_active else "unknown",
        "badge": "In use" if openai_active else "Key set · untested",
        "detail": ("OpenAI is the active engine." if openai_active
                   else "OPENAI_API_KEY is set but hasn't been test-called. Fallback only, not active."),
        "meta": {"key_present": True, "engine_active": openai_active},
    }

    claude = {
        "id": "claude", "group": "health", "name": "Claude API", "icon": "brain",
        "description": "Classifies untagged captures into the right note type.",
        "status": "ok", "badge": "Ready",
        "detail": "Last test call to Claude Haiku succeeded 2 minutes ago.",
        "meta": {"key_present": True},
    }

    ntfy = {
        "id": "ntfy", "group": "health", "name": "ntfy push", "icon": "bell",
        "description": "Sends one push to your phone when a capture fails.",
        "meta": {"topic": CONFIG["ntfy_topic"]},
    }
    if NTFY_TESTED == "failed":
        ntfy.update(status="warn", badge="Test failed",
                    detail="The last test push couldn't reach the ntfy server.",
                    error={
                        "what": "The test push didn't go out.",
                        "cause": "The ntfy server couldn't be reached from this machine "
                                 "(network down, blocked, or the url is wrong).",
                        "todo": "Check ntfy.url and this machine's connection, then try again."})
    elif NTFY_TESTED == "ok":
        ntfy.update(status="ok", badge="Delivered",
                    detail="Test push sent \u2014 your phone should have buzzed.")
    else:
        ntfy.update(status="unknown", badge="Untested",
                    detail="Configured for topic \u201cbrain-cockpit\u201d. "
                           "Send a test push to confirm your phone receives it.")

    vault = {
        "id": "vault-sync", "group": "health", "name": "Vault sync", "icon": "folder-sync",
        "description": "Where notes live and where new captures land.",
        "status": "ok", "badge": "Reachable",
        "detail": "Inbox and vault are both reachable. Last file activity 3 minutes ago.",
        "meta": {"minutes_since_activity": 3},
    }

    git = {
        "id": "git", "group": "health", "name": "Vault git backup", "icon": "git",
        "description": "Keeps every change to your notes revertible.",
    }
    if degraded:
        git.update(status="warn", badge="Uncommitted 30h",
                   detail="The vault has uncommitted changes that are 30 hours old.",
                   error={
                       "what": "Your vault has changes that haven't been backed up in over a day.",
                       "cause": "The vault git repo has uncommitted edits older than 24 hours.",
                       "todo": "Run a backlog pass (it commits before writing), or commit the vault manually."},
                   meta={"dirty": True, "commit_age_hours": 30})
    else:
        git.update(status="ok", badge="Clean",
                   detail="Vault committed 2 hours ago. Nothing uncommitted.",
                   meta={"dirty": False, "commit_age_hours": 2})

    watcher = {
        "id": "watcher", "group": "health", "name": "Watcher", "icon": "pulse",
        "description": "The background process that picks up new captures.",
    }
    if degraded:
        watcher.update(status="problem", badge="Stale 42m",
                       detail="The watcher last checked in 42 minutes ago.",
                       error={
                           "what": "The pipeline hasn't checked in for 42 minutes.",
                           "cause": "The watcher process isn't running, or it crashed on the last pass.",
                           "todo": "Restart it: python3 -m pipeline --loop. Then press Recheck."},
                       meta={"heartbeat_age_min": 42})
    else:
        watcher.update(status="ok", badge="Live 2m",
                       detail="The watcher checked in 2 minutes ago.",
                       meta={"heartbeat_age_min": 2})

    health = [whisper, openai, claude, ntfy, vault, git, watcher]
    links = [] if MODE_INT_EMPTY else LINK_CARDS
    return health + links


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):  # CORS preflight
        self._send(204, {})

    def _authed(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def _route(self, method: str):
        path = self.path.split("?")[0]
        global ENGINE, NTFY_TESTED
        if path == "/api/health":
            return self._send(200, {"ok": True})
        if not self._authed():
            return self._send(
                401,
                {"error": {
                    "what": "The server rejected the access token.",
                    "cause": "The token doesn't match api.auth_token in config.json.",
                    "todo": "Re-enter the token from your config.",
                }},
            )
        if MODE_FAIL:
            return self._send(500, FAIL_ENVELOPE)

        if method == "GET":
            if path == "/api/status":
                return self._send(200, {
                    "vault": "Brain",
                    "engine": "whispercpp",
                    "heartbeat": HEARTBEAT,
                    "last_run": HEARTBEAT,
                    "counts": {
                        "pending": 0 if MODE_EMPTY else 2,
                        "processed_today": 0 if MODE_EMPTY else 5,
                        "needs_review": len(REVIEW_ITEMS),
                        "failed": len(FAILED_ITEMS),
                    },
                })
            if path == "/api/review":
                return self._send(200, {"items": REVIEW_ITEMS})
            if path == "/api/failed":
                return self._send(200, {"items": FAILED_ITEMS})
            if path == "/api/events":
                q = self.path.split("?")[1] if "?" in self.path else ""
                params = dict(p.split("=", 1) for p in q.split("&") if "=" in p)
                events = EVENTS
                if params.get("status"):
                    events = [e for e in events if e["status"] == params["status"]]
                return self._send(200, {"events": events[: int(params.get("limit", 100))]})
            if path == "/api/streak":
                return self._send(200, {"current": CURRENT_STREAK, "days": STREAK_DAYS})
            if path == "/api/resurfaced":
                return self._send(200, {"note": RESURFACED})
            if path == "/api/integrations":
                q = self.path.split("?")[1] if "?" in self.path else ""
                params = dict(p.split("=", 1) for p in q.split("&") if "=" in p)
                return self._send(200, {
                    "engine": ENGINE,
                    "generated_at": iso(datetime.now()),
                    "fresh": params.get("fresh") == "1",
                    "cards": _integration_cards(),
                })
            if path == "/api/config":
                return self._send(200, {**CONFIG, "engine": ENGINE})
            if path == "/api/providers":
                return self._send(200, {"providers": PROVIDERS})
            if path == "/api/build":
                unfinished = next((i for i in BUILD_ITEMS if not i["done"]), None)
                return self._send(200, {
                    "generated_at": iso(datetime.now()),
                    "next": ({"label": unfinished["label"], "next_action": unfinished["next_action"]}
                             if unfinished else None),
                    "items": BUILD_ITEMS,
                })
            if path == "/api/todos":
                return self._send(200, {"items": TODO_ITEMS})
            if path == "/api/selfcheck":
                return self._send(200, {
                    "ok": not MODE_INT_DEGRADED,
                    "problems": [] if not MODE_INT_DEGRADED else [{
                        "what": "The vault folder can't be used.",
                        "cause": "vault_path in config.json points to a folder that "
                                 "doesn't exist or isn't writable.",
                        "todo": "Create the folder or fix vault_path in config.json."}],
                    "checks": [
                        {"id": "config", "label": "config.json readable", "ok": True,
                         "detail": "Parsed."},
                        {"id": "path-vault", "label": "vault folder writable",
                         "ok": not MODE_INT_DEGRADED,
                         "detail": "/vault" if not MODE_INT_DEGRADED
                         else "/vault is missing or not writable."},
                        {"id": "events-db", "label": "events.db opens", "ok": True,
                         "detail": "Opens fine."},
                        {"id": "whisper", "label": "whisper.cpp configured",
                         "ok": not MODE_INT_DEGRADED,
                         "detail": "Binary path set." if not MODE_INT_DEGRADED
                         else "No binary path yet — transcription can't run."},
                    ],
                })
            if path == "/api/backup":
                return self._send(200, {
                    "last_backup": None if MODE_EMPTY else iso(now - timedelta(hours=20)),
                    "last_vault_commit": None if MODE_EMPTY else iso(now - timedelta(hours=2)),
                })

        if method == "PUT":
            if path == "/api/config":
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                try:
                    changes = json.loads(body or b"{}")
                except json.JSONDecodeError:
                    changes = {}
                engine = changes.get("engine")
                if engine is not None:
                    if engine not in ("whispercpp", "openai"):
                        return self._send(400, {"error": {
                            "what": "Couldn't switch the transcription engine.",
                            "cause": "The request didn't name a known engine (whispercpp or openai).",
                            "todo": "Pick one of the two engines and try again."}})
                    if engine == "openai" and not CONFIG["keys"]["openai"]:
                        return self._send(400, {"error": {
                            "what": "Can't switch to cloud transcription.",
                            "cause": "OPENAI_API_KEY is not set on the server, so the OpenAI engine can't run.",
                            "todo": "export OPENAI_API_KEY=... in the server's shell, or stay on whispercpp."}})
                    ENGINE = engine
                for field in ("confidence_threshold", "ntfy_url", "ntfy_topic"):
                    if changes.get(field) is not None:
                        CONFIG[field] = changes[field]
                print("PUT CONFIG", changes)
                return self._send(200, {**CONFIG, "engine": ENGINE})

        if method == "POST":
            if path == "/api/capture":
                print("CAPTURE", self.rfile.read(int(self.headers.get("Content-Length", 0))))
                return self._send(201, {"id": "20260703061500", "status": "captured"})
            if path.startswith("/api/review/") and path.endswith("/approve"):
                note_id = path.split("/")[3]
                print("APPROVE", note_id, self.rfile.read(int(self.headers.get("Content-Length", 0))))
                for item in list(REVIEW_ITEMS):
                    if item["id"] == note_id:
                        REVIEW_ITEMS.remove(item)
                return self._send(200, {"ok": True, "moved_to": "02-Wiki/approved-note.md"})
            if path.startswith("/api/failed/") and path.endswith("/retry"):
                print("RETRY", path.split("/")[3])
                FAILED_ITEMS.clear()
                return self._send(200, {"ok": True})
            if path == "/api/integrations/engine":
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                try:
                    engine = json.loads(body or b"{}").get("engine")
                except json.JSONDecodeError:
                    engine = None
                if engine not in ("whispercpp", "openai"):
                    return self._send(400, {"error": {
                        "what": "Couldn't switch the transcription engine.",
                        "cause": "The request didn't name a known engine (whispercpp or openai).",
                        "todo": "Pick one of the two engines and try again."}})
                if engine == "openai" and not CONFIG["keys"]["openai"]:
                    return self._send(400, {"error": {
                        "what": "Can't switch to cloud transcription.",
                        "cause": "OPENAI_API_KEY is not set on the server, so the OpenAI engine can't run.",
                        "todo": "export OPENAI_API_KEY=... in the server's shell, or stay on whispercpp."}})
                ENGINE = engine
                print("SET ENGINE", engine)
                return self._send(200, {"ok": True, "engine": engine})
            if path == "/api/integrations/ntfy/test":
                print("NTFY TEST")
                if MODE_INT_DEGRADED:
                    # the send was attempted and failed — card shows the warn state
                    NTFY_TESTED = "failed"
                    return self._send(502, {"error": {
                        "what": "The test push didn't go out.",
                        "cause": "The ntfy server couldn't be reached from this machine "
                                 "(network down, blocked, or the url is wrong).",
                        "todo": "Check ntfy.url and this machine's connection, then try again."}})
                NTFY_TESTED = "ok"
                return self._send(200, {"ok": True})
            if path == "/api/run":
                print("RUN NOW")
                return self._send(202, {"started": True})
            if path == "/api/backup":
                print("BACKUP NOW")
                return self._send(200, {"ok": True, "at": iso(datetime.now()),
                                        "vault_committed": True, "events_db_copied": True})

        return self._send(404, {"error": {
            "what": "The server doesn't know that request.",
            "cause": f"No route for {method} {path} — frontend and API contract may be out of sync.",
            "todo": "Check API-CONTRACT.md and update whichever side is behind.",
        }})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def log_message(self, fmt, *args):  # quieter default logging
        sys.stderr.write("mock-api: %s\n" % (fmt % args))


if __name__ == "__main__":
    print(f"mock-api on http://127.0.0.1:{PORT}  (token: {TOKEN})")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
