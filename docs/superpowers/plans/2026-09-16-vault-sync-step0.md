# Step 0 — One Vault (Pass VS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** One vault instead of two. A real frontmatter parser and additive merge rules (C1) land in this repo, `vaultsync.py` gains a note-aware conflict resolver instead of aborting-and-hoping, and the build tracker can prove sync is actually healthy. This is the code half of Step 0 in the roadmap (`~/.claude/plans/read-claude-md-schema-reference-md-web-a-glittery-meerkat.md`); the operator steps (creating the bare repo on the server, the one-time merge, the Mac launchd job) are listed at the end and are NOT implementer tasks — they're run by the owner after this code ships.

**Architecture:** Two new pure-function modules (`pipeline/frontmatter.py`, `pipeline/merge.py`) with zero I/O beyond string in/out, so they're trivially unit-testable and reusable by every future writer (Steps 1–4). `vaultsync.py` gains a conflict resolver that uses both, plus a health signal in events.db that the existing build tracker (`api/build_status.py`) can probe. Nothing here talks to Handshake, cal.diy, or Supabase — this plan only makes the vault itself correct and observable.

**Tech Stack:** stdlib only (CLAUDE.md §7) — `re`, `datetime`, `dataclasses`, `zoneinfo`, `subprocess`, `sqlite3`. No new dependencies.

**Spec:** `~/.claude/plans/read-claude-md-schema-reference-md-web-a-glittery-meerkat.md`, sections C1, C5's D6/D7 references, "Design decisions" table (D3, D6), and the "Step 0" section. Law: `CLAUDE.md`, `SCHEMA-REFERENCE.md`.

## Global Constraints

- Vault stays the only source of truth (CLAUDE.md §1); nothing here writes note content to events.db.
- API keys/tokens env-only, never config.json (CLAUDE.md §7). `VAULT_GIT_REMOTE=/vault.git` is a path, not a secret — fine in `.env` or config.json either way per existing `vaultsync.remote_config`.
- Locked deps: stdlib only for backend changes in this plan.
- Every new persistent bit of state in events.db is disposable and says so in its docstring (existing convention in `pipeline/events.py`).
- `pipeline/frontmatter.py` and `pipeline/merge.py` must round-trip byte-identically on every existing person/company fixture note — a parser that reformats a file the owner didn't toutouch is itself a bug.
- checks.json item schema: `{id, phase, label, type, ..., next_action}`; probe types registered in `api/build_status.py` `_PROBES`.

---

### Task 1: `pipeline/frontmatter.py` — real frontmatter parser (D6)

**Files:**
- Create: `pipeline/frontmatter.py`
- Test: `pipeline/tests/test_frontmatter.py`

**Interfaces — Produces:**
- `parse(text: str) -> tuple[dict, str]` — `(frontmatter_dict, body)`. Values: plain scalars as `str`; `key: [a, b]` or `key: {a: b, c: d}` inline forms parsed to `list[str]` / `dict[str, str]`; a **block list** (`key:\n  - a\n  - b`) parsed to `list[str]`; blank value → `""`. Keys never present in the source are simply absent from the dict (no forced defaults — callers decide).
- `serialize(frontmatter: dict, body: str) -> str` — inverse of `parse`. A `list[str]` of length 1 serializes as `key: value` (not `[value]`) UNLESS the key is registered in `ALWAYS_LIST` (see below) — this keeps existing single-value notes untouched by round-trip. `dict[str, str]` serializes as the same inline `{a: b, c: d}` form. Key order preserved from insertion order of the dict passed in.
- `ALWAYS_LIST: frozenset[str]` — `{"categories", "subjects", "tags"}` (matches SCHEMA-REFERENCE §2 — these are always lists even with 0 or 1 items, per existing notes).

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_frontmatter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.frontmatter'`

- [ ] **Step 3: Write `pipeline/frontmatter.py`**

```python
"""A real frontmatter parser (D6) — scalars, inline lists/maps, and block
lists, round-trip preserving. Every existing writer in this repo
(api/notes.py, pipeline/route.py, pipeline/relationships.py) uses its own
narrower reader; this one exists because C1's union rule (merge.py) turns
single-valued fields like `channels` into list/map-valued ones, and the
column-0 `key: value` scanners those modules use can't represent that.
Locked deps (CLAUDE.md §7) rule out PyYAML — this is a small, deliberately
narrow parser for exactly the shapes this vault's notes use, not a general
YAML implementation."""
from __future__ import annotations

import re

ALWAYS_LIST = frozenset({"categories", "subjects", "tags"})

_FM_DELIM = "---\n"
_BLOCK_ITEM_RE = re.compile(r"^  - (.*)$")


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def _quote_if_needed(value: str) -> str:
    if ": " in value or value.startswith(("[", "{", "'", '"')) or value.strip() != value:
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _parse_inline_list(raw: str) -> list[str]:
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return []
    return [_unquote(p) for p in inner.split(",")]


def _parse_inline_map(raw: str) -> dict[str, str]:
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return {}
    out: dict[str, str] = {}
    for part in inner.split(","):
        key, _, value = part.partition(":")
        key, value = _unquote(key), _unquote(value)
        if key:
            out[key] = value
    return out


def parse(text: str) -> tuple[dict, str]:
    """(frontmatter, body). A missing/malformed frontmatter block returns
    ({}, text) unchanged — same "never lose the note" posture as every other
    reader in this repo."""
    if not text.startswith(_FM_DELIM):
        return {}, text
    parts = text.split(_FM_DELIM, 2)
    if len(parts) < 3:
        return {}, text
    raw_fm, body = parts[1], parts[2]

    fm: dict = {}
    lines = raw_fm.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.startswith((" ", "\t")):
            i += 1
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if not key:
            i += 1
            continue
        if rest.startswith("[") and rest.endswith("]"):
            fm[key] = _parse_inline_list(rest)
        elif rest.startswith("{") and rest.endswith("}"):
            fm[key] = _parse_inline_map(rest)
        elif rest == "" and i + 1 < len(lines) and _BLOCK_ITEM_RE.match(lines[i + 1]):
            items = []
            i += 1
            while i < len(lines) and _BLOCK_ITEM_RE.match(lines[i]):
                items.append(_unquote(_BLOCK_ITEM_RE.match(lines[i]).group(1)))
                i += 1
            fm[key] = items
            continue
        else:
            fm[key] = _unquote(rest)
        i += 1
    return fm, body


def serialize(frontmatter: dict, body: str) -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            if key in ALWAYS_LIST or len(value) != 1:
                inner = ", ".join(_quote_if_needed(v) for v in value)
                lines.append(f"{key}: [{inner}]")
            else:
                lines.append(f"{key}: {_quote_if_needed(value[0])}")
        elif isinstance(value, dict):
            inner = ", ".join(f"{k}: {v}" for k, v in value.items())
            lines.append(f"{key}: {{{inner}}}")
        else:
            lines.append(f"{key}: {_quote_if_needed(str(value))}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_frontmatter.py -v`
