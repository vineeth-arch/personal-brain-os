"""Pass 13 — POST /api/capture/image. Reuses the hermetic uvicorn+urllib
harness from test_api.py. Every request here is a hand-built multipart body
(matching what the iOS Shortcut / browser canvas capture actually sends) —
proves the endpoint against the real stdlib parser, not a mock of it."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from api.tests.test_api import Server, env  # noqa: F401

BOUNDARY = "----CockpitImageTestBoundary"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"

# The endpoint (and the pipeline downstream) only ever checks magic bytes —
# it never decodes pixels — so real JPEG/PNG magic + filler bytes is
# sufficient and keeps these tests independent of any image library.
JPEG_1PX = b"\xff\xd8\xff\xe0\x00\x10JFIF" + bytes(range(256)) * 4
PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
NOT_AN_IMAGE = b"this is definitely not an image, just some bytes"


def _body(fields: dict[str, str], file_field: tuple[str, bytes, str] | None = None) -> bytes:
    """fields: name -> text value. file_field: (filename, bytes, content_type)."""
    out = []
    for name, value in fields.items():
        out.append(f"--{BOUNDARY}\r\n".encode())
        out.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        out.append(value.encode())
        out.append(b"\r\n")
    if file_field:
        filename, data, ctype = file_field
        out.append(f"--{BOUNDARY}\r\n".encode())
        out.append(
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode())
        out.append(f"Content-Type: {ctype}\r\n\r\n".encode())
        out.append(data)
        out.append(b"\r\n")
    out.append(f"--{BOUNDARY}--\r\n".encode())
    return b"".join(out)


def _post_image(port: int, body: bytes, token: str | None = "test-token-123"):
    url = f"http://127.0.0.1:{port}/api/capture/image"
    headers = {"Content-Type": CONTENT_TYPE}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, json.loads(raw) if raw else None


def _sidecar_base(p: Path) -> str:
    return p.name.removesuffix(".meta.json")


def test_happy_path_writes_image_and_sidecar(env):
    tmp, vault, inbox, failed = env
    body = _body({"text": "a nice sunset over the marina", "tag": "resource", "ocr": ""},
                 ("photo.jpg", JPEG_1PX, "image/jpeg"))
    with Server(tmp) as s:
        code, resp = _post_image(s.port, body)
        assert code == 201
        assert resp["status"] == "captured"
        assert len(resp["id"]) == 14  # YYYYMMDDHHmmss

    images = list(inbox.glob("*.jpg"))
    sidecars = list(inbox.glob("*.meta.json"))
    assert len(images) == 1 and len(sidecars) == 1
    assert images[0].stem == _sidecar_base(sidecars[0])
    assert "#resource" in images[0].name
    assert "sunset" in images[0].name
    assert images[0].read_bytes() == JPEG_1PX
    sidecar = json.loads(sidecars[0].read_text())
    assert sidecar == {"text": "a nice sunset over the marina", "ocr": "", "source": "photo"}


def test_png_is_also_accepted(env):
    tmp, vault, inbox, failed = env
    body = _body({"text": "", "tag": "", "ocr": ""}, ("shot.png", PNG_MAGIC, "image/png"))
    with Server(tmp) as s:
        code, resp = _post_image(s.port, body)
        assert code == 201
    images = list(inbox.glob("*.png"))
    assert len(images) == 1
    assert images[0].stem == "photo" or "photo" in images[0].name  # no text → "photo" fallback name


def test_unrecognized_format_is_rejected(env):
    tmp, vault, inbox, failed = env
    body = _body({"text": "", "tag": "", "ocr": ""}, ("file.txt", NOT_AN_IMAGE, "text/plain"))
    with Server(tmp) as s:
        code, resp = _post_image(s.port, body)
        assert code == 400
        assert set(resp["error"]) == {"what", "cause", "todo"}
    assert list(inbox.iterdir()) == []  # nothing was written


def test_missing_file_field_is_rejected(env):
    tmp, vault, inbox, failed = env
    body = _body({"text": "hello", "tag": "", "ocr": ""})
    with Server(tmp) as s:
        code, resp = _post_image(s.port, body)
        assert code == 400
        assert "photo" in resp["error"]["what"].lower()


def test_invalid_tag_is_rejected(env):
    tmp, vault, inbox, failed = env
    body = _body({"text": "", "tag": "not-a-real-tag", "ocr": ""},
                 ("photo.jpg", JPEG_1PX, "image/jpeg"))
    with Server(tmp) as s:
        code, resp = _post_image(s.port, body)
        assert code == 400
        assert "capture tag" in resp["error"]["what"].lower() or "capture tag" in resp["error"]["cause"].lower()
    assert list(inbox.iterdir()) == []


def test_oversized_file_is_rejected_by_content_length(env):
    """A raw urllib client streaming a huge body that the server rejects
    early can surface as a connection reset rather than a clean HTTP
    response (a normal HTTP/1.1 quirk — real clients like the Shortcut or a
    browser handle this transparently). Either outcome is acceptable here;
    what matters is that nothing oversized ever lands in the inbox. The
    size-cap logic itself is unit-tested directly in test_notes_image.py."""
    tmp, vault, inbox, failed = env
    huge = b"\xff\xd8\xff" + b"\x00" * (16 * 1024 * 1024)  # 16MB, over the 15MB cap
    body = _body({"text": "", "tag": "", "ocr": ""}, ("big.jpg", huge, "image/jpeg"))
    with Server(tmp) as s:
        try:
            code, resp = _post_image(s.port, body)
            assert code == 413
            assert "large" in resp["error"]["what"].lower()
        except urllib.error.URLError:
            pass  # connection reset while streaming the oversized body — acceptable
    assert list(inbox.iterdir()) == []


def test_wrong_token_is_rejected(env):
    tmp, vault, inbox, failed = env
    body = _body({"text": "", "tag": "", "ocr": ""}, ("photo.jpg", JPEG_1PX, "image/jpeg"))
    with Server(tmp) as s:
        code, resp = _post_image(s.port, body, token="wrong-token")
        assert code == 401
    assert list(inbox.iterdir()) == []


def test_malicious_filename_is_ignored_no_path_traversal(env):
    """The client-supplied filename must never influence where the file lands
    — the server always derives the name from the owner's own thought text."""
    tmp, vault, inbox, failed = env
    body = _body({"text": "totally normal capture", "tag": "", "ocr": ""},
                 ("../../../../etc/passwd", JPEG_1PX, "image/jpeg"))
    with Server(tmp) as s:
        code, resp = _post_image(s.port, body)
        assert code == 201
    # the image landed safely inside the inbox, named from the TEXT field —
    # never from the attacker-controlled filename
    images = list(inbox.glob("*.jpg"))
    assert len(images) == 1
    assert "passwd" not in images[0].name
    assert "totally-normal-capture" in images[0].name
    # and nothing escaped the inbox directory
    assert not (tmp / "etc").exists()


