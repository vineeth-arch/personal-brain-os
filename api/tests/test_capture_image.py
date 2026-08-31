"""Pass V1/V2 — POST /api/capture/image. Reuses the hermetic harness from
test_api.py. What these tests defend: HEIC/unsupported formats are refused
with the on-device conversion advice (never decoded server-side, CLAUDE.md
§7), the size cap fires mid-stream, the insight sidecar is written as a
dotfile invisible to intake.poll, and a normal roundtrip parses exactly like
api/notes.image_capture_path intends."""
from __future__ import annotations

from api.tests.test_api import Server, env  # noqa: F401


def test_image_roundtrip_with_insight_writes_sidecar_then_image(env):
    root, _, inbox, _ = env
    from pipeline import intake
    with Server(root) as s:
        code, body = s.raw(
            "POST", "/api/capture/image?tag=idea&name=whiteboard&insight=worth%20revisiting",
            b"\xff\xd8\xff\xe0fake jpeg bytes", "image/jpeg")
        assert code == 201 and body["status"] == "captured"

    items = intake.poll(inbox)
    assert len(items) == 1
    item = items[0]
    assert item.kind == "image" and item.source == "manual" and item.tag == "idea"
    assert item.path.suffix == ".jpg" and "whiteboard" in item.path.name
    assert body["id"] == item.captured.strftime("%Y%m%d%H%M%S")

    # the sidecar is a dotfile — intake never sees it as its own item
    sidecar = item.path.with_name(f".{item.path.stem}.insight")
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8") == "worth revisiting"
    assert all(not p.name.startswith(".") or p == sidecar for p in inbox.iterdir())


def test_image_without_insight_writes_no_sidecar(env):
    root, _, inbox, _ = env
    with Server(root) as s:
        code, _ = s.raw("POST", "/api/capture/image", b"\xff\xd8\xff\xe0jpeg", "image/jpeg")
        assert code == 201
    dotfiles = [p for p in inbox.iterdir() if p.name.startswith(".")]
    assert dotfiles == []


def test_heic_is_rejected_with_on_device_conversion_advice(env):
    root, _, inbox, _ = env
    with Server(root) as s:
        code, body = s.raw("POST", "/api/capture/image", b"heic-bytes", "image/heic")
        assert code == 400
        assert set(body["error"]) == {"what", "cause", "todo"}
        assert "HEIC" in body["error"]["cause"]
        assert "convert" in body["error"]["todo"].lower()
    assert list(inbox.iterdir()) == []


def test_unrecognised_content_type_is_rejected(env):
    root, _, inbox, _ = env
    with Server(root) as s:
        code, body = s.raw("POST", "/api/capture/image", b"hello", "text/plain")
        assert code == 400 and set(body["error"]) == {"what", "cause", "todo"}
    assert list(inbox.iterdir()) == []


def test_bad_tag_is_rejected(env):
    root, _, inbox, _ = env
    with Server(root) as s:
        code, _ = s.raw("POST", "/api/capture/image?tag=bogus", b"x", "image/jpeg")
        assert code == 400
    assert list(inbox.iterdir()) == []


def test_empty_upload_is_rejected(env):
    root, _, inbox, _ = env
    with Server(root) as s:
        code, body = s.raw("POST", "/api/capture/image", b"", "image/jpeg")
        assert code == 400 and body["error"]["what"]
    assert list(inbox.iterdir()) == []


def test_image_size_cap(env, monkeypatch):
    root, _, inbox, _ = env
    from api import notes as notes_mod
    monkeypatch.setattr(notes_mod, "MAX_IMAGE_BYTES", 1024)
    with Server(root) as s:
        code, body = s.raw("POST", "/api/capture/image", b"x" * 4096, "image/jpeg")
        assert code == 413 and body["error"]["todo"]
    # nothing half-written left behind — including no orphaned sidecar
    assert list(inbox.iterdir()) == []


def test_png_and_webp_are_accepted(env):
    root, _, inbox, _ = env
    with Server(root) as s:
        assert s.raw("POST", "/api/capture/image", b"\x89PNG", "image/png")[0] == 201
        assert s.raw("POST", "/api/capture/image", b"RIFFxxxxWEBP", "image/webp")[0] == 201
    exts = sorted(p.suffix for p in inbox.iterdir() if not p.name.startswith("."))
    assert exts == [".png", ".webp"]