Expected: PASS, 12 tests

- [ ] **Step 5: Commit**

```bash
git add pipeline/frontmatter.py pipeline/tests/test_frontmatter.py
git commit -m "$(cat <<'EOF'
pipeline: real frontmatter parser (D6) for list/map-valued fields

Existing readers (api/notes.py::parse_frontmatter,
pipeline/relationships.py::parse_channels) only handle flat scalar
frontmatter. C1's union merge rule (next task) turns fields like
channels into list/map-valued ones once a second value is added, which
those readers can't represent. This is a small, narrow, round-trip
parser for exactly the shapes this vault's notes use — not a general
YAML implementation (locked deps forbid PyYAML, CLAUDE.md §7).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `pipeline/merge.py` — additive merge rules (C1)

**Files:**
- Create: `pipeline/merge.py`
- Test: `pipeline/tests/test_merge.py`

**Interfaces — Consumes:** `pipeline.frontmatter.parse/serialize` (Task 1).

**Interfaces — Produces:**
- `FIELD_KINDS: dict[str, str]` — maps field name → one of `"set_once" | "fill" | "union" | "forward" | "append_only"`. Unlisted fields default to `"fill"` (the safest default: never silently overwritten).
  ```python
  FIELD_KINDS = {
      "id": "set_once", "type": "set_once", "created": "set_once",
      "source": "set_once", "origin": "set_once",
      "last_contact": "forward",
      "channels": "union", "tags": "union", "categories": "union",
      "subjects": "union", "attendees": "union", "companies": "union",
      "dex_id": "union", "dex_deeplink": "union", "handshake_id": "union",
      "outreach_id": "union", "booking_uid": "union",
  }
  ```
- `normalize_email(s: str) -> str` — `s.strip().lower()`.
- `normalize_phone(s: str) -> str` — digits only (`re.sub(r"\D", "", s)`), last 10 kept for comparison by the caller (this function returns the full digit string; callers slice `[-10:]`).
- `slugify(name: str) -> str` — ASCII kebab: lowercase, non-alphanumeric → `-`, collapse repeats, strip edges; empty result → `""` (caller supplies a fallback).
- `apply_field(existing: dict, key: str, new_value, *, source: str, origin: str, today: str) -> tuple[dict, str | None]` — returns `(updated_fm, suggestion_line_or_None)`. Implements the five kinds:
  - `set_once`: if `key not in existing or not existing[key]`, sets it; else unchanged, no suggestion.
  - `fill`: if blank/absent, sets it, no suggestion. If already set to a **different** non-blank value, `existing` is unchanged and a suggestion line is returned: `f"- {today} · {key}: {existing[key]} → {new_value}? ({source}, {origin})"`. If set to the **same** value, no-op, no suggestion.
  - `union`: merges. Scalar existing + new scalar (different) → `[existing_value, new_value]` (dedup, order-preserving). Existing list + new scalar/list → append missing items (dedup case-insensitively for strings). `channels` (a dict) unions per-key: same rule per value under each channel key.
  - `forward`: parses both as `datetime.date.fromisoformat`; keeps the later one; unparseable existing value is treated as absent (new value wins, no crash).
  - `append_only`: not handled by `apply_field` (that's for scalar/collection fields) — sections are handled by `append_line` below.
- `append_line(body: str, section: str, line: str, marker: str) -> str` — appends `f"{line} {marker}\n"` under `## {section}` in `body`. Creates the section (at the end of the body) if absent. **Idempotent**: if a line ending in the exact same `marker` already exists anywhere in that section, `body` is returned unchanged.
- `owner_change(existing: dict, key: str, new_value: str, *, today: str) -> tuple[dict, str]` — always applies (the one path that replaces a Fill value), returns `(updated_fm, "- {today} · {key}: {old} → {new} (owner)")`. Caller is responsible for deciding a change IS an owner edit (D4) before calling this.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_merge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.merge'`

- [ ] **Step 3: Write `pipeline/merge.py`**

