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
from urllib.parse import unquote
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
# Google (Pass 12): default = client configured but not linked yet;
# --google-connected = live cards; --google-unconfigured = plain link tiles.
MODE_GOOGLE_CONNECTED = "--google-connected" in sys.argv
MODE_GOOGLE_UNCONFIGURED = "--google-unconfigured" in sys.argv
# Pass H1: default = not configured (the common case); --vault-sync-configured
# renders the "Up to date" state instead.
MODE_VAULT_SYNC_CONFIGURED = "--vault-sync-configured" in sys.argv

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
        "file": "wiki/2026-02-14-constraints-beat-aspirations.md",
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



# Note type → folder, mirroring pipeline/route.py TYPE_FOLDER (keep in sync).
# --- people (Pass MW) --------------------------------------------------------
def _person(pid, name, relationship, company, stage, cadence, days, action="", channels=None,
            dex_id=""):
    return {
        "id": pid, "name": name, "relationship": relationship, "company": company,
        "dex_id": dex_id, "dex_deeplink": "",
        "warmth_stage": stage, "status": "active", "cadence_days": cadence,
        "last_contact": None if days is None else "2026-07-20",
        "days_since_contact": days,
        "going_cold": days is None or days >= cadence,
        "warmup_due": stage not in ("warm", "ready") and (days is None or days >= cadence),
        "commitment_due": bool(action),
        "channels": channels if channels is not None
        else {"whatsapp": "+971500000001", "email": "priya@example.com"},
        "next_action": action, "sample": True, "file": f"2026-07-01-{pid}.md",
    }


PEOPLE = [
    _person("20260701090100", "Priya Raman", "client", "Alserkal Avenue",
            "conversing", 3, 24, "Send the studio deck today", dex_id="dex-priya"),
    _person("20260701090200", "Omar Haddad", "prospect", "Tashkeel", "researched", 5, 12,
            channels={"email": "omar@example.com"}),
    _person("20260701090300", "Aisha Noor", "prospect", "Dubai Design District",
            "identified", 7, None, channels={"linkedin": "aishanoor"}),
    _person("20260701090400", "Tomás Ferreira", "client", "Casa Ferreira", "ready", 14, 2),
]

PEOPLE_DETAIL_EXTRA = {
    "context": "Met at a studio visit in Alserkal. Runs the artist programme.",
    "needs": "A studio partner who can hold a full season.",
    "interaction_log": "- 2026-07-20 — spoke about the season programme",
}

VOICE = {"exists": False, "file": "_System/my-voice.md", "samples": 0}

# --- profile push (Pass D) ---------------------------------------------------
# Mirrors pipeline/dex.py: the app owns what is between these markers and
# nothing else in the field.
PUSH_MARKER_OPEN = "<!-- BRAIN-OS -->"
PUSH_MARKER_CLOSE = "<!-- /BRAIN-OS -->"
PUSHED: set[str] = set()      # who has been pushed this mock session

# mirrors api/notes.py AUDIO_MIME_EXT — what the mic button may upload
AUDIO_MIME_TYPES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/m4a",
                    "audio/x-m4a", "audio/mpeg", "audio/wav", "audio/x-wav"}

# mirrors api/notes.py IMAGE_MIME_EXT — what the photo button/Shortcut may upload
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}

TYPE_FOLDER = {
    "journal": "01-Journal", "musing": "02-Musings", "learning": "03-Learnings",
    "insight": "wiki", "resource": "04-Resources", "project": "05-Projects",
    "todo": "06-Todos", "person": "07-People", "reflection": "08-Reflections",
    "decision": "09-Decisions", "principle": "10-Principles",
}

# ---- Integrations (Pass 4) --------------------------------------------------
ENGINE = "whispercpp"  # module-level so the engine toggle is observable across requests
NTFY_TESTED = None      # None -> "ok" after a test push succeeds, "failed" after one fails
VAULT_SYNCED = MODE_VAULT_SYNC_CONFIGURED   # flips true after a successful mock sync

