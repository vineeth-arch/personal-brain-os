"""Pass S — POST /api/capture learns a share shape: {url, insight?, tag?}
alongside the existing {text, tag?}. What these tests defend: the two shapes
share one route without interfering, a bad url is refused before anything is
written, and the composed inbox capture is exactly what the pipeline's link
detection (Pass L) expects."""
from __future__ import annotations

from api.tests.test_api import Server, env  # noqa: F401


def test_text_shape_is_unchanged(env):
    """Back-compat: {text, tag} alone behaves exactly as before Pass S."""
    tmp, _, inbox, _ = env
    with Server(tmp) as s:
        code, body = s.req("POST", "/api/capture", {"text": "call the plumber", "tag": "todo"})
        assert code == 201 and body["status"] == "captured"
    files = list(inbox.iterdir())
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8").strip() == "call the plumber"
    assert "#todo" in files[0].name


def test_url_only_share_writes_just_the_link(env):
    tmp, _, inbox, _ = env
    with Server(tmp) as s:
        code, body = s.req("POST", "/api/capture", {"url": "https://youtube.com/watch?v=abc123"})
        assert code == 201 and body["status"] == "captured"
    files = list(inbox.iterdir())
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8").strip() == "https://youtube.com/watch?v=abc123"
    # slug comes from the host, not a mash of the whole URL
    assert "youtube" in files[0].name.lower()


def test_url_with_insight_composes_insight_then_url(env):
    tmp, _, inbox, _ = env
    with Server(tmp) as s:
        code, _ = s.req("POST", "/api/capture",
                        {"url": "https://instagram.com/reel/xyz", "insight": "great hook, steal this"})
        assert code == 201
    files = list(inbox.iterdir())
    text = files[0].read_text(encoding="utf-8").strip()
    assert text == "great hook, steal this\n\nhttps://instagram.com/reel/xyz"
    # slug comes from the insight's words, not the URL
    assert "great" in files[0].name.lower() or "hook" in files[0].name.lower()


def test_url_must_be_http_or_https(env):
    tmp, *_ = env
    with Server(tmp) as s:
        for bad in ("not-a-url", "ftp://example.com/x", "javascript:alert(1)", ""):
            code, body = s.req("POST", "/api/capture", {"url": bad})
            assert code == 400, bad
            assert set(body["error"]) == {"what", "cause", "todo"}


def test_neither_text_nor_url_is_refused(env):
    tmp, *_ = env
    with Server(tmp) as s:
        code, body = s.req("POST", "/api/capture", {})
        assert code == 400
        assert set(body["error"]) == {"what", "cause", "todo"}


def test_share_with_tag_still_free_routes(env):
    tmp, _, inbox, _ = env
    with Server(tmp) as s:
        code, _ = s.req("POST", "/api/capture", {"url": "https://example.com/x", "tag": "resource"})
        assert code == 201
    files = list(inbox.iterdir())
    assert "#resource" in files[0].name


def test_share_with_bad_tag_is_refused(env):
    tmp, *_ = env
    with Server(tmp) as s:
        code, body = s.req("POST", "/api/capture", {"url": "https://example.com/x", "tag": "not-a-tag"})
        assert code == 400
        assert set(body["error"]) == {"what", "cause", "todo"}