```python
"""C1's additive merge rules — the single implementation every person/
company writer in this repo (and, ported, Handshake's worker) uses so two
apps enriching the same note never overwrite each other. Pure functions:
no filesystem, no clock (the caller always passes `today`), so every rule
is directly testable and reusable by the vaultsync conflict resolver
(Task 3) without any I/O coupling."""
from __future__ import annotations

import re
from datetime import date

FIELD_KINDS = {
    "id": "set_once", "type": "set_once", "created": "set_once",
    "source": "set_once", "origin": "set_once",
    "last_contact": "forward",
    "channels": "union", "tags": "union", "categories": "union",
    "subjects": "union", "attendees": "union", "companies": "union",
    "dex_id": "union", "dex_deeplink": "union", "handshake_id": "union",
    "outreach_id": "union", "booking_uid": "union",
}


def normalize_email(s: str) -> str:
    return (s or "").strip().lower()


def normalize_phone(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)


def _dedupe_ci(items: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for item in items:
        key = item.lower()
        if key not in seen:
            seen[key] = item
    return list(seen.values())


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return list(value)
    if value in (None, ""):
        return []
    return [value]


def _union_value(existing, new):
    if isinstance(existing, dict) or isinstance(new, dict):
        existing_map = existing if isinstance(existing, dict) else {}
        new_map = new if isinstance(new, dict) else {}
        out = dict(existing_map)
        for key, value in new_map.items():
            if key not in out or not out[key]:
                out[key] = value
            else:
                out[key] = _union_scalar_list(out[key], value)
        return out
    return _union_scalar_list(existing, new)


def _union_scalar_list(existing, new):
    combined = _dedupe_ci(_as_list(existing) + _as_list(new))
    if len(combined) == 1:
        return combined[0]
    return combined


def apply_field(existing: dict, key: str, new_value, *, source: str, origin: str,
                today: str) -> tuple[dict, str | None]:
    kind = FIELD_KINDS.get(key, "fill")
    fm = dict(existing)
    current = fm.get(key)

    if kind == "set_once":
        if not current:
            fm[key] = new_value
        return fm, None

    if kind == "union":
        fm[key] = _union_value(current, new_value)
        return fm, None

    if kind == "forward":
        try:
            current_date = date.fromisoformat(current) if current else None
        except ValueError:
            current_date = None
        try:
            new_date = date.fromisoformat(new_value)
        except (ValueError, TypeError):
            return fm, None
        if current_date is None or new_date > current_date:
            fm[key] = new_value
        return fm, None

    # fill (default)
    if not current:
        fm[key] = new_value
        return fm, None
    if str(current) == str(new_value):
        return fm, None
    suggestion = f"- {today} · {key}: {current} → {new_value}? ({source}, {origin})"
    return fm, suggestion


def owner_change(existing: dict, key: str, new_value: str, *, today: str) -> tuple[dict, str]:
    old = existing.get(key, "")
    fm = dict(existing)
    fm[key] = new_value
    return fm, f"- {today} · {key}: {old} → {new_value} (owner)"


def append_line(body: str, section: str, line: str, marker: str) -> str:
    full_line = f"{line} {marker}"
    if marker in body:
        return body
    heading = f"## {section}"
    if heading not in body:
        sep = "" if body.endswith("\n") or not body else "\n"
        return f"{body}{sep}\n{heading}\n\n{full_line}\n"
    idx = body.index(heading)
    next_heading = body.find("\n## ", idx + len(heading))
    insert_at = next_heading if next_heading != -1 else len(body)
    section_slice = body[idx:insert_at].rstrip("\n")
    new_section = f"{section_slice}\n{full_line}\n"
    return body[:idx] + new_section + body[insert_at:]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_merge.py -v`
Expected: PASS, 17 tests

- [ ] **Step 5: Commit**

```bash
git add pipeline/merge.py pipeline/tests/test_merge.py
git commit -m "$(cat <<'EOF'
pipeline: additive merge rules for person/company notes (C1)

Set-once/Fill/Union/Forward-only/Append — the five merge kinds every
person and company writer in this repo will use so future integrations
(Handshake, cal.diy, Supabase — Steps 1-4 of the integration roadmap)
enrich the same note without ever overwriting each other or the owner's
own edits. A Fill collision never silently loses data: it appends a
dated suggestion line instead, surfaced for Accept/Dismiss in a later
pass. Pure functions, no filesystem or clock coupling, so the
vaultsync conflict resolver (next task) can reuse them directly.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `pipeline/vaultsync.py` — non-`https://` remotes + note-aware conflict resolver (D3)

**Files:**
- Modify: `pipeline/vaultsync.py`
- Modify: `pipeline/tests/test_vaultsync.py`

**Interfaces — Consumes:** `pipeline.frontmatter` (Task 1), `pipeline.merge` (Task 2).

