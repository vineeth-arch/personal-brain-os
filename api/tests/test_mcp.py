"""Pass X — the cockpit MCP server.

It is a proxy and nothing more, so what these tests defend is exactly that:
every tool maps to one existing endpoint, the cockpit's plain-English errors
survive the trip, missing configuration says which env var is missing, and no
tool can deliver a message to anybody.
"""
from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_SCRIPT = REPO_ROOT / "scripts" / "cockpit_mcp.py"

mcp_module = pytest.importorskip(
    "mcp", reason="the MCP SDK is only needed by scripts/cockpit_mcp.py")


def _load():
    spec = importlib.util.spec_from_file_location("cockpit_mcp", MCP_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cockpit(monkeypatch):
    monkeypatch.setenv("COCKPIT_URL", "http://cockpit.test")
    monkeypatch.setenv("COCKPIT_TOKEN", "tok")
    return _load()


class FakeHTTP:
    """Records requests, replays canned responses — the whole network."""

    def __init__(self, responses: dict[str, dict]):
        self.responses = responses
        self.calls = []

    def __call__(self, request, timeout=None):
        body = request.data.decode() if request.data else None
        self.calls.append((request.get_method(), request.full_url, body,
                           dict(request.headers)))
        payload = self.responses.get(request.full_url, {})
        return _Response(json.dumps(payload).encode())


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _http_error(status: int, payload: dict):
    def raise_it(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, status, "err", {},
            io.BytesIO(json.dumps(payload).encode()))
    return raise_it


# ---- every tool is one call to an endpoint that already exists -------------------

def test_each_tool_maps_to_its_endpoint(cockpit):
    cases = [
        (lambda: cockpit.call("GET", "/api/status", opener=http), "GET",
         "http://cockpit.test/api/status", None),
        (lambda: cockpit.call("GET", "/api/todos?range=today", opener=http), "GET",
         "http://cockpit.test/api/todos?range=today", None),
        (lambda: cockpit.call("POST", "/api/capture", {"text": "hi", "tag": None},
                              opener=http), "POST",
         "http://cockpit.test/api/capture", '{"text": "hi", "tag": null}'),
    ]
    for run, method, url, body in cases:
        http = FakeHTTP({url: {"ok": True}})
        run()
        assert http.calls[-1][0] == method
        assert http.calls[-1][1] == url
        assert http.calls[-1][2] == body


def test_every_request_carries_the_bearer_token(cockpit):
    http = FakeHTTP({"http://cockpit.test/api/status": {"ok": True}})
    cockpit.call("GET", "/api/status", opener=http)
    headers = http.calls[0][3]
    assert headers.get("Authorization") == "Bearer tok"


def test_people_list_filters_client_side_without_new_server_logic(cockpit,
                                                                  monkeypatch):
    people = {"items": [
        {"name": "Priya", "going_cold": True, "warmth_stage": "conversing"},
        {"name": "Tomás", "going_cold": False, "warmth_stage": "ready"},
    ]}
    http = FakeHTTP({"http://cockpit.test/api/people": people})
    monkeypatch.setattr(cockpit.urllib.request, "urlopen", http)

    assert len(cockpit.people_list()["items"]) == 2
    cold = cockpit.people_list(going_cold=True)["items"]
    assert [p["name"] for p in cold] == ["Priya"]
    ready = cockpit.people_list(warmth_stage="READY")["items"]
    assert [p["name"] for p in ready] == ["Tomás"]
    # one GET per call, always to the same existing endpoint
    assert {c[1] for c in http.calls} == {"http://cockpit.test/api/people"}


def test_the_draft_tool_returns_text_and_never_delivers_it(cockpit, monkeypatch):
    draft = {"text": "hey Priya — long time", "channel": "whatsapp",
             "channels": {"whatsapp": "+971500000001"}, "provider": "claude-haiku"}
    http = FakeHTTP({"http://cockpit.test/api/people/20260701090000/draft": draft})
    monkeypatch.setattr(cockpit.urllib.request, "urlopen", http)

    result = cockpit.people_draft("20260701090000")
    assert result["text"].startswith("hey Priya")
    assert http.calls[0][0] == "POST"
    assert http.calls[0][1].endswith("/api/people/20260701090000/draft")


# ---- errors keep their plain English ---------------------------------------------

def test_the_cockpits_three_part_error_passes_straight_through(cockpit):
    envelope = {"error": {
        "what": "Drafts need your own voice on file first.",
        "cause": "_System/my-voice.md doesn't exist yet.",
        "todo": "Paste 3–5 messages you've actually sent in Settings → My voice."}}
    with pytest.raises(cockpit.CockpitError) as caught:
        cockpit.call("POST", "/api/people/x/draft", {},
                     opener=_http_error(409, envelope))
    message = str(caught.value)
    assert "What happened: Drafts need your own voice on file first." in message
    assert "Likely cause: _System/my-voice.md doesn't exist yet." in message
    assert "Settings → My voice" in message


def test_an_error_without_an_envelope_still_says_what_to_do(cockpit):
    with pytest.raises(cockpit.CockpitError) as caught:
        cockpit.call("GET", "/api/status", opener=_http_error(500, {"detail": "boom"}))
    message = str(caught.value)
    assert "What happened:" in message and "What to do:" in message
    assert "boom" not in message, "raw server detail belongs in logs, not in the tool result"


def test_an_unreachable_cockpit_names_the_setting_to_fix(cockpit):
    def refuse(request, timeout=None):
        raise urllib.error.URLError("Connection refused")
    with pytest.raises(cockpit.CockpitError) as caught:
        cockpit.call("GET", "/api/status", opener=refuse)
    assert "COCKPIT_URL" in str(caught.value)


def test_missing_configuration_names_the_missing_variable(monkeypatch):
    monkeypatch.delenv("COCKPIT_URL", raising=False)
    monkeypatch.delenv("COCKPIT_TOKEN", raising=False)
    module = _load()
    with pytest.raises(module.CockpitError) as caught:
        module.call("GET", "/api/status")
    assert "COCKPIT_URL" in str(caught.value)

    monkeypatch.setenv("COCKPIT_URL", "http://cockpit.test")
    with pytest.raises(module.CockpitError) as caught:
        module.call("GET", "/api/status")
    assert "COCKPIT_TOKEN" in str(caught.value)


# ---- the shape of the tool set ---------------------------------------------------

@pytest.mark.anyio
async def test_the_registered_tools_are_exactly_the_agreed_set(cockpit):
    names = {tool.name for tool in await cockpit.mcp.list_tools()}
    assert names == {"cockpit_status", "people_list", "people_draft",
                     "capture_text", "todos_today"}


@pytest.mark.anyio
async def test_every_tool_describes_itself(cockpit):
    for tool in await cockpit.mcp.list_tools():
        assert tool.description and len(tool.description) > 40, (
            f"{tool.name} needs a description a model can choose it by")


def test_the_mcp_layer_has_no_way_to_send_anything():
    """CLAUDE.md §4 reaches the MCP surface too: an assistant holding these
    tools must not be able to contact anybody."""
    from api.tests.test_no_send import SEND_PATTERNS

    source = MCP_SCRIPT.read_text(encoding="utf-8")
    offenders = [f"{i}: {line.strip()}"
                 for i, line in enumerate(source.splitlines(), start=1)
                 if SEND_PATTERNS.search(line)]
    assert not offenders, "\n".join(offenders)
    # and the only write it can perform is a capture into the owner's own inbox
    assert source.count('call("POST"') == 2, (
        "the writes are exactly people_draft (which only generates text) and "
        "capture_text — adding another POST needs a constitution check")
