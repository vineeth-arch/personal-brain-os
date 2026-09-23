"""Resolve one of another app's people to a vault person note.

Read-only, stdlib only (CLAUDE.md §7), no outbound calls (rule 4). Exists so
Handshake can find a person without guessing by name: `handshake_id` is
already a `union` field in pipeline/merge.py, reserved for exactly this.

Phone and email are looked up SEPARATELY and a disagreement is reported as
`ambiguous` rather than resolved arbitrarily — the same discipline Handshake
applies in lib/people/match.ts:19-26.
"""
from __future__ import annotations

from pathlib import Path

from pipeline import merge, relationships


def _channel_values(person) -> list[str]:
    # channels is dict[str, str] keyed by kind (whatsapp/email/linkedin), so
    # the phone lives under `whatsapp`. Comparing every value against both
    # normalizers is safe: normalize_phone strips non-digits, so an email
    # becomes "" and never equals a wanted number, and vice versa.
    return [v for v in (person.channels or {}).values() if v]


def _phone_key(s: str) -> str:
    """Last nine digits, or "" if there are fewer. Country-code and
    formatting differences are not identity differences: a note written
    "050 123 4567" must match Handshake's "+971501234567". Nine, not ten,
    because a local number keeps its trunk 0 ("0501234567") while the
    international form drops it ("971501234567"), so the last ten differ in
    their first digit. Nine covers both UAE (9-digit national numbers) and
    India (10-digit). Handshake's lib/vault/adopt.ts uses the same key."""
    digits = merge.normalize_phone(s)
    return digits[-9:] if len(digits) >= 9 else ""


def find_by_external(vault_path: Path, *, handshake_id: str = "",
                     email: str = "", phone: str = ""):
    """Returns (person|None, basis, ambiguous).

    basis is one of "handshake_id" | "email" | "phone" | "".
    Resolution order is strongest key first: an explicit handshake_id wins
    outright, then phone (uniquely indexed on Handshake's side), then email
    (weaker — an address can be shared).
    """
    people = relationships.load_people(vault_path)

    wanted = (handshake_id or "").strip()
    if wanted:
        hits = [p for p in people if p.handshake_id.strip() == wanted]
        if hits:
            return hits[0], "handshake_id", len(hits) > 1

    email_hit = None
    wanted_email = merge.normalize_email(email)
    if wanted_email:
        hits = [p for p in people
                if any(merge.normalize_email(v) == wanted_email
                       for v in _channel_values(p))]
        email_hit = hits[0] if hits else None

    phone_hit = None
    wanted_phone = _phone_key(phone)
    if wanted_phone:
        hits = [p for p in people
                if any(_phone_key(v) == wanted_phone
                       for v in _channel_values(p))]
        phone_hit = hits[0] if hits else None

    if phone_hit and email_hit and phone_hit.id != email_hit.id:
        return phone_hit, "phone", True
    if phone_hit:
        return phone_hit, "phone", False
    if email_hit:
        return email_hit, "email", False
    return None, "", False
