"""Hermetic API tests. No httpx (locked deps forbid it, so no starlette
TestClient): run uvicorn on an ephemeral port in a daemon thread and drive it
with urllib. Each test gets a fresh tmp vault/inbox/config + seeded events.db,
mirroring pipeline/tests/test_pipeline.py's idiom."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import uvicorn

from api.main import create_app

TOKEN = "test-token-123"


# ---- harness ----------------------------------------------------------------

def _seed_events(db: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " timestamp TEXT NOT NULL, file TEXT NOT NULL, stage TEXT NOT NULL,"
        " status TEXT NOT NULL, duration_ms INTEGER, message TEXT, plain_english_error TEXT)")
    for r in rows:
        conn.execute(
            "INSERT INTO events (timestamp, file, stage, status, duration_ms, message,"
            " plain_english_error) VALUES (?,?,?,?,?,?,?)",
            (r["timestamp"], r["file"], r["stage"], r["status"], r.get("duration_ms"),
             r.get("message", ""), r.get("plain_english_error", "")))
    conn.commit()
    conn.close()


def _note(path: Path, note_id: str, ntype: str, status: str, body: str, created="2026-07-01"):
    path.write_text(
        f"---\nid: {note_id}\ntype: {ntype}\ncreated: {created}\nsource: voice\n"
        f"origin: human\nmeta_origin: ai\nstatus: {status}\ncategories: []\n"
        f"subjects: []\ntags: []\n---\n\n{body}\n")


class Server:
    def __init__(self, root: Path):
        self.app = create_app(root=root)
        self.config = uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="warning")
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        assert self.server.started, "server did not start"
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=5)

    def req(self, method: str, path: str, body=None, token=TOKEN):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read() or "null")
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, json.loads(raw) if raw else None


@pytest.fixture
def env(tmp_path):
    vault = tmp_path / "vault"
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    failed = tmp_path / "failed"
    for d in (vault, inbox, archive, failed):
        d.mkdir()
    (vault / "00-Inbox").mkdir()
    (vault / "02-Musings").mkdir()
    (vault / "03-Learnings").mkdir()
    (vault / "wiki").mkdir()
    subprocess.run(["git", "-C", str(vault), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "t"], check=True)

    config = {
        "vault_path": str(vault), "inbox_path": str(inbox), "archive_path": str(archive),
        "failed_path": str(failed),
        "transcription": {"engine": "whispercpp", "whispercpp": {"binary_path": "", "model_path": ""}},
        "ntfy": {"url": "", "topic": ""}, "api": {"auth_token": TOKEN},
        "classification": {"confidence_threshold": 0.7}, "links": {"dex": "https://getdex.com/"},
    }
    (tmp_path / "config.json").write_text(json.dumps(config))
    return tmp_path, vault, inbox, failed


# ---- tests ------------------------------------------------------------------

def test_health_no_auth(env):
    with Server(env[0]) as s:
        assert s.req("GET", "/api/health", token=None) == (200, {"ok": True})


def test_401_envelope(env):
    with Server(env[0]) as s:
        code, body = s.req("GET", "/api/status", token=None)
        assert code == 401 and set(body["error"]) == {"what", "cause", "todo"}
        code, body = s.req("GET", "/api/status", token="wrong")
        assert code == 401 and body["error"]["what"]


def test_unknown_api_path_is_json_404(env):
    with Server(env[0]) as s:
        code, body = s.req("GET", "/api/nope")
        assert code == 404 and "error" in body  # not StaticFiles plain text


def test_status_counts(env):
    root, vault, inbox, _ = env
    today = date.today().isoformat()
    _seed_events(root / "events.db", [
        {"timestamp": f"{today}T09:00:00", "file": "/in/a.m4a", "stage": "archive", "status": "ok"},
        {"timestamp": f"{today}T09:00:00", "file": "/in/b.m4a", "stage": "archive", "status": "ok"},
        {"timestamp": f"{today}T10:00:00", "file": "/in/c.m4a", "stage": "pipeline", "status": "failed",
         "message": "boom", "plain_english_error": "What happened: X\nLikely cause: Y\nWhat to do: Z"},
    ])
    _note(vault / "00-Inbox" / "2026-07-01-x.md", "20260701090000", "learning", "needs-review", "body")
    (inbox / "2026-07-02-0900 pending #todo.md").write_text("later")
    with Server(root) as s:
        code, body = s.req("GET", "/api/status")
        assert code == 200
        assert body["counts"] == {"pending": 1, "processed_today": 2, "needs_review": 1, "failed": 1}
        assert body["vault"] == "vault" and body["engine"] == "whispercpp"


def test_review_confidence_join(env):
    root, vault, _, _ = env
    _seed_events(root / "events.db", [
        {"timestamp": "2026-07-01T09:00:00", "file": "/in/walk.m4a", "stage": "classify",
         "status": "needs_review", "message": "type=learning confidence=0.62 by=llm"},
        {"timestamp": "2026-07-01T09:00:01", "file": "/in/walk.m4a", "stage": "route",
         "status": "ok", "message": "wrote 2026-07-01-walk.md"},
    ])
    _note(vault / "00-Inbox" / "2026-07-01-walk.md", "20260701090000", "learning", "needs-review", "b")
    _note(vault / "00-Inbox" / "2026-07-01-noevents.md", "20260701090100", "musing", "needs-review", "b")
    with Server(root) as s:
        code, body = s.req("GET", "/api/review")
        by_id = {i["id"]: i for i in body["items"]}
        assert by_id["20260701090000"]["confidence"] == 0.62
        assert by_id["20260701090100"]["confidence"] == 0.5  # fallback


def test_approve_write_path(env):
    root, vault, _, _ = env
    _note(vault / "00-Inbox" / "2026-07-01-walk.md", "20260701090000", "musing", "needs-review",
          "the note body")
    with Server(root) as s:
        code, body = s.req("POST", "/api/review/20260701090000/approve", {"type": "learning"})
        assert code == 200
        assert body["moved_to"] == "03-Learnings/2026-07-01-walk.md"
        moved = vault / "03-Learnings" / "2026-07-01-walk.md"
        assert moved.exists() and not (vault / "00-Inbox" / "2026-07-01-walk.md").exists()
        text = moved.read_text()
        assert "type: learning" in text and "status: active" in text
        assert "id: 20260701090000" in text and "origin: human" in text  # untouched
        # git committed with the descriptive message
        logmsg = subprocess.run(["git", "-C", str(vault), "log", "-1", "--format=%s"],
                                capture_output=True, text=True).stdout.strip()
        assert logmsg == "api: filed 20260701090000 as learning"
        # re-approve → 404; bad type → 400
        assert s.req("POST", "/api/review/20260701090000/approve", {"type": "learning"})[0] == 404
        _note(vault / "00-Inbox" / "b.md", "20260701090001", "musing", "needs-review", "b")
        assert s.req("POST", "/api/review/20260701090001/approve", {"type": "nope"})[0] == 400


def test_capture_roundtrip(env):
    root, _, inbox, _ = env
    from pipeline import intake
    with Server(root) as s:
        code, body = s.req("POST", "/api/capture", {"text": "call the plumber about the sink", "tag": "todo"})
        assert code == 201 and body["status"] == "captured"
        items = intake.poll(inbox)
        assert len(items) == 1
        assert items[0].tag == "todo" and items[0].source == "manual"
        # returned id matches what the pipeline will mint (minute precision, ss=00)
        assert body["id"] == items[0].captured.strftime("%Y%m%d%H%M%S")
        assert body["id"].endswith("00")
        # bad tag → 400; empty text → 400
        assert s.req("POST", "/api/capture", {"text": "x", "tag": "bogus"})[0] == 400
        assert s.req("POST", "/api/capture", {"text": "   "})[0] == 400


def test_capture_same_minute_collision(env):
    root, _, inbox, _ = env
    from pipeline import intake
    with Server(root) as s:
        s.req("POST", "/api/capture", {"text": "buy milk", "tag": "todo"})
        s.req("POST", "/api/capture", {"text": "buy milk", "tag": "todo"})
        names = sorted(p.name for p in inbox.glob("*.md"))
        assert len(names) == 2
        # the -2 lands on the name, before the tag, so the tag still parses
        assert any("-2 #todo" in n for n in names)
        assert all(i.tag == "todo" for i in intake.poll(inbox))


def test_failed_and_retry(env):
    root, vault, inbox, failed = env
    (failed / "memo.m4a").write_text("audio")
    _seed_events(root / "events.db", [
        {"timestamp": "2026-07-01T05:12:00", "file": str(inbox / "memo.m4a"), "stage": "pipeline",
         "status": "failed", "message": "Could not transcribe the recording.",
         "plain_english_error": "What happened: Could not transcribe the recording.\n"
                                "Likely cause: whisper.cpp failed.\nWhat to do: Retry after fixing."},
    ])
    with Server(root) as s:
        code, body = s.req("GET", "/api/failed")
        assert code == 200 and len(body["items"]) == 1
        item = body["items"][0]
        assert item["file"] == "memo.m4a"
        assert set(item["error"]) == {"what", "cause", "todo"}
        assert item["error"]["cause"] == "whisper.cpp failed."
        # retry restores the original filename to the inbox
        assert s.req("POST", f"/api/failed/{item['id']}/retry")[0] == 200
        assert (inbox / "memo.m4a").exists() and not (failed / "memo.m4a").exists()
        # unknown id → 404
        assert s.req("POST", "/api/failed/9999/retry")[0] == 404


def test_events_filter_and_limit(env):
    root, _, _, _ = env
    rows = [{"timestamp": f"2026-07-01T0{i}:00:00", "file": f"/in/{i}.m4a", "stage": "classify",
             "status": "ok" if i % 2 else "failed", "message": f"e{i}"} for i in range(6)]
    _seed_events(root / "events.db", rows)
    with Server(root) as s:
        _, body = s.req("GET", "/api/events?limit=2")
        assert len(body["events"]) == 2
        assert body["events"][0]["id"] > body["events"][1]["id"]  # newest first
        _, body = s.req("GET", "/api/events?status=failed")
        assert all(e["status"] == "failed" for e in body["events"])
        top = body["events"][0]["id"]
        _, body = s.req("GET", f"/api/events?before_id={top}")
        assert all(e["id"] < top for e in body["events"])


def test_streak_rule(env):
    root, _, _, _ = env
    today = date.today()
    y, d2 = today - timedelta(days=1), today - timedelta(days=2)
    _seed_events(root / "events.db", [
        {"timestamp": f"{y.isoformat()}T09:00:00", "file": "/in/a", "stage": "archive", "status": "ok"},
        {"timestamp": f"{d2.isoformat()}T09:00:00", "file": "/in/b", "stage": "archive", "status": "ok"},
    ])
    with Server(root) as s:
        _, body = s.req("GET", "/api/streak")
        assert body["current"] == 2  # today not yet captured → streak ends yesterday, not zeroed
        assert len(body["days"]) == 30 and body["days"][-1]["date"] == today.isoformat()


def test_resurfaced_deterministic_and_null(env):
    root, vault, _, _ = env
    with Server(root) as s:
        assert s.req("GET", "/api/resurfaced")[1]["note"] is None  # nothing to resurface
        # one note in each of the three knowledge folders — resurface() must
        # glob across all three, not just one
        _note(vault / "wiki" / "2026-02-14-idea.md", "20260214093000", "insight", "active",
              "First paragraph here.\n\nSecond.")
        _note(vault / "02-Musings" / "2026-02-15-hunch.md", "20260215093000", "musing", "active",
              "A musing.")
        _note(vault / "03-Learnings" / "2026-02-16-fact.md", "20260216093000", "learning", "active",
              "A learning.")
        first = s.req("GET", "/api/resurfaced")[1]["note"]
        assert first["id"] in {"20260214093000", "20260215093000", "20260216093000"}
        assert s.req("GET", "/api/resurfaced")[1]["note"]["id"] == first["id"]  # stable within a day
        # the pick can come from any of the three folders (deterministic per day)
        assert first["file"].split("/")[0] in {"wiki", "02-Musings", "03-Learnings"}


def test_run_conflict(env, monkeypatch):
    root, _, _, _ = env

    class FakeProc:
        def poll(self):
            return None  # always "running"

    import api.main as main_mod
    monkeypatch.setattr(main_mod.subprocess, "Popen", lambda *a, **k: FakeProc())
    with Server(root) as s:
        assert s.req("POST", "/api/run")[0] == 202
        assert s.req("POST", "/api/run")[0] == 409  # second while first "runs"


def test_config_get_and_put(env, monkeypatch):
    root, _, _, _ = env
    with Server(root) as s:
        code, body = s.req("GET", "/api/config")
        assert code == 200
        assert "auth_token" not in json.dumps(body)  # never leak the token
        assert body["keys"] == {"anthropic": False, "openai": False}
        # reject openai without the key
        code, body = s.req("PUT", "/api/config", {"engine": "openai"})
        assert code == 400 and "OPENAI_API_KEY" in body["error"]["cause"]
        # a valid change persists and preserves unknown keys (links)
        code, body = s.req("PUT", "/api/config", {"confidence_threshold": 0.8})
        assert code == 200 and body["confidence_threshold"] == 0.8
        saved = json.loads((root / "config.json").read_text())
        assert saved["classification"]["confidence_threshold"] == 0.8
        assert saved["links"] == {"dex": "https://getdex.com/"}


def test_integrations_shape_and_engine_guard(env):
    root, _, _, _ = env
    with Server(root) as s:
        code, body = s.req("GET", "/api/integrations")
        assert code == 200 and body["engine"] == "whispercpp"
        health = [c for c in body["cards"] if c["group"] == "health"]
        links = [c for c in body["cards"] if c["group"] == "link"]
        assert len(health) == 7
        assert {"obsidian", "dex"} <= {c["id"] for c in links}  # obsidian + configured links only
        assert all(c["badge"] is None for c in links)
        # engine toggle rejects openai without the key
        assert s.req("POST", "/api/integrations/engine", {"engine": "openai"})[0] == 400
        # ntfy test on an unconfigured topic → 400
        assert s.req("POST", "/api/integrations/ntfy/test")[0] == 400


def test_todos_ranges_and_toggle(env):
    root, vault, _, _ = env
    from datetime import timedelta
    from pipeline.todos import today_kolkata
    (vault / "06-Todos").mkdir()
    today = today_kolkata()  # ranges are Asia/Kolkata by spec, not server-local
    lines = [
        f"- [ ] pay the electrician 📅 {today.isoformat()} ⏰ 14:00 ^20260701090000-1",
        f"- [ ] send the invoice 📅 {(today - timedelta(days=2)).isoformat()} ^20260701090000-2",
        f"- [ ] review the deck 📅 {(today + timedelta(days=1)).isoformat()} ^20260701090000-3",
        f"- [ ] book flights 📅 {(today + timedelta(days=4)).isoformat()} ^20260701090000-4",
        "- [ ] undated thing ^20260701090000-5",
    ]
    (vault / "06-Todos" / f"{today.isoformat()}.md").write_text("\n".join(lines) + "\n")
    with Server(root) as s:
        assert [i["id"] for i in s.req("GET", "/api/todos?range=today")[1]["items"]] == ["20260701090000-1"]
        overdue = s.req("GET", "/api/todos?range=overdue")[1]["items"]
        assert [i["id"] for i in overdue] == ["20260701090000-2"] and overdue[0]["overdue"]
        assert [i["id"] for i in s.req("GET", "/api/todos?range=tomorrow")[1]["items"]] == ["20260701090000-3"]
        assert [i["id"] for i in s.req("GET", "/api/todos?range=week")[1]["items"]] == ["20260701090000-4"]
        assert s.req("GET", "/api/todos?range=bogus")[0] == 400
        # toggle round-trips through the file and git-commits the vault
        code, body = s.req("POST", "/api/todos/20260701090000-1/toggle")
        assert code == 200 and body["done"] is True
        assert "- [x] pay the electrician" in (vault / "06-Todos" / f"{today.isoformat()}.md").read_text()
        logmsg = subprocess.run(["git", "-C", str(vault), "log", "-1", "--format=%s"],
                                capture_output=True, text=True).stdout.strip()
        assert logmsg == "api: todo 20260701090000-1 marked done"
        assert s.req("POST", "/api/todos/nope/toggle")[0] == 404


def test_build_probes_and_providers(env):
    root, vault, _, _ = env
    import shutil
    shutil.copy(Path(__file__).resolve().parents[2] / "checks.json", root / "checks.json")
    _seed_events(root / "events.db", [
        {"timestamp": "2026-07-01T09:00:00", "file": "/in/a.m4a", "stage": "llm", "status": "failed",
         "message": "provider=gemini-flash outcome=invalid-json"},
        {"timestamp": "2026-07-01T09:00:01", "file": "/in/a.m4a", "stage": "llm", "status": "ok",
         "message": "provider=claude-haiku outcome=served confidence=0.90"},
    ])
    with Server(root) as s:
        code, body = s.req("GET", "/api/build")
        assert code == 200
        by_id = {i["id"]: i for i in body["items"]}
        assert by_id["pass2"]["done"] is True                # answering proves the API runs
        assert by_id["wire-config"]["done"] is True          # tmp config has vault_path
        assert by_id["wire-whisper"]["done"] is False        # no binary in the tmp config
        assert by_id["wire-whisper"]["next_action"]          # plain-English next step
        assert by_id["wire-backlog-clear"]["done"] is True   # tmp capture inbox is empty
        assert by_id["wire-first-capture"]["done"] is False
        assert body["next"] is not None                      # something is unfinished
        code, body = s.req("GET", "/api/providers")
        stats = {p["provider"]: p for p in body["providers"]}
        assert stats["claude-haiku"]["served"] == 1
        assert stats["claude-haiku"]["avg_confidence"] == 0.9
        assert stats["gemini-flash"]["fell_through"] == 1
        assert stats["gemini-flash"]["invalid_json"] == 1


def test_resource_enrich_endpoint(env, monkeypatch):
    root, vault, _, _ = env
    (vault / "04-Resources").mkdir()
    note = vault / "04-Resources" / "2026-07-04-a-reel.md"
    note.write_text(
        "---\nid: 20260704100000\ntype: resource\nresource_type: article\ncreated: 2026-07-04\n"
        "source: manual\norigin: human\nmeta_origin: ai\ntitle: a reel\ncover: \n"
        "source_url: https://example.com/x\ndescription: \nstatus: inbox\nplatform: web\n"
        "enriched: false\nenrich_attempts: 1\nenrich_last: 2026-07-04T10:00:00\n"
        "categories: []\nsubjects: []\ntags: []\n---\n\n## Insight\n\nsaw this\n")
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-qm", "seed"], check=True, capture_output=True)

    import api.main as main_mod
    # monkeypatch the enricher's default fetch so no real network is hit
    monkeypatch.setattr(
        main_mod.enrich, "_default_fetch",
        lambda url, data=None, timeout=10:
        b'<html><head><title>Now Enriched</title></head></html>')
    with Server(root) as s:
        code, body = s.req("POST", "/api/resources/20260704100000/enrich")
        assert code == 200 and body["enriched"] is True
        assert "enriched: true" in note.read_text() and "Now Enriched" in note.read_text()
        logmsg = subprocess.run(["git", "-C", str(vault), "log", "-1", "--format=%s"],
                                capture_output=True, text=True).stdout.strip()
        assert logmsg == "api: enriched 20260704100000"
        assert s.req("POST", "/api/resources/nope/enrich")[0] == 404


def test_config_has_enrichment_block(env):
    root, _, _, _ = env
    with Server(root) as s:
        code, body = s.req("GET", "/api/config")
        assert code == 200
        assert body["enrichment"]["youtube_keyless"] is True
        assert body["enrichment"]["apify_token"] is False   # no env token in the test
        assert body["enrichment"]["apify_actor_set"] is False
