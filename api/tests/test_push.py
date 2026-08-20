"""Pass D — pushing profile summaries to Dex and Google Contacts.

What these tests defend, beyond the happy path:

- text a human typed into Dex/Contacts survives a push byte for byte;
- the preview really is the dry run (nothing is written to see it);
- a Google link made before Pass D says "reconnect once" instead of failing;
- a person with no matching Google contact is a refusal, never a new contact;
- a sparse interaction log produces a summary prompt that forbids invention;
- the review queue only stages — nothing pushes itself.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from api import google, google_contacts, push
from api.tests.test_api import TOKEN, Server, env  # noqa: F401
from api.tests.test_people import _person
from pipeline import dex, relationships

TODAY = date(2026, 8, 20)


# ---- the marker merge: the append-only guarantee ---------------------------------

def test_merge_appends_when_the_field_has_no_block_yet():
    existing = "Met at Alserkal. Prefers WhatsApp.\nOwes me a deck."
    merged = dex.merge(existing, "Runs the artist programme.", TODAY)
    assert merged.startswith(existing), "everything the human wrote stays, first and intact"
    assert dex.MARKER_OPEN in merged and dex.MARKER_CLOSE in merged
    assert "Runs the artist programme." in merged
    assert "· via Brain OS 2026-08-20" in merged


def test_merge_replaces_only_the_block_and_preserves_both_sides():
    existing = (
        "MY OWN NOTE ABOVE — do not touch.\n\n"
        f"{dex.MARKER_OPEN}\nstale summary\n· via Brain OS 2026-01-01\n{dex.MARKER_CLOSE}\n\n"
        "MY OWN NOTE BELOW — also do not touch.")
    merged = dex.merge(existing, "fresh summary", TODAY)
    assert "MY OWN NOTE ABOVE — do not touch." in merged
    assert "MY OWN NOTE BELOW — also do not touch." in merged
    assert "stale summary" not in merged
    assert "fresh summary" in merged
    # the human's text is not merely present, it is unchanged around the block
    head, _, rest = merged.partition(dex.MARKER_OPEN)
    assert head == "MY OWN NOTE ABOVE — do not touch.\n\n"
    assert rest.split(dex.MARKER_CLOSE, 1)[1] == "\n\nMY OWN NOTE BELOW — also do not touch."


def test_merge_is_idempotent_across_repeated_pushes():
    field = ""
    for _ in range(3):
        field = dex.merge(field, "same summary", TODAY)
    assert field.count(dex.MARKER_OPEN) == 1, "a re-push must not stack blocks"
    assert field.count("same summary") == 1


def test_merge_on_an_empty_field_writes_only_our_block():
    merged = dex.merge("", "first summary", TODAY)
    assert merged.startswith(dex.MARKER_OPEN) and merged.endswith(dex.MARKER_CLOSE)


def test_merge_with_a_truncated_block_does_not_eat_the_text_above_it():
    existing = f"human note\n\n{dex.MARKER_OPEN}\nhalf-written summary"
    merged = dex.merge(existing, "repaired", TODAY)
    assert merged.startswith("human note\n\n")
    assert "half-written summary" not in merged and "repaired" in merged


def test_owned_section_reports_what_would_be_replaced():
    assert dex.owned_section("just a human note") == ""
    field = dex.merge("human note", "ours", TODAY)
    owned = dex.owned_section(field)
    assert owned.startswith(dex.MARKER_OPEN) and "ours" in owned
    assert "human note" not in owned


# ---- dex push: dry run, payload shape, key handling ------------------------------

class FakeDex:
    """Stands in for the network. Records every call so a 'dry run' that
    secretly wrote something would fail loudly."""

    def __init__(self, description=""):
        self.description = description
        self.calls = []

    def __call__(self, method, url, payload):
        self.calls.append((method, url, payload))
        if method == "GET":
            return {"contact": {"id": "dex-1", "description": self.description}}
        self.description = payload["contact"]["description"]
        return {}

    @property
    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


def _dex_person(tmp_path: Path, dex_id="dex-1"):
    folder = tmp_path / "07-People"
    folder.mkdir(parents=True, exist_ok=True)
    path = _person(folder, "Priya Raman", "20260701090000")
    text = path.read_text().replace("warmth_stage:", f"dex_id: {dex_id}\nwarmth_stage:")
    path.write_text(text)
    return relationships.find_person(tmp_path, "20260701090000")


def test_dry_run_returns_the_payload_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("DEX_API_KEY", "k")
    person = _dex_person(tmp_path)
    fake = FakeDex("human note")
    result = dex.push_description(person, "the summary", today=TODAY,
                                  dry_run=True, fetch=fake)
    assert result["dry_run"] is True and "pushed" not in result
    assert fake.writes == [], "a preview must not write to Dex"
    body = result["payload"]["contact"]["description"]
    assert body.startswith("human note") and "the summary" in body


def test_a_real_push_writes_the_merged_description(tmp_path, monkeypatch):
    monkeypatch.setenv("DEX_API_KEY", "k")
    person = _dex_person(tmp_path)
    fake = FakeDex("human note")
    result = dex.push_description(person, "the summary", today=TODAY, fetch=fake)
    assert result["pushed"] is True
    method, url, payload = fake.writes[0]
    assert method == "PUT" and url.endswith("/contacts/dex-1")
    assert payload["contact"]["description"].startswith("human note")
    # and a second push replaces in place rather than stacking
    dex.push_description(person, "newer summary", today=TODAY, fetch=fake)
    assert fake.description.count(dex.MARKER_OPEN) == 1
    assert "newer summary" in fake.description and "human note" in fake.description


def test_no_key_is_an_honest_not_configured_state(tmp_path, monkeypatch):
    monkeypatch.delenv("DEX_API_KEY", raising=False)
    person = _dex_person(tmp_path)
    assert dex.configured() is False
    with pytest.raises(dex.DexError) as caught:
        dex.push_description(person, "s", today=TODAY, fetch=FakeDex())
    assert caught.value.status == 503
    assert set(caught.value.envelope) == {"what", "cause", "todo"}
    assert "DEX_API_KEY" in caught.value.envelope["cause"]


def test_a_person_without_a_dex_id_is_refused_not_guessed(tmp_path, monkeypatch):
    monkeypatch.setenv("DEX_API_KEY", "k")
    person = _dex_person(tmp_path, dex_id="")
    with pytest.raises(dex.DexError) as caught:
        dex.push_description(person, "s", today=TODAY, fetch=FakeDex())
    assert caught.value.status == 409 and "dex_id" in caught.value.envelope["cause"]


# ---- google contacts: scope, matching, update-only -------------------------------

class FakeConfig:
    def __init__(self, raw):
        self.raw = raw


def _config_with_scopes(scopes: str):
    return FakeConfig({"google": {"refresh_token": "r", "scopes": scopes}})


class FakePeopleApi:
    def __init__(self, contacts):
        self.contacts = contacts
        self.calls = []

    def __call__(self, method, url, payload):
        self.calls.append((method, url, payload))
        if "searchContacts" in url:
            if "query=&" in url or url.endswith("query="):
                return {"results": []}
            return {"results": [{"person": c} for c in self.contacts]}
        return {}

    @property
    def writes(self):
        return [c for c in self.calls if c[0] == "PATCH"]


CONTACT = {
    "resourceName": "people/c1",
    "etag": "etag-1",
    "names": [{"displayName": "Priya Raman"}],
    "emailAddresses": [{"value": "priya@example.com"}],
    "phoneNumbers": [{"value": "+971 50 000 0000"}],
    "biographies": [{"value": "my own note about Priya"}],
}


def test_a_link_made_before_pass_d_asks_for_one_reconsent(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    config = _config_with_scopes(
        "https://www.googleapis.com/auth/gmail.readonly")     # no contacts scope
    person = _dex_person(tmp_path)
    assert google.has_contacts_scope(config) is False
    assert google_contacts.configured(config) is False
    with pytest.raises(google.GoogleError) as caught:
        google_contacts.push_biography(config, {}, person, "s", today=TODAY,
                                       fetch=FakePeopleApi([CONTACT]))
    assert caught.value.status == 409
    assert "Disconnect" in caught.value.envelope["todo"]


def test_a_matched_contact_is_updated_never_created(tmp_path):
    config = _config_with_scopes(google.CONTACTS_SCOPE)
    person = _dex_person(tmp_path)
    fake = FakePeopleApi([CONTACT])
    result = google_contacts.push_biography(config, {}, person, "the summary",
                                            today=TODAY, fetch=fake)
    assert result["pushed"] is True and result["contact_name"] == "Priya Raman"
    method, url, payload = fake.writes[0]
    assert method == "PATCH" and "updatePersonFields=biographies" in url
    assert payload["etag"] == "etag-1", "the etag guards against clobbering a concurrent edit"
    body = payload["biographies"][0]["value"]
    assert body.startswith("my own note about Priya") and "the summary" in body
    assert not any("createContact" in c[1] for c in fake.calls)


def test_no_match_refuses_rather_than_adding_to_the_address_book(tmp_path):
    config = _config_with_scopes(google.CONTACTS_SCOPE)
    person = _dex_person(tmp_path)
    stranger = {**CONTACT, "emailAddresses": [{"value": "someone@else.com"}],
                "phoneNumbers": [{"value": "+1 555 000 1111"}]}
    fake = FakePeopleApi([stranger])
    with pytest.raises(google.GoogleError) as caught:
        google_contacts.push_biography(config, {}, person, "s", today=TODAY, fetch=fake)
    assert caught.value.status == 404
    assert "never creates contacts" in caught.value.envelope["cause"]
    assert fake.writes == []


def test_a_phone_saved_with_spaces_still_matches(tmp_path):
    config = _config_with_scopes(google.CONTACTS_SCOPE)
    person = _dex_person(tmp_path)
    by_phone = {**CONTACT, "emailAddresses": []}
    found = google_contacts.find_contact(config, {}, person, fetch=FakePeopleApi([by_phone]))
    assert found is not None, "'+971 50 000 0000' and '+971500000000' are one person"


def test_contacts_dry_run_writes_nothing(tmp_path):
    config = _config_with_scopes(google.CONTACTS_SCOPE)
    person = _dex_person(tmp_path)
    fake = FakePeopleApi([CONTACT])
    result = google_contacts.push_biography(config, {}, person, "s", today=TODAY,
                                            dry_run=True, fetch=fake)
    assert result["dry_run"] is True and fake.writes == []


# ---- the summary leash -----------------------------------------------------------

def test_a_sparse_log_is_told_to_say_less_not_to_invent(tmp_path):
    from api import people as people_mod

    folder = tmp_path / "07-People"
    folder.mkdir(parents=True)
    _person(folder, "Aisha Noor", "20260701091000", last_contact="", log="")
    person = relationships.find_person(tmp_path, "20260701091000")
    prompt = people_mod.build_summary_prompt(person)
    assert "nothing logged yet" in prompt
    assert "Do not invent" in prompt
    assert "leave that line out entirely" in prompt
    assert "two-line honest" in prompt


def test_the_summary_prompt_is_a_profile_not_a_message(tmp_path):
    from api import people as people_mod

    person = _dex_person(tmp_path)
    prompt = people_mod.build_summary_prompt(person)
    assert "third person" in prompt
    assert "contact manager" in prompt
    # a message draft asks for the owner's voice; a CRM summary must not
    assert "my-voice" not in prompt.lower()


# ---- the routes ------------------------------------------------------------------

@pytest.fixture
def vault_env(env):
    root, vault, inbox, failed = env
    folder = vault / "07-People"
    folder.mkdir()
    path = _person(folder, "Priya Raman", "20260701090000")
    path.write_text(path.read_text().replace(
        "warmth_stage:", "dex_id: dex-1\nwarmth_stage:"))
    return root, vault, folder


def test_preview_reports_not_configured_rather_than_failing(vault_env, monkeypatch):
    root, _, _ = vault_env
    monkeypatch.delenv("DEX_API_KEY", raising=False)
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/push/preview",
                           {"target": "dex"})
        assert code == 503
        assert set(body["error"]) == {"what", "cause", "todo"}


def test_an_unknown_push_target_is_refused(vault_env):
    root, _, _ = vault_env
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/push/preview",
                           {"target": "linkedin"})
        assert code == 400 and "dex, contacts" in body["error"]["cause"]


def test_config_exposes_push_availability_as_booleans_only(vault_env):
    root, _, _ = vault_env
    with Server(root) as s:
        code, body = s.req("GET", "/api/config")
        assert code == 200
        assert set(body["push"]) == {"dex", "contacts_scope"}
        assert all(isinstance(v, bool) for v in body["push"].values())


def test_the_queue_is_empty_when_nothing_is_configured(vault_env, monkeypatch):
    root, _, _ = vault_env
    monkeypatch.delenv("DEX_API_KEY", raising=False)
    with Server(root) as s:
        code, body = s.req("GET", "/api/push/queue")
        assert code == 200 and body["items"] == []
        assert body["available"] == {"dex": False, "contacts_scope": False}


def test_the_queue_stages_a_person_who_has_never_been_pushed(vault_env, monkeypatch):
    root, vault, _ = vault_env
    monkeypatch.setenv("DEX_API_KEY", "k")
    with Server(root) as s:
        code, body = s.req("GET", "/api/push/queue")
        assert code == 200
        assert [i["name"] for i in body["items"]] == ["Priya Raman"]
        assert body["items"][0]["targets"] == ["dex"]
        assert body["items"][0]["last_pushed"] is None


def test_a_pushed_person_leaves_the_queue_until_they_move_on(vault_env, monkeypatch):
    root, vault, folder = vault_env
    monkeypatch.setenv("DEX_API_KEY", "k")
    db = root / "events.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "timestamp TEXT NOT NULL, file TEXT NOT NULL, stage TEXT NOT NULL,"
        "status TEXT NOT NULL, duration_ms INTEGER, message TEXT,"
        "plain_english_error TEXT);")
    conn.execute("INSERT INTO events (timestamp, file, stage, status, message) "
                 "VALUES (?,?,?,?,?)",
                 ("2026-08-19T09:00:00", "p.md", "push", "ok",
                  "target=dex person=20260701090000"))
    conn.commit()
    conn.close()

    with Server(root) as s:
        assert s.req("GET", "/api/push/queue")[1]["items"] == [], \
            "nothing new has happened since the push"

    # now log a contact — there IS something new to say about them
    path = next(folder.glob("*.md"))
    path.write_text(path.read_text().replace(
        "last_contact: 2026-06-01", f"last_contact: {date.today().isoformat()}"))
    with Server(root) as s:
        assert [i["name"] for i in s.req("GET", "/api/push/queue")[1]["items"]] \
            == ["Priya Raman"]


def test_the_morning_digest_only_counts_never_pushes(vault_env, monkeypatch):
    """CLAUDE.md §3: the batch half stages, the human confirms each one."""
    from pipeline import config as config_mod, morning

    root, vault, _ = vault_env
    monkeypatch.setenv("DEX_API_KEY", "k")
    config = config_mod.load(root / "config.json")
    lines = morning.push_section(config, root / "events.db")
    assert lines == ["1 profile ready to push — review in the cockpit"]

    monkeypatch.delenv("DEX_API_KEY", raising=False)
    assert morning.push_section(config, root / "events.db") == [], \
        "no push target configured means no nagging"


def test_the_digest_scope_check_still_matches_the_real_scope_constant():
    """morning.py checks a substring so it needn't import the api layer; if the
    real scope URL ever changes, this is what notices."""
    from pipeline import morning

    config = FakeConfig({"google": {"scopes": google.CONTACTS_SCOPE}})
    assert "auth/contacts" in google.CONTACTS_SCOPE
    assert morning.push_enabled(config) is True