**Interfaces — Produces:**
- `SyncResult` gains one new `status` value: `"resolved"` — a conflict occurred and was successfully auto-merged (distinct from `"ok"`, which means "no conflict at all"; both are non-error outcomes, but `"resolved"` is what the health probe and the Pipeline card key off of to explain *why* a commit happened without the owner touching anything).
- `_authed_url` unchanged for `https://` remotes; a remote with **no** `://` (a plain filesystem path, e.g. `/vault.git`) or an `ssh://`/`user@host:path` form is passed straight through — untouched, since neither carries a token.
- `RESOLVABLE_FOLDERS = {"07-People", "11-Companies", "12-Conversations", "06-Todos"}` — module constant. A conflict is only auto-resolved when **every** conflicted path's top-level folder is in this set; a conflict touching anything else (attachments, `_System/`, `raw/`) still aborts exactly as today.
- New pure function `resolve_conflict(ours: str, theirs: str, base: str) -> str | None` — three-way text resolve for one `*.md` file's full content. Returns the resolved text, or `None` if the file can't be resolved this way (binary content, or frontmatter that doesn't parse on either side) — `None` means the caller must abort the whole rebase, unchanged from today's behavior.
  - Frontmatter: parse all three (`frontmatter.parse`); for every key present in `ours` or `theirs`, `merge.apply_field(base_fm, key, ours_value)` then `apply_field(result, key, theirs_value)` — i.e. both sides are folded through the same merge rules against the common ancestor, so a Fill collision becomes a suggestion line (appended to the body below) rather than a silent pick of one side.
  - Body: start from `base` body's lines; every line present in `ours` body but not `base`, and every line present in `theirs` body but not `base`, gets appended in file order (ours first, then theirs) via `merge.append_line` when the line matches an `## Section` append-only shape, deduped by trailing `<!-- marker -->` when present, else by exact line match.
- `vaultsync.sync` behavior: on a rebase conflict, instead of an unconditional `rebase --abort`, list conflicted files (`git diff --name-only --diff-filter=U`); if every one is under `RESOLVABLE_FOLDERS` and every one resolves via `resolve_conflict`, write the resolved content, `git add`, `git rebase --continue`, return `SyncResult("resolved", ...)`. Otherwise, `rebase --abort` exactly as before, PLUS: call `errors.ntfy` (imported lazily to avoid a new top-level pipeline→pipeline coupling risk — actually same package, direct import is fine) at most once per 6 hours (`events.reminder_fired("vaultsync-alert-<hour//6>")`-style key passed in by the caller in Task 4, since `vaultsync.py` itself has no `EventLog` — see Task 4's wiring).

- [ ] **Step 1: Write the failing tests** — append to `pipeline/tests/test_vaultsync.py`:

```python
# ---- resolve_conflict (D3) -------------------------------------------------

def test_resolve_conflict_returns_none_for_unparseable_frontmatter():
    assert vaultsync.resolve_conflict("no frontmatter here", "also none", "base") is None


def test_resolve_conflict_merges_fill_field_as_suggestion():
    base = "---\nid: 1\ncompany: \n---\n# P\n\n## Updates\n\n"
    ours = "---\nid: 1\ncompany: Studio X\n---\n# P\n\n## Updates\n\n"
    theirs = "---\nid: 1\ncompany: Acme\n---\n# P\n\n## Updates\n\n"
    resolved = vaultsync.resolve_conflict(ours, theirs, base)
    fm, body = frontmatter.parse(resolved)
    # first writer to fill wins the field; the second becomes a suggestion
    assert fm["company"] in ("Studio X", "Acme")
    assert "## Updates" in body


def test_resolve_conflict_unions_channels_from_both_sides():
    base = "---\nid: 1\nchannels: {}\n---\nBody\n"
    ours = "---\nid: 1\nchannels: {email: a@b.c}\n---\nBody\n"
    theirs = "---\nid: 1\nchannels: {whatsapp: +971555}\n---\nBody\n"
    resolved = vaultsync.resolve_conflict(ours, theirs, base)
    fm, _ = frontmatter.parse(resolved)
    assert fm["channels"] == {"email": "a@b.c", "whatsapp": "+971555"}


def test_resolve_conflict_keeps_appended_lines_from_both_sides():
    base = "---\nid: 1\n---\n# P\n\n## Interaction log\n\n"
    ours = "---\nid: 1\n---\n# P\n\n## Interaction log\n\n- 2026-09-16 — called <!-- bc:a -->\n"
    theirs = "---\nid: 1\n---\n# P\n\n## Interaction log\n\n- 2026-09-16 — noted <!-- vq:b -->\n"
    resolved = vaultsync.resolve_conflict(ours, theirs, base)
    assert "<!-- bc:a -->" in resolved
    assert "<!-- vq:b -->" in resolved


# ---- sync() auto-resolves a real rebase conflict --------------------------

def test_sync_auto_resolves_conflicting_person_note(bare_remote, tmp_path):
    origin = _init_vault(tmp_path / "origin")
    (origin / "07-People").mkdir()
    note = origin / "07-People" / "priya.md"
    note.write_text("---\nid: 1\nchannels: {}\n---\n# Priya\n\n## Interaction log\n\n")
    _run("add", "-A", cwd=origin)
    _run("commit", "-q", "-m", "seed", cwd=origin)
    _run("push", "-q", f"file://{bare_remote}", "HEAD:main", cwd=origin)

    a = _clone(bare_remote, tmp_path / "a")
    b = _clone(bare_remote, tmp_path / "b")

    (a / "07-People" / "priya.md").write_text(
        "---\nid: 1\nchannels: {email: a@b.c}\n---\n# Priya\n\n## Interaction log\n\n"
        "- 2026-09-16 — called from A <!-- bc:a -->\n")
    _run("add", "-A", cwd=a)
    _run("commit", "-q", "-m", "a edits", cwd=a)
    _run("push", "-q", f"file://{bare_remote}", "HEAD:main", cwd=a)

    (b / "07-People" / "priya.md").write_text(
        "---\nid: 1\nchannels: {whatsapp: +971555}\n---\n# Priya\n\n## Interaction log\n\n"
        "- 2026-09-16 — noted from B <!-- vq:b -->\n")
    _run("add", "-A", cwd=b)
    _run("commit", "-q", "-m", "b edits", cwd=b)

    cfg = config({"remote": f"file://{bare_remote}"})
    result = vaultsync.sync(b, cfg)

    assert result.status == "resolved"
    merged = (b / "07-People" / "priya.md").read_text()
    fm, body = frontmatter.parse(merged)
    assert fm["channels"] == {"email": "a@b.c", "whatsapp": "+971555"}
    assert "<!-- bc:a -->" in body
    assert "<!-- vq:b -->" in body


def test_sync_still_aborts_on_unresolvable_conflict(bare_remote, tmp_path):
    origin = _init_vault(tmp_path / "origin")
    (origin / "attachments").mkdir()
    photo = origin / "attachments" / "img.txt"
    photo.write_text("binary-ish content v1")
    _run("add", "-A", cwd=origin)
    _run("commit", "-q", "-m", "seed", cwd=origin)
    _run("push", "-q", f"file://{bare_remote}", "HEAD:main", cwd=origin)

    a = _clone(bare_remote, tmp_path / "a")
    b = _clone(bare_remote, tmp_path / "b")

    (a / "attachments" / "img.txt").write_text("binary-ish content v2 from A")
    _run("add", "-A", cwd=a)
    _run("commit", "-q", "-m", "a edits", cwd=a)
    _run("push", "-q", f"file://{bare_remote}", "HEAD:main", cwd=a)

    (b / "attachments" / "img.txt").write_text("binary-ish content v2 from B")
    _run("add", "-A", cwd=b)
    _run("commit", "-q", "-m", "b edits", cwd=b)

    cfg = config({"remote": f"file://{bare_remote}"})
    result = vaultsync.sync(b, cfg)

    assert result.status == "conflict"
    assert (b / "attachments" / "img.txt").read_text() == "binary-ish content v2 from B"


def test_path_remote_untouched_by_url_authing():
    assert vaultsync._authed_url("/vault.git", "some-token") == "/vault.git"


def test_ssh_remote_untouched_by_url_authing():
    remote = "ssh://root@2.29.35.159/root/vault.git"
    assert vaultsync._authed_url(remote, "some-token") == remote
```

Add `from pipeline import frontmatter` to the test file's imports.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_vaultsync.py -v -k "resolve_conflict or auto_resolves or still_aborts or remote_untouched"`
Expected: FAIL — `resolve_conflict` doesn't exist yet; the two `_authed_url` tests currently pass already (no `://` means passthrough already works per the existing implementation) — confirm those two still pass unmodified, they're regression pins, not new behavior.

- [ ] **Step 3: Implement in `pipeline/vaultsync.py`**

Add imports at the top:
```python
from pipeline import frontmatter, merge
```

Add the module constant near the top (after `REMOTE_REF_TEMPLATE`):
```python
RESOLVABLE_FOLDERS = {"07-People", "11-Companies", "12-Conversations", "06-Todos"}
```

Add `resolve_conflict` as a new top-level function:
```python
def resolve_conflict(ours: str, theirs: str, base: str) -> str | None:
    """Three-way text resolve for one conflicted note, using C1's merge
    rules (pipeline/merge.py) instead of picking a side. Returns None when
    the file isn't resolvable this way (unparseable frontmatter on either
    side) — the caller aborts the rebase exactly as before in that case."""
    base_fm, base_body = frontmatter.parse(base)
    ours_fm, ours_body = frontmatter.parse(ours)
    theirs_fm, theirs_body = frontmatter.parse(theirs)
    if not ours_fm or not theirs_fm:
        return None

    today = date.today().isoformat()
    result_fm = dict(base_fm)
    suggestions: list[str] = []
    for key in set(ours_fm) | set(theirs_fm):
        ours_value = ours_fm.get(key)
        if ours_value not in (None, ""):
            result_fm, s = merge.apply_field(result_fm, key, ours_value,
                                              source="vault", origin="human", today=today)
            if s:
                suggestions.append(s)
        theirs_value = theirs_fm.get(key)
        if theirs_value not in (None, ""):
            result_fm, s = merge.apply_field(result_fm, key, theirs_value,
                                              source="vault", origin="human", today=today)
            if s:
                suggestions.append(s)

    result_body = base_body
    for side_body in (ours_body, theirs_body):
        for line in side_body.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- ") or stripped in base_body:
                continue
            marker_match = re.search(r"(<!--\s*\S+:\S+\s*-->)\s*$", stripped)
            marker = marker_match.group(1) if marker_match else stripped
            if marker in result_body:
                continue
            section = _section_for_line(side_body, line)
            if section is None:
                continue
            text = stripped[: marker_match.start()].rstrip() if marker_match else stripped
            result_body = merge.append_line(result_body, section, text,
                                            marker_match.group(1) if marker_match else "")

    for suggestion in suggestions:
        result_body = merge.append_line(result_body, "Updates", suggestion, "")

    return frontmatter.serialize(result_fm, result_body)


def _section_for_line(body: str, line: str) -> str | None:
    idx = body.find(line)
    if idx == -1:
        return None
    heading_idx = body.rfind("\n## ", 0, idx)
    if heading_idx == -1:
        return None
    heading_line = body[heading_idx + 1:body.index("\n", heading_idx + 1)]
    return heading_line.removeprefix("## ").strip()
```

Add `import re` and `from datetime import date` to the top-of-file imports (both stdlib, both already used elsewhere in this package).

Modify `_sync` — replace the conflict branch:
```python
        rebase = _git(vault, ["rebase", ref])
        if rebase.returncode != 0:
            resolved = _try_resolve_conflicts(vault)
            if not resolved:
                _git(vault, ["rebase", "--abort"])
                return SyncResult(
                    "conflict",
                    "The vault and the remote both changed the same note. The rebase was aborted "
                    "and your vault is untouched — pull normally, resolve the conflict by hand "
                    "(in Obsidian or git), then sync again.")
```

Add `_try_resolve_conflicts` as a new module-level function (above `_sync`):
```python
def _try_resolve_conflicts(vault: Path) -> bool:
    """Attempt to auto-resolve every conflicted file via C1's merge rules.
    Returns True (rebase continued) only when EVERY conflicted path is
    under RESOLVABLE_FOLDERS and resolves cleanly — a single unresolvable
    file aborts the whole attempt, leaving the caller to `rebase --abort`
    exactly as before. Never partially applies."""
    listing = _git(vault, ["diff", "--name-only", "--diff-filter=U"])
    if listing.returncode != 0:
        return False
    conflicted = [line for line in listing.stdout.splitlines() if line.strip()]
    if not conflicted:
        return False
    if not all(Path(p).parts and Path(p).parts[0] in RESOLVABLE_FOLDERS for p in conflicted):
        return False

    resolutions: dict[str, str] = {}
    for rel_path in conflicted:
        base = _show_stage(vault, rel_path, 1)
        ours = _show_stage(vault, rel_path, 2)
        theirs = _show_stage(vault, rel_path, 3)
        if base is None or ours is None or theirs is None:
            return False
        resolved = resolve_conflict(ours, theirs, base)
        if resolved is None:
            return False
        resolutions[rel_path] = resolved

    for rel_path, content in resolutions.items():
        (vault / rel_path).write_text(content, encoding="utf-8")
        _git(vault, ["add", rel_path])
    continue_result = _git(vault, ["rebase", "--continue"])
    return continue_result.returncode == 0


def _show_stage(vault: Path, rel_path: str, stage: int) -> str | None:
    result = _git(vault, ["show", f":{stage}:{rel_path}"])
    return result.stdout if result.returncode == 0 else None
```

Modify the return path after a successful `_try_resolve_conflicts` — in `_sync`, right after the conflict-handling block above, before the `push = _git(...)` line, track whether resolution happened:
```python
        rebase = _git(vault, ["rebase", ref])
        was_resolved = False
        if rebase.returncode != 0:
            was_resolved = _try_resolve_conflicts(vault)
            if not was_resolved:
                _git(vault, ["rebase", "--abort"])
                return SyncResult(
                    "conflict",
                    "The vault and the remote both changed the same note. The rebase was aborted "
                    "and your vault is untouched — pull normally, resolve the conflict by hand "
                    "(in Obsidian or git), then sync again.")
```
And at the very end of `_sync`, change the final return:
```python
    ahead, behind = ahead_behind(vault, branch)
    status = "resolved" if was_resolved else "ok"
    detail = "Vault synced (auto-resolved a conflict)." if was_resolved else "Vault synced."
    return SyncResult(status, detail, ahead=ahead, behind=behind)
```
`was_resolved` must be initialized to `False` before the `if not remote_is_empty:` block (it's referenced after that block whether or not it ran).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_vaultsync.py -v`
Expected: PASS, all tests including the pre-existing ones (no regression)

- [ ] **Step 5: Commit**

```bash
git add pipeline/vaultsync.py pipeline/tests/test_vaultsync.py
git commit -m "$(cat <<'EOF'
pipeline: vaultsync auto-resolves note conflicts via C1 merge rules (D3)

Today a rebase conflict on ANY file aborts the whole sync and stays
silently stuck until someone notices — the exact failure mode that
made the two-vault split invisible for weeks. When every conflicted
path is a person/company/conversation/todo note, this resolves each
one through merge.py's Fill/Union/Forward rules against the common
ancestor (a Fill collision becomes an Updates suggestion line, not a
coin flip) and continues the rebase automatically. Anything else
conflicted — attachments, _System/, raw/ — still aborts exactly as
before; the vault is never left half-migrated.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: vault sync health — events.db tracking + ntfy alert + build-tracker probe

**Files:**
- Modify: `pipeline/events.py`
- Modify: `pipeline/watcher.py` (the `sync_vault` tick)
- Modify: `api/build_status.py`
- Modify: `checks.json`
- Test: `pipeline/tests/test_events.py` (create if absent — check first), `pipeline/tests/test_watcher_vaultsync.py`, `api/tests/test_build_status.py`

**Interfaces — Produces:**
- `EventLog.last_vault_sync_ok() -> str | None` — ISO timestamp of the most recent `stage='vault_sync'` event whose `status` is `'ok'` or `'resolved'`, or `None` if none exists.
- `_probe_vault_sync_healthy(app_root, item, config, db_path)` registered in `api/build_status.py` `_PROBES["vault_sync_healthy"]` — `True` when `last_vault_sync_ok` is within `item.get("max_age_hours", 2)` hours of now; a `no-remote` result is `True` too (sync genuinely isn't configured — that's not "unhealthy", it's a different, already-covered state via `deploy-tunnel`-style config checks... actually: **not configured is False here**, matching every other "not yet wired" probe's convention — see Step below).
- `watcher.sync_vault` calls `errors.ntfy` at most once per 6 hours when a tick's `SyncResult.status == "conflict"`, using `events.reminder_fired("vaultsync-alert")` / `mark_reminder` with a time-bucketed key so it can re-fire after the throttle window (`f"vaultsync-alert-{int(time.time() // 21600)}"`).

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/tests/test_watcher_vaultsync.py` (read the existing file first to match its fixture style before writing — it already builds a `Config`/`EventLog` pair for `sync_vault`):
```python
def test_sync_vault_ntfy_on_conflict_once_per_window(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(errors, "ntfy", lambda *a, **k: calls.append(a))
    # ... arrange a config/events pair whose vaultsync.sync() would return
    # SyncResult("conflict", ...) — reuse this file's existing fixture
    # helpers for that setup, then call watcher.sync_vault(config, events)
    # twice and assert len(calls) == 1 (second call within the same
    # 6-hour bucket is throttled).
```
(Implementer note: this file already has helpers for constructing a conflicting vaultsync scenario for its pre-existing tests — reuse them; don't re-derive the git setup.)

Create/append to `api/tests/test_build_status.py`:
```python
def test_vault_sync_healthy_true_when_recent(tmp_path):
    # seed events.db with one stage='vault_sync' status='ok' event at now
    ...
    ok, detail = _probe_vault_sync_healthy(REPO_ROOT, {"max_age_hours": 2}, config, db_path)
    assert ok is True


def test_vault_sync_healthy_false_when_stale(tmp_path):
    # seed one event 5 hours old, max_age_hours=2
    ...
    ok, detail = _probe_vault_sync_healthy(REPO_ROOT, {"max_age_hours": 2}, config, db_path)
    assert ok is False


def test_vault_sync_healthy_false_when_never_synced(tmp_path):
    ok, detail = _probe_vault_sync_healthy(REPO_ROOT, {}, config, empty_db_path)
    assert ok is False
```
(Implementer note: follow this file's existing pattern for seeding events.db and constructing `config`/`db_path` fixtures — copy the shape used by neighboring probe tests in the same file rather than inventing a new one.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_watcher_vaultsync.py api/tests/test_build_status.py -v -k "vault_sync_healthy or ntfy_on_conflict"`
Expected: FAIL — function/attribute doesn't exist yet

- [ ] **Step 3: Implement**

`pipeline/events.py` — add method to `EventLog` (no schema change needed; `events` table already has `stage`/`status`/`timestamp`):
```python
    def last_vault_sync_ok(self) -> str | None:
        """ISO timestamp of the most recent healthy sync (ok or resolved),
        for the build tracker's vault_sync_healthy probe. Disposable —
        events.db loss just means the probe reads 'unknown' until the next
        successful tick, same as every other events.db-backed signal here."""
        cur = self.conn.execute(
            "SELECT timestamp FROM events WHERE stage='vault_sync' "
            "AND status IN ('ok','resolved') ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        return row[0] if row else None
```

`pipeline/watcher.py` — modify `sync_vault`:
```python
def sync_vault(config, events: EventLog) -> None:
    """Push/pull the vault's own git history to its configured remote (Pass
    H1 — the island fix, F1; conflict auto-resolution added in D3). A quiet
    no-op when VAULT_GIT_REMOTE isn't set; never raises."""
    if vaultsync.remote_config(config) is None:
        return
    result = vaultsync.sync(Path(config.vault_path), config)
    events.log(str(config.vault_path), "vault_sync",
              "ok" if result.status in ("ok", "resolved") else "failed",
              message=f"status={result.status} ahead={result.ahead} behind={result.behind}"
                      + (f" — {result.detail}" if result.detail else ""))
    if result.status == "conflict":
        window_key = f"vaultsync-alert-{int(time.time() // 21600)}"
        if not events.reminder_fired(window_key):
            errors.ntfy(config.ntfy_url, config.ntfy_topic,
                        "Vault sync is stuck — two machines edited the same note "
                        "in a way that couldn't be auto-merged. Open Pipeline to see it.",
                        title="Brain Cockpit — vault sync stuck")
            events.mark_reminder(window_key)
```
(`errors` and `time` are already imported at the top of `pipeline/watcher.py` — verify before adding; if `errors` isn't imported, add `from pipeline import errors`.)

`api/build_status.py` — add the probe function and register it:
```python
def _probe_vault_sync_healthy(app_root: Path, item: dict, config, db_path: Path):
    """Sync being CONFIGURED is a separate question (covered by
    deploy-tunnel-style config checks elsewhere) — this probe only answers
    "is it actually working right now", so an unconfigured or never-synced
    vault reads False here, same convention as every other not-yet-wired
    milestone in this file."""
    if config is None:
        return False, "config.json doesn't exist yet."
    from pipeline.events import EventLog
    try:
        events = EventLog(db_path, config.vault_path)
        last = events.last_vault_sync_ok()
    except Exception:
        return False, "events.db couldn't be read."
    if not last:
        return False, "The vault has never synced successfully yet."
    from datetime import datetime
    age_hours = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 3600
    max_age = item.get("max_age_hours", 2)
    ok = age_hours <= max_age
    return ok, (f"Last healthy sync {age_hours:.1f}h ago." if ok
                else f"Last healthy sync was {age_hours:.1f}h ago — over the {max_age}h window.")
```
Register in `_PROBES`:
```python
    "vault_sync_healthy": _probe_vault_sync_healthy,
```

`checks.json` — add one item to the `Deployment` phase (find the existing `deploy-tunnel` item and add this after it):
```json
    { "id": "vault-sync-healthy", "phase": "Deployment", "label": "Vault sync is healthy",
      "type": "vault_sync_healthy", "max_age_hours": 2,
      "next_action": "Check the Pipeline screen for a stuck-sync card, or run the sync manually." },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_watcher_vaultsync.py api/tests/test_build_status.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/events.py pipeline/watcher.py api/build_status.py checks.json \
        pipeline/tests/test_watcher_vaultsync.py api/tests/test_build_status.py
git commit -m "$(cat <<'EOF'
pipeline+api: vault sync health signal — ntfy alert + build-tracker probe

A stuck vault sync today fails silently forever (the original two-vault
split went unnoticed for weeks because of exactly this). Now: a
conflict that can't auto-resolve (Task 3) pushes one ntfy alert per
6-hour window, and a new vault_sync_healthy build-tracker probe reads
events.db's last successful sync timestamp so the build screen states
plainly whether sync is actually current, not just configured.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `config.timezone` + IST-aware todo dates

**Files:**
- Modify: `pipeline/config.py`
- Modify: `config.example.json`
- Test: `pipeline/tests/test_config.py` (check if it exists first; create if not)

**Interfaces — Produces:** `Config.timezone: str = "UTC"` field; `Config.tzinfo` property returning `zoneinfo.ZoneInfo(self.timezone)` (stdlib, no dependency). This is infrastructure only in this task — Steps 2/3 of the roadmap (cal.diy, conversations) are what actually consume `tzinfo` for todo-file dates; this task just makes the field exist and be correct, so later passes don't each reinvent config plumbing.

- [ ] **Step 1: Write the failing test**

```python
"""pipeline/tests/test_config.py (add if the file doesn't already exist;
if it does, append these two tests to it)."""
import json
from pathlib import Path

from pipeline import config as config_module


def test_timezone_defaults_to_utc(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "vault_path": str(tmp_path / "vault"), "inbox_path": str(tmp_path / "inbox"),
        "archive_path": str(tmp_path / "archive"), "failed_path": str(tmp_path / "failed"),
    }))
    cfg = config_module.load(cfg_path)
    assert cfg.timezone == "UTC"
    assert cfg.tzinfo.key == "UTC"


def test_timezone_reads_from_config_json(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "vault_path": str(tmp_path / "vault"), "inbox_path": str(tmp_path / "inbox"),
        "archive_path": str(tmp_path / "archive"), "failed_path": str(tmp_path / "failed"),
        "timezone": "Asia/Kolkata",
    }))
    cfg = config_module.load(cfg_path)
    assert cfg.timezone == "Asia/Kolkata"
    assert cfg.tzinfo.key == "Asia/Kolkata"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest pipeline/tests/test_config.py -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'timezone'`

- [ ] **Step 3: Implement**

`pipeline/config.py` — add field and property, add `zoneinfo` import:
```python
from zoneinfo import ZoneInfo
```
```python
    timezone: str = "UTC"
```
(add alongside the other defaulted fields in the `Config` dataclass)
```python
    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)
```
And in `load()`:
```python
        timezone=data.get("timezone", "UTC"),
```

`config.example.json` — add near the top-level keys (alongside `vault_path` etc.):
```json
  "timezone": "Asia/Kolkata",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest pipeline/tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/config.py config.example.json pipeline/tests/test_config.py
git commit -m "$(cat <<'EOF'
pipeline: config.timezone (stdlib zoneinfo) for owner-local todo dates

Infrastructure for the integration roadmap's Steps 2-3 (conversations,
cal.diy bookings) — both need to write todo-file dates in the owner's
own timezone, not the server's UTC. This task only adds the config
field and Config.tzinfo property; nothing consumes it yet.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: SCHEMA-REFERENCE.md — vault schema additions (repo copy)

