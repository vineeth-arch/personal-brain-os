"""Pass HB — the scoped Handshake token (1b-2). What this defends: the
integration token works on its allowlisted reads + capture, is refused with
403 (not silently allowed) on anything else — including the owner-only
`?desk=1` field and any write/delete route — and the master token keeps
working on everything, unaffected."""
from __future__ import annotations

import json

import pytest

from api.tests.test_api import TOKEN, Server, env  # noqa: F401

HS_TOKEN = "handshake-scoped-token-xyz"


@pytest.fixture
def env_with_handshake_token(env, tmp_path):
    root, vault, inbox, failed = env
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    config["api"]["handshake_token"] = HS_TOKEN
    (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return root, vault, inbox, failed


def test_scoped_token_allowed_route_200(env_with_handshake_token):
    root, *_ = env_with_handshake_token
    with Server(root) as s:
        code, _ = s.req("GET", "/api/status", token=HS_TOKEN)
        assert code == 200


def test_scoped_token_cannot_delete(env_with_handshake_token):
    root, *_ = env_with_handshake_token
    with Server(root) as s:
        code, _ = s.req("DELETE", "/api/resources/sample?older_than=all", token=HS_TOKEN)
        assert code == 403


def test_scoped_token_cannot_reach_desk_view(env_with_handshake_token):
    root, vault, *_ = env_with_handshake_token
    folder = vault / "07-People"
    folder.mkdir()
    (folder / "priya.md").write_text(
        "---\nid: 20260701090000\ntype: person\ncreated: 2026-07-01\nsource: manual\n"
        "origin: human\nrelationship: client\ncompany: Acme\nchannels: {}\n"
        "cadence_days: 7\nlast_contact: 2026-06-01\nwarmth_stage: engaging\n"
        "status: active\n---\n\n# Priya\n", encoding="utf-8")
    with Server(root) as s:
        code, _ = s.req("GET", "/api/people/20260701090000?desk=1", token=HS_TOKEN)
        assert code == 403


def test_master_token_still_works_on_everything(env_with_handshake_token):
    root, *_ = env_with_handshake_token
    with Server(root) as s:
        code, _ = s.req("GET", "/api/status", token=TOKEN)
        assert code == 200
        code, _ = s.req("DELETE", "/api/resources/sample?older_than=all", token=TOKEN)
        assert code == 200
