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
    # v2.2 (SCHEMA-REFERENCE.md §7 cross-app merge table)
    "relationship": "union", "last_give": "forward", "last_ask": "forward",
    "quiet_until": "forward", "referred_by": "set_once",
    # Owner-only: an AI writer may suggest, never set (literal list — no
    # import from pipeline.relationships, to keep this module dependency-free).
    "tier": "owner", "energy": "owner", "known_for": "owner",
    "recall_trigger": "owner", "conversation_stage": "owner",
    "buyer_role": "owner", "fit": "owner", "list_of_20": "owner",
}


def normalize_email(s: str) -> str:
    return (s or "").strip().lower()


def normalize_phone(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9-\s]+", "", (name or "").lower()).strip("-")
    s = re.sub(r"\s+", "-", s).strip("-")
    return s


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
    # Track whether original was a list
    original_was_list = isinstance(existing, list)
    combined = _dedupe_ci(_as_list(existing) + _as_list(new))
    if len(combined) == 1 and not original_was_list:
        return combined[0]
    return combined if combined else (existing or "")


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
        except (ValueError, TypeError):
            current_date = None
        try:
            new_date = date.fromisoformat(new_value)
        except (ValueError, TypeError):
            return fm, None
        if current_date is None or new_date > current_date:
            fm[key] = new_value
        return fm, None

    if kind == "owner":
        if str(current or "") == str(new_value or ""):
            return fm, None                      # no-op, no Updates spam (vaultsync)
        if origin == "human":
            fm[key] = new_value                  # the owner's own edit arriving via sync
            return fm, None
        return fm, f"- {today} · {key}: {current or '(blank)'} → {new_value}? ({source}, {origin})"

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