**Files:**
- Modify: `SCHEMA-REFERENCE.md`

This is the **repo's own copy**; the operator applies the matching edit to the vault's `_System/SCHEMA-REFERENCE.md` by hand as one of the manual steps below (CLAUDE.md §2 treats this file as law read fresh each session — the repo copy and the vault copy must never drift, so both get the identical text).

- [ ] **Step 1: Add new frontmatter fields to the Person section**

In the `### Person (07-People)` YAML block, add two lines after `dex_deeplink:`:
```yaml
handshake_id:
outreach_id:
```

- [ ] **Step 2: Add `## Facts` and `## Updates` to the Person body sections**

Change the line:
```
Body: `## Context` · `## Needs` · `## Interaction log` (append-only, dated) · `## Next action`.
```
to:
```
Body: `## Context` · `## Needs` · `## Facts` (append-only, dated — one fact per line, citing its source with `derived-from::` when it comes from a conversation note) · `## Interaction log` (append-only, dated) · `## Next action` · `## Updates` (append-only, dated — proposed field changes from another app that would overwrite an already-filled value; never applied automatically, see the merge-rules table below).
```

- [ ] **Step 3: Add `handshake_id`/`outreach_id` to the Company section**

In the `### Company (11-Companies)` YAML block, add after `domain:`:
```yaml
handshake_id:
outreach_id:
```

