"""pipeline/tests/test_merge.py — C1's five merge kinds, pure and
deterministic (no filesystem, no clock — `today` is always passed in)."""
from __future__ import annotations

from pipeline import merge


def test_set_once_fills_blank():
    fm, suggestion = merge.apply_field({}, "id", "20260916101500",
                                        source="cockpit", origin="human", today="2026-09-16")
    assert fm["id"] == "20260916101500"
    assert suggestion is None


def test_set_once_never_overwrites():
    fm, suggestion = merge.apply_field({"id": "OLD"}, "id", "NEW",
                                        source="cockpit", origin="human", today="2026-09-16")
    assert fm["id"] == "OLD"
    assert suggestion is None


def test_fill_sets_when_blank():
    fm, suggestion = merge.apply_field({"company": ""}, "company", "Acme",
                                        source="handshake", origin="ai", today="2026-09-16")
    assert fm["company"] == "Acme"
    assert suggestion is None


def test_fill_suggests_when_set_and_different():
    fm, suggestion = merge.apply_field({"company": "Studio X"}, "company", "Acme",
                                        source="handshake", origin="ai", today="2026-09-16")
    assert fm["company"] == "Studio X"  # unchanged
    assert suggestion == "- 2026-09-16 · company: Studio X → Acme? (handshake, ai)"


def test_fill_no_suggestion_when_same_value():
    fm, suggestion = merge.apply_field({"company": "Acme"}, "company", "Acme",
                                        source="handshake", origin="ai", today="2026-09-16")
    assert fm["company"] == "Acme"
    assert suggestion is None


def test_union_scalar_plus_scalar_becomes_list():
    fm, _ = merge.apply_field({"dex_id": "abc"}, "dex_id", "xyz",
                               source="cockpit", origin="ai", today="2026-09-16")
    assert fm["dex_id"] == ["abc", "xyz"]


def test_union_dedupes_case_insensitive():
    fm, _ = merge.apply_field({"tags": ["Branding"]}, "tags", "branding",
                               source="cockpit", origin="ai", today="2026-09-16")
    assert fm["tags"] == ["Branding"]


def test_union_channels_map_per_key():
    fm, _ = merge.apply_field({"channels": {"email": "a@b.c"}}, "channels",
                               {"email": "d@e.f", "whatsapp": "+971555"},
                               source="handshake", origin="ai", today="2026-09-16")
    assert fm["channels"] == {"email": ["a@b.c", "d@e.f"], "whatsapp": "+971555"}


def test_forward_keeps_later_date():
    fm, _ = merge.apply_field({"last_contact": "2026-09-01"}, "last_contact", "2026-09-10",
                               source="cockpit", origin="human", today="2026-09-16")
    assert fm["last_contact"] == "2026-09-10"


def test_forward_ignores_earlier_date():
    fm, _ = merge.apply_field({"last_contact": "2026-09-10"}, "last_contact", "2026-09-01",
                               source="cockpit", origin="human", today="2026-09-16")
    assert fm["last_contact"] == "2026-09-10"


def test_unknown_field_defaults_to_fill():
    fm, suggestion = merge.apply_field({"birthday": "1990-01-01"}, "birthday", "1990-02-02",
                                        source="handshake", origin="ai", today="2026-09-16")
    assert fm["birthday"] == "1990-01-01"
    assert suggestion is not None


def test_append_line_creates_section():
    body = "# Priya\n\n## Context\n\nSome context.\n"
    out = merge.append_line(body, "Interaction log", "- 2026-09-16 — called", "<!-- bc:abc -->")
    assert "## Interaction log" in out
    assert "- 2026-09-16 — called <!-- bc:abc -->" in out


def test_append_line_idempotent_by_marker():
    body = "# Priya\n\n## Interaction log\n\n- 2026-09-16 — called <!-- bc:abc -->\n"
    out = merge.append_line(body, "Interaction log", "- 2026-09-16 — called AGAIN", "<!-- bc:abc -->")
    assert out == body  # unchanged — same marker already present


def test_append_line_appends_to_existing_section():
    body = "# Priya\n\n## Interaction log\n\n- 2026-09-01 — met <!-- bc:one -->\n"
    out = merge.append_line(body, "Interaction log", "- 2026-09-16 — called", "<!-- bc:two -->")
    assert "- 2026-09-01 — met <!-- bc:one -->" in out
    assert "- 2026-09-16 — called <!-- bc:two -->" in out


def test_owner_change_always_applies():
    fm, line = merge.owner_change({"relationship": "prospect"}, "relationship", "client",
                                   today="2026-09-16")
    assert fm["relationship"] == "client"
    assert line == "- 2026-09-16 · relationship: prospect → client (owner)"


def test_normalize_email():
    assert merge.normalize_email("  Priya@Studio.com ") == "priya@studio.com"


def test_normalize_phone():
    assert merge.normalize_phone("+971 (555) 123-4567") == "9715551234567"


def test_slugify():
    assert merge.slugify("Priya D'Souza") == "priya-dsouza"
    assert merge.slugify("   ") == ""
