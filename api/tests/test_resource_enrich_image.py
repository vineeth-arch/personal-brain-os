"""Pass V3 — POST /api/resources/{id}/enrich re-runs vision for a photo
resource (platform: photo) instead of the URL-based reenrich_note path,
which a photo note has nothing for (no source_url). Mirrors
test_api.py::test_resource_enrich_endpoint's link version; the network seam
here is pipeline.vision._call_claude (the floor provider — only
ANTHROPIC_API_KEY is set below, so the cheapest-first chain falls through
gemini-flash/openai-mini and lands on claude) rather than enrich._default_fetch."""
from __future__ import annotations

import json
import subprocess

from api.tests.test_api import Server, env  # noqa: F401


def test_resource_enrich_reruns_vision_for_a_photo_note(env, monkeypatch):
    root, vault, _, _ = env
    (vault / "04-Resources").mkdir(exist_ok=True)
    (vault / "attachments").mkdir(exist_ok=True)
    (vault / "attachments" / "x.jpg").write_bytes(b"jpeg-bytes")
    note = vault / "04-Resources" / "2026-07-04-a-photo.md"
    note.write_text(
        "---\nid: 20260704100000\ntype: resource\nresource_type: article\ncreated: 2026-07-04\n"
        "source: manual\norigin: human\nmeta_origin: ai\ntitle: a photo\ncover: attachments/x.jpg\n"
        "source_url: \ndescription: \nstatus: inbox\nplatform: photo\n"
        "enriched: false\nenrich_attempts: 1\nenrich_last: 2026-07-04T10:00:00\n"
        "categories: []\nsubjects: []\ntags: []\n---\n\n## Insight\n\nworth revisiting\n",
        encoding="utf-8")
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-qm", "seed"], check=True, capture_output=True)

    import pipeline.vision as vision_mod
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    # Only ANTHROPIC_API_KEY is set, so gemini-flash/openai-mini are skipped
    # keyless and the chain lands on claude-haiku, the floor provider — patch
    # the PROVIDERS entry (not the standalone _call_claude reference) since
    # describe()'s real chain dispatches through that dict, not the function
    # object directly.
    monkeypatch.setitem(
        vision_mod.PROVIDERS, "claude-haiku",
        lambda image_path, mime, key: json.dumps({
            "description": "A whiteboard with a project roadmap.",
            "resource_type": "tool", "extracted_text": "Q3 launch",
        }))

    with Server(root) as s:
        code, body = s.req("POST", "/api/resources/20260704100000/enrich")
        assert code == 200 and body["enriched"] is True
        text = note.read_text(encoding="utf-8")
        assert "enriched: true" in text
        assert "description: A whiteboard with a project roadmap." in text
        assert "resource_type: tool" in text
        assert "## Extracted text" in text and "Q3 launch" in text
        assert "worth revisiting" in text  # the user's insight is untouched
        logmsg = subprocess.run(["git", "-C", str(vault), "log", "-1", "--format=%s"],
                                capture_output=True, text=True).stdout.strip()
        assert logmsg == "api: enriched 20260704100000"


def test_resource_enrich_keeps_photo_note_honest_when_vision_fails(env, monkeypatch):
    root, vault, _, _ = env
    (vault / "04-Resources").mkdir(exist_ok=True)
    (vault / "attachments").mkdir(exist_ok=True)
    (vault / "attachments" / "x.jpg").write_bytes(b"jpeg-bytes")
    note = vault / "04-Resources" / "2026-07-04-a-photo.md"
    note.write_text(
        "---\nid: 20260704100000\ntype: resource\nresource_type: article\ncreated: 2026-07-04\n"
        "source: manual\norigin: human\nmeta_origin: ai\ntitle: a photo\ncover: attachments/x.jpg\n"
        "source_url: \ndescription: \nstatus: inbox\nplatform: photo\n"
        "enriched: false\nenrich_attempts: 1\nenrich_last: 2026-07-04T10:00:00\n"
        "categories: []\nsubjects: []\ntags: []\n---\n\n## Insight\n\nworth revisiting\n",
        encoding="utf-8")
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-qm", "seed"], check=True, capture_output=True)

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # no key configured at all

    with Server(root) as s:
        code, body = s.req("POST", "/api/resources/20260704100000/enrich")
        assert code == 200 and body["enriched"] is False
        text = note.read_text(encoding="utf-8")
        assert "enriched: false" in text and "enrich_attempts: 2" in text
        assert "worth revisiting" in text