# The editable safe-config subset (GET/PUT /api/config). Module-level so a PUT
# from Settings is observable on the next GET, like the real API.
CONFIG = {
    "engine": ENGINE,
    "language": "",
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
    # Dex on, contacts off — so the working push AND the honest "reconnect
    # Google" pill are both visible without any real key.
    "push": {"dex": True, "contacts_scope": False},
    "transliteration": {
        "engine": "", "ollama_url": "", "ollama_model": "",
        "openrouter_model": "", "openrouter_key_present": False,
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

# Search fixtures (Pass Q) — a static set standing in for a real whole-vault
# scan; each entry names which field the fixture's own `q` matched, since the
# mock has no real vault to search.
SEARCH_ITEMS = [] if MODE_EMPTY else [
    {"id": "20260703140000", "type": "resource", "title": "Weeknight dal",
     "file": "04-Resources/2026-07-03-weeknight-dal.md", "folder": "04-Resources",
     "excerpt": "Weeknight dal", "matched_in": "title"},
    {"id": "20260701090000", "type": "person", "title": "Priya Raman",
     "file": "07-People/2026-07-01-priya-raman.md", "folder": "07-People",
     "excerpt": "- 2026-06-01 — coffee at Alserkal, talked about the studio residency",
     "matched_in": "body"},
    {"id": "20260620090000", "type": "learning", "title": "Spaced repetition retrieval",
     "file": "03-Learnings/2026-06-20-spaced-repetition.md", "folder": "03-Learnings",
     "excerpt": "…the trellis pattern maps neatly onto retrieval practice…",
     "matched_in": "body"},
]

# Resource OS fixtures (Pass 6, mocked in Pass H) — the six /api/resources*
# routes existed since Pass 6 but had no mock coverage, so the Resources
# screen couldn't be driven against `mock-api.py` at all until now.
RESOURCE_ITEMS = [] if MODE_EMPTY else [
    {"id": "20260703140000", "title": "Weeknight dal", "category": "recipe",
     "status": "to-consume", "cover": "https://picsum.photos/seed/dal/400/560",
     "url": "https://example.com/dal", "created": "2026-07-03", "sample": False,
     "file": f"{TYPE_FOLDER['resource']}/2026-07-03-weeknight-dal.md",
     "has_insight": True, "insight": "Halve the chili next time.",
     "description": "A quick weeknight lentil curry.", "rating": None,
     "author": None, "where_to_watch": None, "runtime": None,
     "ingredients": "1 cup red lentils, 1 onion, 2 tsp chili powder",
     "steps": "Simmer lentils; fry onion + spices; combine.",
     "tools_mentioned": None, "transcript": None, "map_url": None, "best_time": None,
     "sections": [{"heading": "Insight", "text": "Halve the chili next time."}]},
    {"id": "20260701090000", "title": "Kepano's PKM talk", "category": "tutorial",
     "status": "inbox", "cover": None,
     "url": "https://www.youtube.com/watch?v=abc123def45", "created": "2026-07-01",
     "sample": True, "file": f"{TYPE_FOLDER['resource']}/2026-07-01-kepanos-pkm-talk.md",
     "has_insight": False, "insight": None,
     "description": "How Kepano structures his Obsidian vault.", "rating": None,
     "author": None, "where_to_watch": None, "runtime": None, "ingredients": None,
     "steps": None, "tools_mentioned": "Obsidian, Bases", "transcript": None,
     "map_url": None, "best_time": None, "sections": []},
]

# Google fixture data (Pass 12) — served to the live Gmail/Calendar cards.
GMAIL_MESSAGES = [
    {"id": "m1", "from": "Priya Raman <priya@example.com>",
     "subject": "Re: studio visit on Thursday", "date": iso(now - timedelta(hours=2)),
     "snippet": "Works for me — I'll bring the prints.",
     "url": "https://mail.google.com/mail/u/0/#inbox/m1"},
    {"id": "m2", "from": "billing@hetzner.com",
     "subject": "Your invoice for August", "date": iso(now - timedelta(hours=9)),
     "snippet": "Invoice 2026-08 is available.",
     "url": "https://mail.google.com/mail/u/0/#inbox/m2"},
    {"id": "m3", "from": "Anand <anand@example.com>",
     "subject": "book recommendation", "date": iso(now - timedelta(days=1)),
     "snippet": "The one I mentioned is Designing Brand Identity.",
     "url": "https://mail.google.com/mail/u/0/#inbox/m3"},
]

CALENDAR_EVENTS = [
    {"id": "e1", "summary": "Studio visit", "start": iso(now + timedelta(days=1, hours=3)),
     "end": iso(now + timedelta(days=1, hours=4)), "all_day": False,
     "location": "Alserkal Avenue", "url": "https://calendar.google.com/e1"},
    {"id": "e2", "summary": "Dentist", "start": iso(now + timedelta(days=3)),
     "end": iso(now + timedelta(days=3, hours=1)), "all_day": False,
     "location": "", "url": "https://calendar.google.com/e2"},
]

LINK_CARDS = [
    {"id": "obsidian", "group": "link", "name": "Obsidian", "icon": "obsidian",
     "description": "Open your vault in Obsidian.", "status": "unknown", "badge": None,
     "url": "obsidian://open?vault=Brain"},
    {"id": "dex", "group": "link", "name": "Dex", "icon": "link",
     "description": "Your personal CRM for people and relationships.", "status": "unknown",
     "badge": None, "url": "https://getdex.com/"},
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

    vault_git_sync = {
        "id": "vault-git-sync", "group": "health", "name": "Vault git-sync", "icon": "cloud-sync",
        "description": "Pushes/pulls the vault's git history to a private repo, so every "
                       "machine that opens it has the same notes.",
    }
    if VAULT_SYNCED:
        vault_git_sync.update(status="ok", badge="Up to date",
                              detail="Last synced 2 minutes ago. Nothing pending.",
                              meta={"branch": "main", "ahead": 0, "behind": 0})
    else:
        vault_git_sync.update(
            status="unknown", badge="Not configured",
            detail="No remote is set — fine on a single machine, essential once more than "
                   "one opens this vault.",
            error={
                "what": "This vault only lives on this machine.",
                "cause": "Neither VAULT_GIT_REMOTE (env) nor vault_sync.remote (config.json) is set.",
                "todo": "Create a private GitHub repo, set VAULT_GIT_REMOTE + VAULT_GIT_TOKEN "
                        "(or vault_sync.remote in config.json), then press Sync now.",
            })

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

    health = [whisper, openai, claude, ntfy, vault, git, vault_git_sync, watcher]
    links = [] if MODE_INT_EMPTY else LINK_CARDS
    return health + _google_cards() + links


def _google_cards():
    """Gmail + Calendar, mirroring api/integrations.py::_google_cards."""
    if MODE_INT_EMPTY:
        return []
    specs = [
        ("gmail", "Gmail", "mail",
         "Recent unread mail, and drafts you write here and send from Gmail.",
         "https://mail.google.com/"),
        ("gcal", "Google Calendar", "calendar",
         "What's on in the next seven days.",
         "https://calendar.google.com/"),
    ]
    cards = []
    for card_id, name, icon, description, url in specs:
        if MODE_GOOGLE_UNCONFIGURED:
            cards.append({"id": card_id, "group": "link", "name": name, "icon": icon,
                          "description": description, "status": "unknown",
                          "badge": None, "url": url})
            continue
        card = {"id": card_id, "group": "google", "name": name, "icon": icon,
                "description": description, "url": url,
                "meta": {"configured": True, "connected": MODE_GOOGLE_CONNECTED}}
        if MODE_GOOGLE_CONNECTED:
            card.update(status="ok", badge="Connected",
                        detail="Linked to your Google account.")
        else:
            card.update(status="unknown", badge="Not connected",
                        detail="The server has a Google client — connect your account to go live.",
                        error={"what": f"{name} isn't showing live data yet.",
                               "cause": "No Google account has been linked to this cockpit.",
                               "todo": "Press Connect Google on this card and approve the access."})
        cards.append(card)
    return cards


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

    def _json_body(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self):  # CORS preflight
        self._send(204, {})

    def _authed(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def _route(self, method: str):
        path = self.path.split("?")[0]
        global ENGINE, NTFY_TESTED, VAULT_SYNCED
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
            if path == "/api/people":
                items = [] if MODE_EMPTY else PEOPLE
                return self._send(200, {"items": items})
            if path == "/api/people/voice":
                return self._send(200, VOICE)
            if path == "/api/push/queue":
                # staged, never pushed: whoever has a dex_id and hasn't been
                # pushed yet this session
                items = [] if MODE_EMPTY else [
                    {**p, "targets": ["dex"], "last_pushed": None}
                    for p in PEOPLE if p["dex_id"] and p["id"] not in PUSHED
                ]
                return self._send(200, {"items": items, "available": CONFIG["push"]})
            if path.startswith("/api/people/"):
                pid = path.split("/")[3]
                found = next((p for p in PEOPLE if p["id"] == pid), None)
                if not found:
                    return self._send(404, {"error": {
                        "what": "That person isn't in the vault.",
                        "cause": f"No note in 07-People has the id {pid}.",
                        "todo": "Refresh the People screen."}})
                return self._send(200, {**found, **PEOPLE_DETAIL_EXTRA})
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
            if path == "/api/search":
                q = self.path.split("?")[1] if "?" in self.path else ""
                params = dict(p.split("=", 1) for p in q.split("&") if "=" in p)
                needle = unquote(params.get("q", "")).strip()
                if len(needle) < 2:
                    return self._send(400, {"error": {
                        "what": "That search is too short to be useful.",
                        "cause": "Search needs at least 2 characters.",
                        "todo": "Type a bit more, then search again."}})
                needle_lower = needle.lower()
                hits = [item for item in SEARCH_ITEMS
                       if needle_lower in item["title"].lower()
                       or needle_lower in item["excerpt"].lower()]
                limit = int(params.get("limit", 50) or 50)
                return self._send(200, {"items": hits[:limit]})
            if path == "/api/resources":
                q = self.path.split("?")[1] if "?" in self.path else ""
                params = dict(p.split("=", 1) for p in q.split("&") if "=" in p)
                if params.get("sort") not in (None, "created", "oldest", "title"):
                    return self._send(400, {"error": {
                        "what": "That's not a sort the resource list knows.",
                        "cause": f"'{params.get('sort')}' isn't one of created, oldest, title.",
                        "todo": "Use one of the three sort values, or omit it for newest-first."}})
                items = RESOURCE_ITEMS
                if params.get("category"):
                    items = [r for r in items if r["category"] == params["category"]]
                if params.get("status"):
                    items = [r for r in items if r["status"] == params["status"]]
                if params.get("q"):
                    needle = params["q"].lower()
                    items = [r for r in items if needle in r["title"].lower()]
                if params.get("has_insight") is not None:
                    want = params["has_insight"] in ("1", "true", "True")
                    items = [r for r in items if r["has_insight"] == want]
                summaries = [{k: r[k] for k in
                             ("id", "title", "category", "status", "cover", "url", "created",
                              "sample", "file", "has_insight", "insight")} for r in items]
                return self._send(200, {"items": summaries})
            if path == "/api/resources/sample/count":
                q = self.path.split("?")[1] if "?" in self.path else ""
                params = dict(p.split("=", 1) for p in q.split("&") if "=" in p)
                scope = params.get("older_than", "all")
                if scope not in ("1d", "1w", "1m", "all"):
                    return self._send(400, {"error": {
                        "what": "That's not a cleanup scope the server knows.",
                        "cause": f"'{scope}' isn't one of 1d, 1w, 1m, all.",
                        "todo": "Pick one of the four scopes."}})
                count = len([r for r in RESOURCE_ITEMS if r["sample"]])
                return self._send(200, {"count": count, "scope": scope})
            if path.startswith("/api/resources/"):
                rid = path.split("/")[3]
                found = next((r for r in RESOURCE_ITEMS if r["id"] == rid), None)
                if not found:
                    return self._send(404, {"error": {
                        "what": "That resource isn't in the vault.",
                        "cause": "No resource note in 04-Resources has that id.",
                        "todo": "Refresh the resource list."}})
                return self._send(200, found)
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
            if path == "/api/google/inbox":
                return self._send(200, {"items": GMAIL_MESSAGES})
            if path == "/api/google/events":
                return self._send(200, {"items": CALENDAR_EVENTS})
            if path == "/api/google/connect":
                # the real server hands back Google's consent URL; the mock
                # points at its own callback so e2e never leaves the harness
                return self._send(200, {"url": f"http://127.0.0.1:{PORT}/api/google/callback?state=mock&code=mock"})

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
                for field in ("confidence_threshold", "ntfy_url", "ntfy_topic", "language"):
                    if changes.get(field) is not None:
                        CONFIG[field] = changes[field]
                tl_engine = changes.get("transliteration_engine")
                if tl_engine is not None:
                    if tl_engine not in ("", "ollama", "openrouter"):
                        return self._send(400, {"error": {
                            "what": "Couldn't change the Hindi → Hinglish engine.",
                            "cause": "The request didn't name a known engine (off, ollama, or openrouter).",
                            "todo": "Pick one of the three options and try again."}})
                    if tl_engine == "openrouter" and not CONFIG["transliteration"]["openrouter_key_present"]:
                        return self._send(400, {"error": {
                            "what": "Can't switch transliteration to OpenRouter.",
                            "cause": "OPENROUTER_API_KEY is not set on the server, so OpenRouter can't run.",
                            "todo": "export OPENROUTER_API_KEY=... in the server's shell, or use Ollama instead."}})
                    CONFIG["transliteration"]["engine"] = tl_engine
                for field, key in (("transliteration_ollama_url", "ollama_url"),
                                   ("transliteration_ollama_model", "ollama_model"),
                                   ("transliteration_openrouter_model", "openrouter_model")):
                    if changes.get(field) is not None:
                        CONFIG["transliteration"][key] = changes[field]
                print("PUT CONFIG", changes)
                return self._send(200, {**CONFIG, "engine": ENGINE})

        if method == "POST":
            if path.startswith("/api/resources/") and path.endswith("/enrich"):
                rid = path.split("/")[3]
                found = next((r for r in RESOURCE_ITEMS if r["id"] == rid), None)
                if not found:
                    return self._send(404, {"error": {
                        "what": "That resource isn't in the vault.",
                        "cause": "No resource note in 04-Resources has that id.",
                        "todo": "Refresh the resource list."}})
                return self._send(200, {"ok": True, "enriched": True})
            if path.startswith("/api/resources/") and path.endswith("/status"):
                rid = path.split("/")[3]
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                new_status = json.loads(raw or b"{}").get("status", "")
                if new_status not in ("inbox", "to-consume", "consumed", "referenced", "archived"):
                    return self._send(400, {"error": {
                        "what": "That's not a resource status the vault knows.",
                        "cause": f"'{new_status}' isn't one of inbox, to-consume, consumed, "
                                 "referenced, archived (SCHEMA-REFERENCE.md §6).",
                        "todo": "Advance to one of the lifecycle statuses."}})
                found = next((r for r in RESOURCE_ITEMS if r["id"] == rid), None)
                if not found:
                    return self._send(404, {"error": {
                        "what": "That resource isn't in the vault.",
                        "cause": "No resource note in 04-Resources has that id.",
                        "todo": "Refresh the resource list."}})
                found["status"] = new_status
                summary = {k: found[k] for k in
                          ("id", "title", "category", "status", "cover", "url", "created",
                           "sample", "file", "has_insight", "insight")}
                return self._send(200, summary)
            if path.startswith("/api/resources/") and path.endswith("/insight"):
                rid = path.split("/")[3]
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                text = json.loads(raw or b"{}").get("text", "").strip()
                found = next((r for r in RESOURCE_ITEMS if r["id"] == rid), None)
                if not found:
                    return self._send(404, {"error": {
                        "what": "That resource isn't in the vault.",
                        "cause": "No resource note in 04-Resources has that id.",
                        "todo": "Refresh the resource list."}})
                found["insight"] = text or None
                found["has_insight"] = bool(text)
                summary = {k: found[k] for k in
                          ("id", "title", "category", "status", "cover", "url", "created",
                           "sample", "file", "has_insight", "insight")}
                return self._send(200, summary)
            if path.startswith("/api/todos/") and path.endswith("/toggle"):
                block_id = path.split("/")[3]
                item = next((t for t in TODO_ITEMS if t["id"] == block_id), None)
                if not item:
                    return self._send(404, {"error": {
                        "what": "That todo isn't in the daily notes anymore.",
                        "cause": "Its line was edited or removed in Obsidian, or the id is unknown.",
                        "todo": "Refresh the agenda."}})
                item["done"] = not item["done"]
                return self._send(200, {"ok": True, "done": item["done"]})
            if path == "/api/people/voice":
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                samples = [s for s in json.loads(raw or b"{}").get("samples", []) if s.strip()]
                if not samples:
                    return self._send(400, {"error": {
                        "what": "There were no writing samples to learn from.",
                        "cause": "Every sample in the list was empty.",
                        "todo": "Paste 3–5 messages you actually sent, then save again."}})
                VOICE.update(exists=True, samples=len(samples))
                return self._send(200, dict(VOICE))
            if path.startswith("/api/people/") and path.endswith("/draft"):
                pid = path.split("/")[3]
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                found = next((p for p in PEOPLE if p["id"] == pid), None)
                if not found:
                    return self._send(404, {"error": {
                        "what": "That person isn't in the vault.",
                        "cause": f"No note in 07-People has the id {pid}.",
                        "todo": "Refresh the People screen."}})
                if not VOICE["exists"]:
                    # the real refusal: no voice file, no draft (never a generic one)
                    return self._send(409, {"error": {
                        "what": "Drafts need your own voice on file first.",
                        "cause": "_System/my-voice.md doesn't exist yet, and a draft written "
                                 "without it would sound like a chatbot, not like you.",
                        "todo": "Paste 3–5 messages you've actually sent in Settings → My voice, "
                                "then try again."}})
                channel = next((c for c in ("whatsapp", "email", "linkedin")
                                if found["channels"].get(c)), "whatsapp")
                return self._send(200, {
                    "text": f"hey {found['name'].split()[0]} — long time. still thinking about "
                            "that studio conversation. free for a coffee this week?",
                    "channel": channel, "channels": found["channels"],
                    "provider": "claude-haiku"})
            if path.startswith("/api/people/") and path.endswith("/contact"):
                pid = path.split("/")[3]
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                for person in PEOPLE:
                    if person["id"] == pid:
                        person.update(days_since_contact=0, going_cold=False,
                                      warmup_due=False, commitment_due=False,
                                      last_contact="2026-08-20")
                        order = ["identified", "researched", "engaging", "conversing",
                                 "warm", "ready"]
                        stage = person["warmth_stage"]
                        nxt = (order[order.index(stage) + 1]
                               if stage in order and order.index(stage) + 1 < len(order) else None)
                        return self._send(200, {**person, "suggest_stage": nxt})
                return self._send(404, {"error": {
                    "what": "That person isn't in the vault.", "cause": "Unknown id.",
                    "todo": "Refresh the People screen."}})
            if path.startswith("/api/people/") and path.endswith("/warmth"):
                pid = path.split("/")[3]
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                stage = json.loads(raw or b"{}").get("stage", "")
                if stage not in ("identified", "researched", "engaging", "conversing",
                                 "warm", "ready"):
                    return self._send(400, {"error": {
                        "what": "That's not a warmth stage the vault knows.",
                        "cause": f"'{stage}' isn't one of the six stages in SCHEMA-REFERENCE.md.",
                        "todo": "Pick one of the stage chips on the person's card."}})
                for person in PEOPLE:
                    if person["id"] == pid:
                        person["warmth_stage"] = stage
                        return self._send(200, dict(person))
                return self._send(404, {"error": {
                    "what": "That person isn't in the vault.", "cause": "Unknown id.",
                    "todo": "Refresh the People screen."}})
            if path.startswith("/api/people/") and path.endswith("/enrich"):
                # the mock ships the unconfigured state — that's the honest default
                return self._send(503, {"error": {
                    "what": "Enrichment isn't set up yet.",
                    "cause": "PDL_API_KEY isn't set in the server's shell, so there's "
                             "nothing to ask.",
                    "todo": "Add a People Data Labs key to the server's environment and "
                            "restart the API — everything else on this card keeps working "
                            "without it."}})
            if path == "/api/people":
                body = self._json_body()
                name = (body.get("name") or "").strip()
                channel = body.get("channel") or {}
                kind, value = channel.get("kind", ""), (channel.get("value") or "").strip()
                if not name or not value or kind not in ("whatsapp", "email", "linkedin"):
                    return self._send(400, {"error": {
                        "what": "That target couldn't be added.",
                        "cause": "A target needs a name and one way to reach them.",
                        "todo": "Give them a name and one channel — WhatsApp, email, "
                                "or LinkedIn."}})
                created = _person(datetime.now().strftime("%Y%m%d%H%M%S"), name, "", "",
                                  "identified", 7, None, channels={kind: value})
                created["sample"] = False
                PEOPLE.insert(0, created)
                return self._send(201, created)

            # --- profile push (Pass D) ---------------------------------------
            # The mock ships Dex ON and contacts OFF so both states — a working
            # preview→confirm and an honest "reconnect Google" pill — are
            # visible without any real key.
            if path.startswith("/api/people/") and path.endswith("/push/preview"):
                body = self._json_body()
                pid = path.split("/")[3]
                person = next((p for p in PEOPLE if p["id"] == pid), None)
                if person is None:
                    return self._send(404, {"error": {
                        "what": "That person isn't in the vault.", "cause": "Unknown id.",
                        "todo": "Refresh the People screen."}})
                if body.get("target") == "contacts":
                    return self._send(409, {"error": {
                        "what": "Google Contacts isn't connected yet.",
                        "cause": "This cockpit's Google link was made before it could "
                                 "update contacts, so Google hasn't granted the contacts "
                                 "permission.",
                        "todo": "Open Integrations, press Disconnect, then Connect Google "
                                "again — it's a one-time re-consent."}})
                summary = (f"{person['name']} runs the artist programme at "
                           f"{person['company'] or 'their company'}.\n"
                           "Last spoke on 2026-07-20 about the season programme.\n"
                           "Open: whether the studio can hold a full season.\n"
                           f"Next: {person['next_action'] or 'no step owed'}.")
                block = (f"{PUSH_MARKER_OPEN}\n{summary}\n· via Brain OS 2026-08-20\n"
                         f"{PUSH_MARKER_CLOSE}")
                return self._send(200, {
                    "target": "dex", "person_id": pid, "name": person["name"],
                    "summary": summary, "block": block,
                    "destination": f"Dex contact {person['dex_id']} · description",
                    "replaced": ""})
            if path.startswith("/api/people/") and path.endswith("/push"):
                body = self._json_body()
                pid = path.split("/")[3]
                person = next((p for p in PEOPLE if p["id"] == pid), None)
                print("PUSH", pid, body.get("target"), repr(body.get("text", ""))[:80])
                if person is None:
                    return self._send(404, {"error": {
                        "what": "That person isn't in the vault.", "cause": "Unknown id.",
                        "todo": "Refresh the People screen."}})
                PUSHED.add(pid)
                return self._send(200, {
                    "ok": True, "target": body.get("target", "dex"),
                    "changed": f"Dex contact {person['dex_id']} · description",
                    "replaced": False})
            if path == "/api/capture":
                print("CAPTURE", self.rfile.read(int(self.headers.get("Content-Length", 0))))
                return self._send(201, {"id": "20260703061500", "status": "captured"})
            if path == "/api/capture/audio":
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                print("CAPTURE AUDIO", ctype, len(raw), "bytes")
                if ctype not in AUDIO_MIME_TYPES:
                    return self._send(400, {"error": {
                        "what": "That recording isn't in a format the pipeline can read.",
                        "cause": f"The upload's Content-Type was '{ctype or 'missing'}'.",
                        "todo": "Record again with the mic button, or drop the audio file "
                                "into the inbox folder instead."}})
                if not raw:
                    return self._send(400, {"error": {
                        "what": "There was nothing to capture.",
                        "cause": "The recording arrived empty — the mic may have been "
                                 "blocked mid-recording.",
                        "todo": "Check the microphone permission, then record again."}})
                return self._send(201, {"id": "20260703061500", "status": "captured"})
            if path == "/api/capture/image":
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                print("CAPTURE IMAGE", ctype, len(raw), "bytes")
                if ctype not in IMAGE_MIME_TYPES:
                    heic = "heic" in ctype or "heif" in ctype
                    return self._send(400, {"error": {
                        "what": "That photo isn't in a format the pipeline can read.",
                        "cause": f"The upload's Content-Type was '{ctype or 'missing'}'."
                                 + (" HEIC photos need converting first." if heic else ""),
                        "todo": "Convert it to JPEG on the device — the Shortcut's Convert "
                                "Image step (or the cockpit's own photo button) does this "
                                "automatically." if heic else "Accepted formats are JPEG, PNG, and WebP."}})
                if not raw:
                    return self._send(400, {"error": {
                        "what": "There was nothing to capture.",
                        "cause": "The upload arrived empty.",
                        "todo": "Try sharing the photo again."}})
                return self._send(201, {"id": "20260703061500", "status": "captured"})
            if path.startswith("/api/review/") and path.endswith("/approve"):
                note_id = path.split("/")[3]
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                print("APPROVE", note_id, raw)
                try:
                    approved_type = json.loads(raw or b"{}").get("type", "learning")
                except json.JSONDecodeError:
                    approved_type = "learning"
                # mirror pipeline/route.py TYPE_FOLDER so the mock echoes a
                # realistic destination for the type actually approved
                folder = TYPE_FOLDER.get(approved_type, "00-Inbox")
                for item in list(REVIEW_ITEMS):
                    if item["id"] == note_id:
                        REVIEW_ITEMS.remove(item)
                return self._send(200, {"ok": True, "moved_to": f"{folder}/approved-note.md"})
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
            if path == "/api/vault/sync":
                print("VAULT SYNC")
                if not MODE_VAULT_SYNC_CONFIGURED:
                    return self._send(400, {"error": {
                        "what": "Vault sync isn't configured.",
                        "cause": "Neither VAULT_GIT_REMOTE nor vault_sync.remote in config.json is set.",
                        "todo": "Set VAULT_GIT_REMOTE (and VAULT_GIT_TOKEN) as service variables, or "
                                "vault_sync.remote in config.json, then try again."}})
                VAULT_SYNCED = True
                return self._send(200, {"ok": True, "status": "ok", "detail": "Vault synced.",
                                        "ahead": 0, "behind": 0})
            if path == "/api/run":
                print("RUN NOW")
                return self._send(202, {"started": True})
            if path == "/api/backup":
                print("BACKUP NOW")
                return self._send(200, {"ok": True, "at": iso(datetime.now()),
                                        "vault_committed": True, "events_db_copied": True})
            if path == "/api/google/draft":
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                draft = json.loads(body or b"{}")
                print("DRAFT SAVED", draft.get("to"), draft.get("subject"))
                return self._send(200, {"id": "draft-1",
                                        "url": "https://mail.google.com/mail/u/0/#drafts"})
            if path == "/api/google/disconnect":
                print("GOOGLE DISCONNECT")
                return self._send(200, {"ok": True})

        if method == "DELETE":
            if path == "/api/resources/sample":
                q = self.path.split("?")[1] if "?" in self.path else ""
                params = dict(p.split("=", 1) for p in q.split("&") if "=" in p)
                scope = params.get("older_than", "all")
                if scope not in ("1d", "1w", "1m", "all"):
                    return self._send(400, {"error": {
                        "what": "That's not a cleanup scope the server knows.",
                        "cause": f"'{scope}' isn't one of 1d, 1w, 1m, all.",
                        "todo": "Pick one of the four scopes."}})
                targets = [r for r in RESOURCE_ITEMS if r["sample"]]
                titles = [r["title"] for r in targets]
                n = len(targets)
                for r in targets:
                    RESOURCE_ITEMS.remove(r)
                scope_phrase = {"1d": "older than a day", "1w": "older than a week",
                                "1m": "older than a month", "all": "of any age"}[scope]
                message = (
                    f"Removed {n} sample note{'' if n == 1 else 's'} {scope_phrase}. "
                    "Your real notes were never touched, and the vault was git-committed first."
                    if n else
                    f"No sample notes {scope_phrase} to remove. Nothing was changed.")
                return self._send(200, {"removed": n, "titles": titles, "scope": scope,
                                        "message": message})

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

    def do_DELETE(self):
        self._route("DELETE")

    def log_message(self, fmt, *args):  # quieter default logging
        sys.stderr.write("mock-api: %s\n" % (fmt % args))


if __name__ == "__main__":
    print(f"mock-api on http://127.0.0.1:{PORT}  (token: {TOKEN})")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
