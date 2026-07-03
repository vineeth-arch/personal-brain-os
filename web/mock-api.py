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


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):  # CORS preflight
        self._send(204, {})

    def _authed(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def _route(self, method: str):
        path = self.path.split("?")[0]
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
            if path == "/api/run":
                print("RUN NOW")
                return self._send(202, {"started": True})

        return self._send(404, {"error": {
            "what": "The server doesn't know that request.",
            "cause": f"No route for {method} {path} — frontend and API contract may be out of sync.",
            "todo": "Check API-CONTRACT.md and update whichever side is behind.",
        }})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def log_message(self, fmt, *args):  # quieter default logging
        sys.stderr.write("mock-api: %s\n" % (fmt % args))


if __name__ == "__main__":
    print(f"mock-api on http://127.0.0.1:{PORT}  (token: {TOKEN})")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
