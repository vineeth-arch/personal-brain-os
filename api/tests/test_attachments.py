"""Pass F — signed attachment URLs (GET /api/att/{name}).

Security-focused: this is a brand-new UNAUTHENTICATED route serving files
from disk, protected only by an HMAC-signed, time-limited query string
instead of the usual bearer token. Every test here is load-bearing, not
polish — a gap in signature verification or path-traversal protection is a
real vulnerability.

Reuses the hermetic harness from test_api.py (ephemeral uvicorn + urllib, the
git-initialised tmp vault fixture).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from api.main import _ATTACHMENT_NAME_RE, sign_attachment
from api.tests.test_api import TOKEN, Server, env  # noqa: F401 (env is a fixture)


def _get_raw(port: int, path: str, token: str | None = None):
    """A GET that returns raw bytes rather than JSON — Server.req always
    json-parses, which breaks on a real image response."""
    url = f"http://127.0.0.1:{port}{path}"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _envelope(body: bytes) -> dict:
    return json.loads(body)["error"]


# ---- name-format regex (unit-level — proves the character class itself
# rejects a slash or a leading dot, independent of how far an HTTP request
# carrying one even gets through routing) ------------------------------------

def test_name_regex_rejects_traversal_shapes():
    assert _ATTACHMENT_NAME_RE.fullmatch("../../../../etc/passwd") is None  # contains "/"
    assert _ATTACHMENT_NAME_RE.fullmatch("..") is None  # leading char not alnum
    assert _ATTACHMENT_NAME_RE.fullmatch(".hidden") is None  # leading dot
    assert _ATTACHMENT_NAME_RE.fullmatch("sub/dir.jpg") is None  # embedded slash
    assert _ATTACHMENT_NAME_RE.fullmatch("sample.jpg") is not None  # sanity: legit name passes


# ---- round trip: the whole point --------------------------------------------

def test_valid_signature_serves_the_file_without_a_bearer_token(env):
    root, vault, _, _ = env
    attachments = vault / "attachments"
    attachments.mkdir()
    content = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    (attachments / "sample.jpg").write_bytes(content)

    with Server(root) as s:
        url = sign_attachment("sample.jpg", TOKEN)
        code, body = _get_raw(s.port, url, token=None)  # no Authorization header at all
        assert code == 200
        assert body == content


def test_tampered_signature_is_rejected(env):
    root, vault, _, _ = env
    attachments = vault / "attachments"
    attachments.mkdir()
    (attachments / "sample.jpg").write_bytes(b"bytes")

    with Server(root) as s:
        url = sign_attachment("sample.jpg", TOKEN)
        # flip one hex character in the signature
        path, _, sig = url.rpartition("sig=")
        tampered_char = "0" if sig[0] != "0" else "1"
        tampered = f"{path}sig={tampered_char}{sig[1:]}"
        code, body = _get_raw(s.port, tampered, token=None)
        assert code == 403
        envelope = _envelope(body)
        assert set(envelope) == {"what", "cause", "todo"}


def test_expired_signature_is_rejected(env):
    root, vault, _, _ = env
    attachments = vault / "attachments"
    attachments.mkdir()
    (attachments / "sample.jpg").write_bytes(b"bytes")

    with Server(root) as s:
        past = int(time.time()) - 3600
        url = sign_attachment("sample.jpg", TOKEN, now=past, ttl=1)  # exp = past + 1, already gone
        code, body = _get_raw(s.port, url, token=None)
        assert code == 403


def test_tampered_and_expired_return_the_identical_envelope(env):
    """No oracle: a bad signature and an expired one must be indistinguishable
    to anyone probing the endpoint."""
    root, vault, _, _ = env
    attachments = vault / "attachments"
    attachments.mkdir()
    (attachments / "sample.jpg").write_bytes(b"bytes")

    with Server(root) as s:
        past = int(time.time()) - 3600
        expired_url = sign_attachment("sample.jpg", TOKEN, now=past, ttl=1)
        _, expired_body = _get_raw(s.port, expired_url, token=None)

        valid_url = sign_attachment("sample.jpg", TOKEN)
        path, _, sig = valid_url.rpartition("sig=")
        tampered_char = "0" if sig[0] != "0" else "1"
        tampered_url = f"{path}sig={tampered_char}{sig[1:]}"
        _, tampered_body = _get_raw(s.port, tampered_url, token=None)

        assert json.loads(expired_body) == json.loads(tampered_body)


def test_traversal_name_rejected_before_touching_filesystem(env):
    """A single path SEGMENT that would traverse upward via '..' — no slash
    needed, since Path(attachments_dir) / '..' resolves to the parent dir on
    its own. This reaches the handler (unlike a slash-bearing name, which
    Starlette's own routing already refuses to match to /api/att/{name} —
    confirmed separately below) and must be blocked by the leading-alnum
    regex rule before any filesystem access."""
    root, vault, _, _ = env
    attachments = vault / "attachments"
    attachments.mkdir()
    # something a successful traversal to the parent dir could plausibly hit
    (vault / "secret.txt").write_text("should never be servable", encoding="utf-8")

    with Server(root) as s:
        exp = int(time.time()) + 600
        import hashlib
        import hmac
        sig = hmac.new(TOKEN.encode(), f"..:{exp}".encode(), hashlib.sha256).hexdigest()
        code, body = _get_raw(s.port, f"/api/att/..?exp={exp}&sig={sig}", token=None)
        assert code == 403
        envelope = _envelope(body)
        # the DEDICATED "invalid link" envelope (from the regex check), not
        # the "attachment isn't in the vault" 404 that only fires after a
        # signature is already proven valid
        assert "isn't valid" in envelope["what"]


def test_slash_bearing_traversal_never_reaches_the_vault(env):
    """A name containing literal slashes never even matches the
    /api/att/{name} route (Starlette's default path converter excludes '/'),
    so this can never return the target file's bytes and never leaks whether
    /etc/passwd exists — confirmed here at the HTTP boundary, independent of
    the regex-level unit test above."""
    root, vault, _, _ = env
    (vault / "attachments").mkdir()

    with Server(root) as s:
        exp = int(time.time()) + 600
        import hashlib
        import hmac
        name = "../../../../etc/passwd"
        sig = hmac.new(TOKEN.encode(), f"{name}:{exp}".encode(), hashlib.sha256).hexdigest()
        code, body = _get_raw(s.port, f"/api/att/{name}?exp={exp}&sig={sig}", token=None)
        assert code != 200
        assert b"root:" not in body  # never served /etc/passwd's contents


def test_leading_dot_name_rejected(env):
    root, vault, _, _ = env
    attachments = vault / "attachments"
    attachments.mkdir()
    (attachments / ".hidden").write_bytes(b"secret")

    with Server(root) as s:
        url = sign_attachment(".hidden", TOKEN)
        code, body = _get_raw(s.port, url, token=None)
        assert code == 403


def test_valid_signature_for_missing_file_is_404_not_403(env):
    root, vault, _, _ = env
    (vault / "attachments").mkdir()  # exists, but no ghost.jpg inside it

    with Server(root) as s:
        url = sign_attachment("ghost.jpg", TOKEN)
        code, body = _get_raw(s.port, url, token=None)
        assert code == 404
        envelope = _envelope(body)
        assert "isn't in the vault" in envelope["what"]


# ---- resources routes mint the signed URL -----------------------------------

def _write_photo_resource(vault_path, note_id: str, created: str) -> None:
    folder = vault_path / "04-Resources"
    folder.mkdir(parents=True, exist_ok=True)
    text = (
        "---\n"
        f"id: {note_id}\n"
        "type: resource\n"
        "resource_type: photo\n"
        "status: inbox\n"
        "title: Studio wall sketch\n"
        "cover: attachments/sample.jpg\n"
        f"created: {created}\n"
        "categories: []\n"
        "subjects: []\n"
        "tags: []\n"
        "---\n\n"
        "A photo resource with a local cover.\n"
    )
    (folder / f"{created}-studio-wall-sketch.md").write_text(text, encoding="utf-8")


def test_resources_list_mints_signed_cover_that_round_trips(env):
    root, vault, _, _ = env
    attachments = vault / "attachments"
    attachments.mkdir()
    content = b"jpeg-bytes-for-the-cover"
    (attachments / "sample.jpg").write_bytes(content)
    _write_photo_resource(vault, "20260731080000", "2026-07-31")

    with Server(root) as s:
        code, body = s.req("GET", "/api/resources")  # real bearer token, via Server.req
        assert code == 200
        items = body["items"]
        assert len(items) == 1
        cover = items[0]["cover"]
        # minted, not the raw frontmatter value
        assert cover.startswith("/api/att/sample.jpg?")
        assert "exp=" in cover and "sig=" in cover

        # the signed URL stands ENTIRELY on its own — no Authorization header
        # this time, proving the query-string auth genuinely doesn't need one
        code2, raw_body = _get_raw(s.port, cover, token=None)
        assert code2 == 200
        assert raw_body == content


def test_resource_detail_also_mints_signed_cover(env):
    root, vault, _, _ = env
    attachments = vault / "attachments"
    attachments.mkdir()
    content = b"jpeg-bytes"
    (attachments / "sample.jpg").write_bytes(content)
    _write_photo_resource(vault, "20260731080000", "2026-07-31")

    with Server(root) as s:
        code, body = s.req("GET", "/api/resources/20260731080000")
        assert code == 200
        cover = body["cover"]
        assert cover.startswith("/api/att/sample.jpg?")
        code2, raw_body = _get_raw(s.port, cover, token=None)
        assert code2 == 200
        assert raw_body == content


def test_absolute_cover_url_passes_through_unsigned(env):
    root, vault, _, _ = env
    folder = vault / "04-Resources"
    folder.mkdir(parents=True, exist_ok=True)
    text = (
        "---\nid: 20260703140000\ntype: resource\nresource_type: recipe\n"
        "status: inbox\ntitle: Weeknight dal\n"
        "cover: https://picsum.photos/seed/dal/400/560\n"
        "created: 2026-07-03\ncategories: []\nsubjects: []\ntags: []\n---\n\nbody\n"
    )
    (folder / "2026-07-03-weeknight-dal.md").write_text(text, encoding="utf-8")

    with Server(root) as s:
        code, body = s.req("GET", "/api/resources")
        assert code == 200
        assert body["items"][0]["cover"] == "https://picsum.photos/seed/dal/400/560"


# ---- review fix: mutation routes were still returning the raw frontmatter
# cover value, silently re-breaking the exact bug this task exists to close
# (a cover advancing status or saving an insight would 404 until the next
# poll re-minted it). Both POST /api/resources/{id}/status and
# POST /api/resources/{id}/insight return the same _resource_summary shape
# as GET /api/resources — they need the identical _signed_cover treatment. --

def test_status_route_returns_signed_cover_that_round_trips(env):
    root, vault, _, _ = env
    attachments = vault / "attachments"
    attachments.mkdir()
    content = b"jpeg-bytes-status"
    (attachments / "sample.jpg").write_bytes(content)
    _write_photo_resource(vault, "20260731080000", "2026-07-31")

    with Server(root) as s:
        code, body = s.req(
            "POST", "/api/resources/20260731080000/status",
            body={"status": "to-consume"})
        assert code == 200
        cover = body["cover"]
        # not the raw frontmatter value
        assert cover != "attachments/sample.jpg"
        assert cover.startswith("/api/att/sample.jpg?")
        # and it actually works, unauthenticated, through the real GET route —
        # not just a string-prefix check
        code2, raw_body = _get_raw(s.port, cover, token=None)
        assert code2 == 200
        assert raw_body == content


def test_insight_route_returns_signed_cover_that_round_trips(env):
    root, vault, _, _ = env
    attachments = vault / "attachments"
    attachments.mkdir()
    content = b"jpeg-bytes-insight"
    (attachments / "sample.jpg").write_bytes(content)
    _write_photo_resource(vault, "20260731080000", "2026-07-31")

    with Server(root) as s:
        code, body = s.req(
            "POST", "/api/resources/20260731080000/insight",
            body={"text": "Nice light in this one."})
        assert code == 200
        cover = body["cover"]
        assert cover != "attachments/sample.jpg"
        assert cover.startswith("/api/att/sample.jpg?")
        code2, raw_body = _get_raw(s.port, cover, token=None)
        assert code2 == 200
        assert raw_body == content


def test_enrich_route_has_no_cover_field_to_sign(env):
    """The enrich route returns {ok, enriched} — a bool, never a resource
    summary shape — so there is nothing here for _signed_cover to touch.
    Asserted explicitly so a future shape change doesn't silently reopen
    the same bug the status/insight routes just had."""
    root, vault, _, _ = env
    _write_photo_resource(vault, "20260731080000", "2026-07-31")

    with Server(root) as s:
        code, body = s.req("POST", "/api/resources/20260731080000/enrich")
        assert code == 200
        assert set(body) == {"ok", "enriched"}


# ---- review fix: hmac.compare_digest raises TypeError (-> uncaught 500) on
# a non-ASCII sig; it must fail closed into the same 403 envelope instead. --

def test_non_ascii_signature_is_403_not_500(env):
    root, vault, _, _ = env
    attachments = vault / "attachments"
    attachments.mkdir()
    (attachments / "sample.jpg").write_bytes(b"bytes")

    with Server(root) as s:
        exp = int(time.time()) + 600
        code, body = _get_raw(
            s.port, f"/api/att/sample.jpg?exp={exp}&sig=%C3%A9%C3%A9%C3%A9",
            token=None)
        assert code == 403
        envelope = _envelope(body)
        assert "isn't valid" in envelope["what"]