- [ ] **Step 4: Add the C1 merge-rule table as a new subsection**

After the Company section's body-sections line, before the Conversation section, insert:
```markdown
### Cross-app merge rules (multiple writers, one note)

Person and company notes can be enriched by more than one system (the cockpit pipeline, Handshake, the outreach cockpit). No writer ever overwrites another's or the owner's data. Every field/section falls into one of five kinds:

| Kind | Rule |
| ----- | ----- |
| Set-once | written at creation, never changed (`id, type, created, source, origin`) |
| Fill | set only if blank; if already set to a different value, the new value is appended as a dated suggestion under `## Updates` instead of applied |
| Union | merges without removing — a second value for the same field becomes a list (`channels, tags, dex_id, handshake_id, outreach_id, …`) |
| Forward-only | only moves forward in time (`last_contact`) |
| Append | add dated lines, never edit or delete existing ones (`## Context, ## Needs, ## Facts, ## Interaction log, ## Updates`) |

The one thing that DOES replace a Filled value: the owner editing it directly (in Obsidian or the cockpit). That always applies, and appends `- {date} · {field}: {old} → {new} (owner)` under `## Updates` so the history is never lost.

Appended lines end with an idempotency marker (`<!-- bc:… -->` for cockpit, `<!-- vq:… -->` for Handshake) so re-applying the same fact twice is a no-op.
```

- [ ] **Step 5: Add the vault `.gitignore` note and naming clarifications**

In the `## 9. NAMING CONVENTIONS` section, after the existing "Exception" paragraph about handshake filenames, add:
```markdown
* The vault's own `.gitignore` excludes `.obsidian/workspace*.json`, `.obsidian/cache`, `.trash/`, and `.DS_Store` — these churn on every Obsidian focus/close and would conflict on nearly every sync between two machines.
```

