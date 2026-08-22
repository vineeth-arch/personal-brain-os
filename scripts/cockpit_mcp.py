#!/usr/bin/env python3
"""Cockpit MCP server — Claude Desktop's window into the running cockpit.

This process holds NO logic and NO data. Every tool is one authenticated HTTP
call to an endpoint the cockpit already serves, and the answer is passed
straight back. The vault stays the only source of truth (CLAUDE.md §1); this is
a window onto it, not a second copy of it.

What it deliberately cannot do:

- **It cannot send anything.** `people_draft` returns draft TEXT, exactly as
  the cockpit's own drawer does; delivering it stays a human action in the
  human's own messaging app (CLAUDE.md §4). There is no tool here that
  contacts another person, and none may ever be added.
- **It writes almost nothing.** `capture_text` is the single write, and it
  writes to the capture inbox — the same thing the cockpit's text box does.

Configuration is env-only (CLAUDE.md §7):

    COCKPIT_URL     e.g. https://cockpit.example.com   (or http://127.0.0.1:8000)
    COCKPIT_TOKEN   the api.auth_token from config.json

Claude Desktop config (also documented in help.html and DEPLOY.md):

    {
      "mcpServers": {
        "brain-cockpit": {
          "command": "/path/to/brain-cockpit/.venv/bin/python",
          "args": ["/path/to/brain-cockpit/scripts/cockpit_mcp.py"],
          "env": {
            "COCKPIT_URL": "https://your-tunnel-hostname",
            "COCKPIT_TOKEN": "the token from config.json"
          }
        }
      }
    }
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.mcpserver import MCPServer

TIMEOUT = 20

mcp = MCPServer(
    name="brain-cockpit",
    instructions=(
        "Read-mostly access to the owner's Brain Cockpit: pipeline health, the "
        "people who have gone quiet, today's todos, and a draft writer. "
        "Drafts come back as text for the owner to send themselves — nothing "
        "here can message anybody. The only write is capture_text, which drops "
        "a thought into the capture inbox."),
)


class CockpitError(Exception):
    """Carries the cockpit's own three-part message through to the tool caller
    rather than replacing it with something vaguer."""


def _base() -> str:
    url = os.environ.get("COCKPIT_URL", "").rstrip("/")
    if not url:
        raise CockpitError(
            "What happened: The cockpit address isn't set.\n"
            "Likely cause: COCKPIT_URL is missing from this MCP server's environment.\n"
            "What to do: Add COCKPIT_URL (e.g. http://127.0.0.1:8000) to the env "
            "block of the brain-cockpit entry in your Claude Desktop config.")
    return url


def _token() -> str:
    token = os.environ.get("COCKPIT_TOKEN", "")
    if not token:
        raise CockpitError(
            "What happened: The cockpit access token isn't set.\n"
            "Likely cause: COCKPIT_TOKEN is missing from this MCP server's environment.\n"
            "What to do: Copy api.auth_token from the cockpit's config.json into the "
            "env block of the brain-cockpit entry in your Claude Desktop config.")
    return token


def _envelope_text(payload: dict) -> str | None:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and {"what", "cause", "todo"} <= set(error):
        return (f"What happened: {error['what']}\n"
                f"Likely cause: {error['cause']}\n"
                f"What to do: {error['todo']}")
    return None


def call(method: str, path: str, body: dict | None = None, *, opener=None) -> dict:
    """One authenticated call to the cockpit. `opener` replaces the network in
    tests. Non-2xx bodies carry the cockpit's {what,cause,todo} envelope and it
    is passed through verbatim — a plain-English error is the whole point of
    having written them."""
    url = _base() + path
    token = _token()
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with (opener or urllib.request.urlopen)(request, timeout=TIMEOUT) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            payload = {}
        passed_through = _envelope_text(payload)
        if passed_through:
            raise CockpitError(passed_through) from None
        raise CockpitError(
            f"What happened: The cockpit refused that request (error {e.code}).\n"
            "Likely cause: The token may be wrong, or the cockpit is running an "
            "older version without this route.\n"
            "What to do: Check COCKPIT_TOKEN, and that the cockpit is up to date.") from None
    except urllib.error.URLError as e:
        raise CockpitError(
            "What happened: The cockpit couldn't be reached.\n"
            f"Likely cause: Nothing answered at {url} ({e.reason}).\n"
            "What to do: Start the cockpit API, or correct COCKPIT_URL in your "
            "Claude Desktop config.") from None


# ---- the tools -------------------------------------------------------------------

@mcp.tool(
    description="How the capture pipeline is doing right now: which vault and "
                "transcription engine are in use, when the watcher last ran, and "
                "the counts of captures pending, processed today, waiting for "
                "review, and failed.")
def cockpit_status() -> dict:
    return call("GET", "/api/status")


@mcp.tool(
    description="The owner's people, ranked by who is furthest past their own "
                "contact cadence. Optionally narrow to those going cold, or to "
                "one warmth stage (identified, researched, engaging, conversing, "
                "warm, ready).")
def people_list(going_cold: bool = False, warmth_stage: str = "") -> dict:
    items = call("GET", "/api/people").get("items", [])
    if going_cold:
        items = [p for p in items if p.get("going_cold")]
    if warmth_stage:
        items = [p for p in items if p.get("warmth_stage") == warmth_stage.strip().lower()]
    return {"items": items}


@mcp.tool(
    description="Write a reconnection message to one person, in the owner's own "
                "voice, leashed to what their note actually records. Returns the "
                "TEXT ONLY — this server cannot send messages, and the owner "
                "sends it themselves from their own app. person_id is the note's "
                "frontmatter id, from people_list.")
def people_draft(person_id: str, channel: str = "") -> dict:
    return call("POST", f"/api/people/{urllib.parse.quote(person_id)}/draft",
                {"channel": channel or None})


@mcp.tool(
    description="Drop a thought into the capture inbox, exactly as the cockpit's "
                "own capture box does — the pipeline transcribes, classifies and "
                "files it. Optional tag routes it for free: one of todo, idea, "
                "journal, learning, person, resource, decision, project.")
def capture_text(text: str, tag: str = "") -> dict:
    return call("POST", "/api/capture", {"text": text, "tag": tag or None})


@mcp.tool(description="Today's todos from the vault's daily todo files.")
def todos_today() -> dict:
    return call("GET", "/api/todos?range=today")


if __name__ == "__main__":
    mcp.run("stdio")
