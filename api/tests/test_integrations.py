"""Pass 4 integration-screen behaviors: config writes bust the health-card
cache, the ntfy test push is truthful in both directions (mocked — the send
itself can't run in a network-restricted test environment), unknown link keys
render, and the git card reports dirty/clean honestly."""
from __future__ import annotations

import json
import subprocess

from api import integrations

from .test_api import TOKEN, Server, env  # noqa: F401  (env is a fixture)

NTFY_SET = {"ntfy_url": "https://ntfy.example", "ntfy_topic": "test-topic"}


def test_put_config_busts_integrations_cache(env):
    root, _, _, _ = env
    with Server(root) as s:
        # warm the 60s cache — the ntfy card starts unconfigured
        _, body = s.req("GET", "/api/integrations")
        ntfy = next(c for c in body["cards"] if c["id"] == "ntfy")
        assert ntfy["badge"] == "Not configured"
        # a config write must invalidate the cache…
        assert s.req("PUT", "/api/config", NTFY_SET)[0] == 200
        # …so a plain GET (no fresh=1) already reflects the new topic
        _, body = s.req("GET", "/api/integrations")
        ntfy = next(c for c in body["cards"] if c["id"] == "ntfy")
        assert ntfy["meta"]["topic"] == "test-topic"
        assert ntfy["badge"] == "Untested"


def test_put_config_openai_with_key_switches_live(env, monkeypatch):
    root, _, _, _ = env
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with Server(root) as s:
        _, body = s.req("GET", "/api/integrations")  # warm cache on whispercpp
        assert body["engine"] == "whispercpp"
        code, body = s.req("PUT", "/api/config", {"engine": "openai"})
        assert code == 200 and body["engine"] == "openai"
        # the write landed in config.json…
        saved = json.loads((root / "config.json").read_text())
        assert saved["transcription"]["engine"] == "openai"
        # …and integrations reflect it without a restart or fresh=1
        _, body = s.req("GET", "/api/integrations")
        assert body["engine"] == "openai"
        openai_card = next(c for c in body["cards"] if c["id"] == "transcription-openai")
        assert openai_card["meta"]["engine_active"] is True


def test_ntfy_test_send_success_mocked(env, monkeypatch):
    root, _, _, _ = env
    sent = []
    monkeypatch.setattr(integrations, "_raw_push",
                        lambda url, topic, message, title: sent.append((url, topic, message)))
    with Server(root) as s:
        s.req("PUT", "/api/config", NTFY_SET)
        code, body = s.req("POST", "/api/integrations/ntfy/test")
        assert code == 200 and body == {"ok": True}
        assert len(sent) == 1 and sent[0][:2] == ("https://ntfy.example", "test-topic")
        _, body = s.req("GET", "/api/integrations")
        ntfy = next(c for c in body["cards"] if c["id"] == "ntfy")
        assert ntfy["status"] == "ok" and ntfy["badge"] == "Delivered"


def test_ntfy_test_send_failure_mocked(env, monkeypatch):
    root, _, _, _ = env
    def boom(url, topic, message, title):
        raise OSError("network unreachable")
    monkeypatch.setattr(integrations, "_raw_push", boom)
    with Server(root) as s:
        s.req("PUT", "/api/config", NTFY_SET)
        code, body = s.req("POST", "/api/integrations/ntfy/test")
        assert code == 502
        assert set(body["error"]) == {"what", "cause", "todo"}
        assert "couldn't be reached" in body["error"]["cause"]
        # the card renders the failed test gracefully (no fresh=1 needed)
        _, body = s.req("GET", "/api/integrations")
        ntfy = next(c for c in body["cards"] if c["id"] == "ntfy")
        assert ntfy["status"] == "warn" and ntfy["badge"] == "Test failed"
        assert set(ntfy["error"]) == {"what", "cause", "todo"}
        # a later successful test clears the warn
        monkeypatch.setattr(integrations, "_raw_push", lambda *a: None)
        assert s.req("POST", "/api/integrations/ntfy/test")[0] == 200
        _, body = s.req("GET", "/api/integrations")
        assert next(c for c in body["cards"] if c["id"] == "ntfy")["status"] == "ok"


def test_unknown_link_keys_render(env):
    root, _, _, _ = env
    config = json.loads((root / "config.json").read_text())
    config["links"] = {"dex": "https://getdex.com/", "notion": "https://www.notion.so/x",
                       "empty": ""}
    (root / "config.json").write_text(json.dumps(config))
    with Server(root) as s:
        _, body = s.req("GET", "/api/integrations")
        health = [c for c in body["cards"] if c["group"] == "health"]
        links = {c["id"]: c for c in body["cards"] if c["group"] == "link"}
        assert len(health) == 7  # unknown links never grow the health set
        assert {"obsidian", "dex", "notion"} <= set(links)
        assert "empty" not in links  # blank urls are skipped
        notion = links["notion"]
        assert notion["url"] == "https://www.notion.so/x"
        assert notion["icon"] == "notion" and notion["badge"] is None  # lettermark fallback


def test_git_dirty_clean_truthfully_reported(env):
    root, vault, _, _ = env
    (vault / "seed.md").write_text("seed\n")
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-qm", "seed"], check=True,
                   capture_output=True)
    with Server(root) as s:
        _, body = s.req("GET", "/api/integrations?fresh=1")
        git_card = next(c for c in body["cards"] if c["id"] == "git")
        assert git_card["meta"]["dirty"] is False
        assert git_card["status"] == "ok" and git_card["badge"] == "Clean"
        # dirty the vault → the card must say so
        (vault / "uncommitted.md").write_text("new\n")
        _, body = s.req("GET", "/api/integrations?fresh=1")
        git_card = next(c for c in body["cards"] if c["id"] == "git")
        assert git_card["meta"]["dirty"] is True
        assert git_card["badge"] == "Changes pending"  # fresh changes: ok, not warn


def test_config_providers_chain(env):
    root, _, _, _ = env
    with Server(root) as s:
        _, body = s.req("GET", "/api/config")
        assert body["providers"] == ["gemini-flash", "groq-llama-3.3-70b",
                                     "openrouter-free", "claude-haiku"]  # default chain
    config = json.loads((root / "config.json").read_text())
    config["classification"]["providers"] = ["claude-haiku"]
    (root / "config.json").write_text(json.dumps(config))
    with Server(root) as s:
        _, body = s.req("GET", "/api/config")
        assert body["providers"] == ["claude-haiku"]