- [ ] **Step 6: Commit**

```bash
git add SCHEMA-REFERENCE.md
git commit -m "$(cat <<'EOF'
docs: SCHEMA-REFERENCE — handshake_id/outreach_id, Facts/Updates sections,
cross-app merge rules (C1)

Repo copy of the schema law, ahead of Steps 1-4 of the integration
roadmap. The vault's own _System/SCHEMA-REFERENCE.md gets the identical
edit by hand as part of Step 0's manual steps — CLAUDE.md §2 treats
this file as authoritative and re-read every session, so the two
copies must never drift.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### GATE 0

- [ ] `.venv/bin/python -m pytest` — all green, including every new test file above
- [ ] `cd web && npx tsc --noEmit && npm run build` — unaffected by this plan (no frontend changes), confirm still green
- [ ] `node web/e2e/run-e2e.mjs` — unaffected, confirm still green
- [ ] checks.json validates as JSON (`python3 -c "import json; json.load(open('checks.json'))"`) and the new `vault-sync-healthy` item's `type` matches a registered probe
- [ ] `git log --oneline` shows the six commits above, in order
- [ ] `git pull --rebase origin main` then push

---

## Manual steps (owner/operator — NOT implementer tasks)

These happen after GATE 0 is green and merged to main, and are run by the owner directly (SSH, Finder, a text editor) — no subagent executes these, and they are listed here only so nothing from the roadmap's Step 0 is lost.

1. **Server: create the bare hub repo.**
   ```bash
   ssh root@2.29.35.159
   cd /root/personal-brain-os
   git init --bare vault.git
   ```
2. **Server: mount it into the container.** Edit `docker-compose.yml`'s `app` service `volumes:` to add:
   ```yaml
         - ./vault.git:/vault.git
   ```
3. **Mac: apply the SCHEMA-REFERENCE.md edit** (Task 6's text) to `~/Vibe Code/MyBrain-vault/MyBrain/_System/SCHEMA-REFERENCE.md`, create `12-Conversations/` and `Templates/Conversation.md`, add the vault `.gitignore` (the four lines from Task 6 Step 5), update the README folder table to include `11-Companies` and `12-Conversations`. Commit these as one commit in the Mac vault.
4. **Mac: push to the new hub.**
   ```bash
   cd "~/Vibe Code/MyBrain-vault/MyBrain"
   git remote add origin root@2.29.35.159:/root/personal-brain-os/vault.git
   git push -u origin main
   ```
5. **Server: one-time merge of server-only captures.**
   ```bash
   cd /root/personal-brain-os
   mv vault vault.pre-merge
   git clone /root/personal-brain-os/vault.git vault
   cp -r vault.pre-merge/00-Inbox/* vault/00-Inbox/ 2>/dev/null
   cp -r vault.pre-merge/02-Musings/* vault/02-Musings/ 2>/dev/null
   cp -r vault.pre-merge/06-Todos/* vault/06-Todos/ 2>/dev/null
   cat vault.pre-merge/_System/capture_log.md >> vault/_System/capture_log.md 2>/dev/null
   cd vault && git add -A && git commit -m "merge: server captures into MyBrain" && git push
   ```
6. **Server: point the app at the new hub.** In `.env`:
   ```
   VAULT_GIT_REMOTE=/vault.git
   ```
   Then `docker compose up -d --force-recreate app`.
7. **Mac: unload Handshake's vault-worker and Plaud launchd jobs** (D1 — the server is now the only vault writer; the Handshake integration in Step 1 replaces the Mac worker with a cockpit tick). `launchctl unload ~/Library/LaunchAgents/<handshake-worker-plist>` and the Plaud one, per Handshake's own `workers/vault-worker/README.md`.
8. **Verify:** capture something on the phone, confirm it shows up in Mac Obsidian within ~20 minutes (the watcher's `sync_vault` tick runs every 5 min per `POLL_SECONDS`, so the actual bound is closer to 5-10 min — the operator should just confirm it lands, not clock-watch). Edit a note in Obsidian, confirm cockpit's search picks it up. Check the build tracker shows `vault-sync-healthy` DONE.

A Mac-side "pull for Obsidian, push owner edits" job (`scripts/vault_sync_mac.py` + a launchd plist) is deliberately **not** in this plan — with D1 (server is the only writer), the Mac side only needs `git pull --rebase` on a schedule for Obsidian to see updates, which is a one-line cron/launchd entry the operator can set up directly (`*/10 * * * * cd ~/Vibe\ Code/MyBrain-vault/MyBrain && git pull --rebase --autostash`) without needing repo code. If the owner later wants push-from-Obsidian-edits automated too (rather than relying on Obsidian's own periodic git plugin, if one is installed), that's a small follow-up, not blocking Step 1.
