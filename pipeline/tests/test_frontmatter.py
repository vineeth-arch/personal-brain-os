"""pipeline/tests/test_frontmatter.py — round-trip correctness for the real
frontmatter parser (D6). channels/tags/categories become list-valued once
C1's union rule fires, so the flat column-0 scanner in relationships.py
can't be reused — this one handles inline lists/maps and block lists."""
from __future__ import annotations

from pipeline import frontmatter


def test_round_trip_simple_scalars():
    text = (
        "---\n"
        "id: 20260916101500\n"
        "type: person\n"
        "created: 2026-09-16\n"
        "status: active\n"
        "---\n"
        "# Body\n\nSome text.\n"
    )
    fm, body = frontmatter.parse(text)
    assert fm == {"id": "20260916101500", "type": "person",
                  "created": "2026-09-16", "status": "active"}
    assert body == "# Body\n\nSome text.\n"
    assert frontmatter.serialize(fm, body) == text


def test_round_trip_blank_value():
    text = "---\nid: 1\ndex_id: \n---\nBody\n"
    fm, body = frontmatter.parse(text)
    assert fm["dex_id"] == ""
    assert frontmatter.serialize(fm, body) == text


def test_inline_list():
    text = "---\nid: 1\ntags: [alpha, beta]\n---\nBody\n"
    fm, body = frontmatter.parse(text)
    assert fm["tags"] == ["alpha", "beta"]
    assert frontmatter.serialize(fm, body) == text


def test_inline_list_empty():
    text = "---\nid: 1\ntags: []\n---\nBody\n"
    fm, body = frontmatter.parse(text)
    assert fm["tags"] == []
    assert frontmatter.serialize(fm, body) == text


def test_inline_map():
    text = "---\nid: 1\nchannels: {whatsapp: +971555, email: a@b.c}\n---\nBody\n"
    fm, body = frontmatter.parse(text)
    assert fm["channels"] == {"whatsapp": "+971555", "email": "a@b.c"}
    assert frontmatter.serialize(fm, body) == text


def test_inline_map_empty():
    text = "---\nid: 1\nchannels: {}\n---\nBody\n"
    fm, body = frontmatter.parse(text)
    assert fm["channels"] == {}
    assert frontmatter.serialize(fm, body) == text


def test_block_list():
    text = "---\nid: 1\nattendees:\n  - \"[[20260101090000]]\"\n  - \"[[20260102090000]]\"\n---\nBody\n"
    fm, body = frontmatter.parse(text)
    assert fm["attendees"] == ["[[20260101090000]]", "[[20260102090000]]"]
    assert frontmatter.serialize(fm, body) == text


def test_always_list_single_value_stays_a_list():
    text = "---\nid: 1\ntags: [solo]\n---\nBody\n"
    fm, body = frontmatter.parse(text)
    assert fm["tags"] == ["solo"]
    assert frontmatter.serialize(fm, body) == text


def test_ordinary_key_single_item_list_serializes_as_scalar():
    # a key NOT in ALWAYS_LIST that happens to hold a 1-item list (produced
    # by merge.py's union rule) serializes back as a plain scalar — this is
    # what keeps a note with one email address looking exactly as a human
    # would have typed it, and only grows a `[...]` shape once there's a
    # second value to union in.
    fm = {"id": "1", "dex_deeplink": ["only-one"]}
    out = frontmatter.serialize(fm, "Body\n")
    assert out == "---\nid: 1\ndex_deeplink: only-one\n---\nBody\n"


def test_no_frontmatter_block():
    text = "Just a body, no frontmatter.\n"
    fm, body = frontmatter.parse(text)
    assert fm == {}
    assert body == text


def test_quoted_scalar_unquoted_on_parse_requoted_on_serialize_if_needed():
    # a value containing ": " must be quoted to stay valid frontmatter;
    # parse() strips matching quotes, serialize() re-adds them only when needed
    text = '---\nid: 1\ntitle: "Note: a title with a colon"\n---\nBody\n'
    fm, body = frontmatter.parse(text)
    assert fm["title"] == "Note: a title with a colon"
    assert frontmatter.serialize(fm, body) == text


def test_plain_scalar_with_colon_but_no_space_not_quoted():
    text = "---\nid: 1\nurl: https://example.com/a:b\n---\nBody\n"
    fm, body = frontmatter.parse(text)
    assert fm["url"] == "https://example.com/a:b"
    assert frontmatter.serialize(fm, body) == text


def test_inline_list_with_quotable_element_stays_inline():
    # a quotable element must NOT force the whole list to block form —
    # shape is preserved from the source, never inferred from content
    text = '---\nid: 1\ntags: ["work: urgent", other]\n---\nBody\n'
    fm, body = frontmatter.parse(text)
    assert fm["tags"] == ["work: urgent", "other"]
    assert frontmatter.serialize(fm, body) == text


def test_single_item_block_list_on_ordinary_key_stays_block():
    text = '---\nid: 1\nattendees:\n  - "[[123]]"\n---\nBody\n'
    fm, body = frontmatter.parse(text)
    assert fm["attendees"] == ["[[123]]"]
    assert frontmatter.serialize(fm, body) == text


def test_parse_returns_empty_on_block_map_it_cannot_represent():
    # a block-style nested map under a key with no inline value — this
    # parser doesn't support it; it must fail closed (whole doc unparsed)
    # rather than silently drop the map and let a caller re-serialize an
    # empty key over real content
    text = ("---\nid: 1\nchannels:\n  email: a@b.c\n  whatsapp: +9715\n---\n"
            "Body\n")
    fm, body = frontmatter.parse(text)
    assert fm == {}
    assert body == text
