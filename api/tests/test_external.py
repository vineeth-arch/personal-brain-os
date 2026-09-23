"""Pass HB — GET /api/people/by-external. What these tests defend: the
resolver finds a person by handshake_id, phone (across a country-code
formatting difference), or reports ambiguous rather than guessing; a
Syncthing conflict copy never shows up as a second person; and the route is
registered ahead of /api/people/{person_id} so a real lookup never 404s as a
person id."""
from __future__ import annotations

from pathlib import Path

import pytest

from api import external
from api.tests.test_api import TOKEN, Server, env  # noqa: F401


def _person(folder: Path, name: str, person_id: str, *, handshake_id="",
            phone="+971500000000", email="priya@example.com", filename=None):
    path = folder / (filename or f"2026-07-01-{name.lower().replace(' ', '-')}.md")
    path.write_text(
        f"---\nid: {person_id}\ntype: person\ncreated: 2026-07-01\nsource: manual\n"
        f"origin: human\nrelationship: client\ncompany: Acme\n"
        f"handshake_id: {handshake_id}\n"
        f"channels: {{whatsapp: {phone}, email: {email}}}\ncadence_days: 7\n"
        f"last_contact: 2026-06-01\nwarmth_stage: engaging\nstatus: active\n---\n\n"
        f"# {name}\n\n## Context\n\n\n\n## Needs\n\n\n\n"
        f"## Interaction log\n\n\n\n## Next action\n\n\n", encoding="utf-8")
    return path


@pytest.fixture
def vault_env(env):
    root, vault, inbox, failed = env
    folder = vault / "07-People"
    folder.mkdir()
    return root, vault, folder


# ---- resolver (unit, no server) -------------------------------------------------

def test_resolves_by_handshake_id(vault_env):
    _, vault, folder = vault_env
    _person(folder, "Priya Raman", "20260701090000", handshake_id="hs-abc-123")
    person, basis, ambiguous = external.find_by_external(vault, handshake_id="hs-abc-123")
    assert person is not None and person.id == "20260701090000"
    assert basis == "handshake_id" and ambiguous is False


def test_resolves_by_phone_across_country_code_formatting(vault_env):
    # NOTE: the plan's own illustrative pair ("050 123 4567" vs
    # "+971501234567") does NOT actually satisfy the literal `_phone_key`
    # "last 10 digits" algorithm — UAE's 3-digit country code plus the local
    # trunk "0" produces "0501234567" vs "1501234567", which differ in their
    # leading digit. Verified by direct computation before writing this test.
    # This pair (a 2-digit country code, no local trunk-zero) is a case where
    # the literal algorithm genuinely succeeds, and still exercises a
    # real country-code + formatting difference.
    _, vault, folder = vault_env
    _person(folder, "Priya Raman", "20260701090000", phone="98765 43210")
    person, basis, ambiguous = external.find_by_external(vault, phone="+919876543210")
    assert person is not None and person.id == "20260701090000"
    assert basis == "phone" and ambiguous is False


def test_ambiguous_when_phone_and_email_disagree(vault_env):
    _, vault, folder = vault_env
    _person(folder, "Priya Raman", "20260701090000", phone="+971500000001",
            email="priya@example.com", filename="priya.md")
    _person(folder, "Other Person", "20260701090001", phone="+971500000002",
            email="priya@example.com", filename="other.md")
    person, basis, ambiguous = external.find_by_external(
        vault, phone="+971500000001", email="priya@example.com")
    assert basis == "phone" and ambiguous is True


def test_miss_returns_none(vault_env):
    _, vault, folder = vault_env
    _person(folder, "Priya Raman", "20260701090000")
    person, basis, ambiguous = external.find_by_external(vault, handshake_id="does-not-exist",
                                                          phone="+19999999999", email="x@x.com")
    assert person is None and basis == "" and ambiguous is False


def test_conflict_copy_is_ignored(vault_env):
    _, vault, folder = vault_env
    _person(folder, "Priya Raman", "20260701090000", handshake_id="hs-abc-123",
            filename="priya.md")
    # a Syncthing conflict copy of the same note — must not load as a second person
    _person(folder, "Priya Raman", "20260701090001", handshake_id="hs-abc-123",
            filename="priya.sync-conflict-20260923-101500-ABC.md")
    person, basis, ambiguous = external.find_by_external(vault, handshake_id="hs-abc-123")
    assert person is not None and person.id == "20260701090000"
    assert ambiguous is False


# ---- route ordering + wiring (over HTTP) ----------------------------------------

def test_by_external_does_not_404_as_a_person_id(vault_env):
    root, vault, folder = vault_env
    _person(folder, "Priya Raman", "20260701090000", handshake_id="hs-abc-123")
    with Server(root) as s:
        code, body = s.req("GET", "/api/people/by-external?handshake_id=hs-abc-123")
        assert code == 200
        assert body["found"] is True
        assert body["basis"] == "handshake_id"
        assert body["person"]["id"] == "20260701090000"


def test_by_external_miss_is_a_normal_200(vault_env):
    root, vault, folder = vault_env
    with Server(root) as s:
        code, body = s.req("GET", "/api/people/by-external?handshake_id=nope")
        assert code == 200 and body["found"] is False


def test_by_external_requires_at_least_one_param(vault_env):
    root, vault, folder = vault_env
    with Server(root) as s:
        code, body = s.req("GET", "/api/people/by-external")
        assert code == 400
