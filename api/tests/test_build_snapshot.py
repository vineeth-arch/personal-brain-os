"""Task I3: the /api/build disk snapshot. A cold server (empty in-memory
cache) with a `.build-snapshot.json` left over from a previous run answers
instantly from that file — stale: true, cached_at set — instead of paying the
full synchronous probe cost on its very first request, and kicks a background
refresh off to catch up."""
from __future__ import annotations

import json

from .test_api import TOKEN, Server, env  # noqa: F401  (env is a fixture)

FAKE_SNAPSHOT = {
    "generated_at": "2020-01-01T00:00:00",
    "next": None,
    "items": [
        {"id": "fake-item", "label": "Fake milestone from disk", "phase": "Fake phase",
         "done": True, "detail": "totally fake — proves this came from disk, not a live probe.",
         "next_action": None},
    ],
}


def test_cold_start_answers_from_disk_snapshot(env):
    root, _, _, _ = env
    (root / ".build-snapshot.json").write_text(json.dumps(FAKE_SNAPSHOT), encoding="utf-8")
    with Server(root) as s:
        # a brand-new create_app() means app.state.build_cache starts empty —
        # this simulates a cold server restart with no warm in-memory cache
        code, body = s.req("GET", "/api/build")
        assert code == 200
        assert body["stale"] is True
        assert body["cached_at"] == FAKE_SNAPSHOT["generated_at"]
        # proves the live probes were never run inline for this response —
        # a real probe run would never produce this fabricated item
        assert body["items"] == FAKE_SNAPSHOT["items"]
        assert body["generated_at"] == FAKE_SNAPSHOT["generated_at"]


def test_normal_call_writes_the_disk_snapshot(env):
    root, _, _, _ = env
    snapshot_path = root / ".build-snapshot.json"
    assert not snapshot_path.exists()
    with Server(root) as s:
        code, body = s.req("GET", "/api/build")
        assert code == 200
        assert "stale" not in body
        assert snapshot_path.exists()
        on_disk = json.loads(snapshot_path.read_text(encoding="utf-8"))
        assert on_disk == body