def test_collision_gets_a_numeric_suffix_image_and_sidecar_stay_paired(env):
    tmp, vault, inbox, failed = env
    body = _body({"text": "same thought", "tag": "", "ocr": "some ocr"},
                 ("a.jpg", JPEG_1PX, "image/jpeg"))
    with Server(tmp) as s:
        code1, r1 = _post_image(s.port, body)
        code2, r2 = _post_image(s.port, body)
        assert code1 == 201 and code2 == 201

    images = sorted(inbox.glob("*.jpg"))
    sidecars = sorted(inbox.glob("*.meta.json"))
    assert len(images) == 2 and len(sidecars) == 2
    # every image has its own paired sidecar
    assert {i.stem for i in images} == {_sidecar_base(s) for s in sidecars}


def test_sidecar_field_length_is_capped(env):
    """A sidecar field rides into an LLM prompt downstream — cap it so a
    pathological upload can't blow up token usage or storage."""
    tmp, vault, inbox, failed = env
    body = _body({"text": "x" * 50_000, "tag": "", "ocr": "y" * 50_000},
                 ("photo.jpg", JPEG_1PX, "image/jpeg"))
    with Server(tmp) as s:
        code, resp = _post_image(s.port, body)
        assert code == 201
    sidecar = json.loads(next(inbox.glob("*.meta.json")).read_text())
    assert len(sidecar["text"]) == 20_000
    assert len(sidecar["ocr"]) == 20_000
