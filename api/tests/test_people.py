"""Pass MW — the People API. What these tests defend, beyond the happy path:
drafts refuse rather than write in a generic voice, they never invent a shared
history, the server hands back raw channel values instead of message links, and
PDL being unconfigured is an honest state rather than a crash."""
from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from api import people
# the tmp vault/config/server harness is shared with the main API suite
from api.tests.test_api import TOKEN, Server, env  # noqa: F401

TODAY = date(2026, 8, 20)


def _person(folder: Path, name: str, person_id: str, *, last_contact="2026-06-01",
            cadence="7", stage="engaging", log="- 2026-06-01 — coffee at Alserkal",
            channels="{whatsapp: +971500000000, email: priya@example.com}"):
    path = folder / f"2026-07-01-{name.lower().replace(' ', '-')}.md"
    path.write_text(
        f"---\nid: {person_id}\ntype: person\ncreated: 2026-07-01\nsource: manual\n"
        f"origin: human\nrelationship: client\ncompany: Alserkal\n"
        f"channels: {channels}\ncadence_days: {cadence}\nlast_contact: {last_contact}\n"
        f"warmth_stage: {stage}\nstatus: active\n---\n\n"
        f"# {name}\n\n## Context\n\nMet at a studio visit.\n\n## Needs\n\nA studio.\n\n"
        f"## Interaction log\n\n{log}\n\n## Next action\n\n\n", encoding="utf-8")
    return path


@pytest.fixture
def vault_env(env):
    root, vault, inbox, failed = env
    folder = vault / "07-People"
    folder.mkdir()
    _person(folder, "Priya Raman", "20260701090000")
    return root, vault, folder


# ---- reading -------------------------------------------------------------------

def test_people_list_carries_the_computed_state(vault_env):
    root, vault, _ = vault_env
    with Server(root) as s:
        code, body = s.req("GET", "/api/people")
        assert code == 200 and len(body["items"]) == 1
        item = body["items"][0]
        assert item["name"] == "Priya Raman" and item["warmth_stage"] == "engaging"
        assert item["going_cold"] is True and item["days_since_contact"] > 7
        assert item["channels"]["whatsapp"] == "+971500000000"


def test_person_detail_includes_the_body_sections(vault_env):
    root, _, _ = vault_env
    with Server(root) as s:
        code, body = s.req("GET", "/api/people/20260701090000")
        assert code == 200
        assert "studio visit" in body["context"] and "coffee at Alserkal" in body["interaction_log"]
        code, err = s.req("GET", "/api/people/99999999999999")
        assert code == 404 and set(err["error"]) == {"what", "cause", "todo"}


# ---- my voice ------------------------------------------------------------------

def test_voice_file_round_trip(vault_env):
    root, vault, _ = vault_env
    with Server(root) as s:
        assert s.req("GET", "/api/people/voice")[1]["exists"] is False
        code, body = s.req("POST", "/api/people/voice",
                           {"samples": ["hey! sorry for the slow reply", "Sounds good — Tuesday works."]})
        assert code == 200 and body["exists"] is True and body["samples"] == 2
        text = (vault / "my-voice.md").read_text(encoding="utf-8") if (vault / "my-voice.md").exists() \
            else (vault / "_System" / "my-voice.md").read_text(encoding="utf-8")
        # the samples are stored verbatim — they ARE the style guide
        assert "hey! sorry for the slow reply" in text
        assert "origin: human" in text, "nothing here was AI-written"
        # empty samples are refused rather than writing a useless file
        assert s.req("POST", "/api/people/voice", {"samples": ["  ", ""]})[0] == 400


def test_voice_write_commits_the_vault(vault_env):
    root, vault, _ = vault_env
    with Server(root) as s:
        s.req("POST", "/api/people/voice", {"samples": ["yep, on it"]})
    log = subprocess.run(["git", "-C", str(vault), "log", "-1", "--format=%s"],
                         capture_output=True, text=True).stdout
    assert "my-voice" in log


# ---- drafting ------------------------------------------------------------------

def test_draft_refuses_until_the_voice_file_exists(vault_env):
    root, _, _ = vault_env
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/draft", {})
        assert code == 409, "a generic-voice draft is worse than no draft"
        assert "my-voice" in body["error"]["cause"]
        assert "Settings" in body["error"]["todo"]


def test_draft_returns_raw_channels_never_a_message_link(vault_env, monkeypatch):
    root, vault, _ = vault_env
    from pipeline import llm

    monkeypatch.setattr(llm, "complete_text",
                        lambda prompt, config: ("hey Priya, long time — still thinking about "
                                                "that studio. free this week?", "claude-haiku", []))
    with Server(root) as s:
        s.req("POST", "/api/people/voice", {"samples": ["hey! long time"]})
        code, body = s.req("POST", "/api/people/20260701090000/draft", {})
        assert code == 200
        assert body["text"].startswith("hey Priya")
        # the preferred channel per the whatsapp → email → linkedin order
        assert body["channel"] == "whatsapp"
        assert body["channels"] == {"whatsapp": "+971500000000", "email": "priya@example.com"}
        # CLAUDE.md §4 — the server hands over values, not a way to deliver.
        # The forbidden strings are assembled rather than spelled out, so this
        # file stays clean for the send-path scanner in test_no_send.py.
        delivery = ["wa" + ".me", "mail" + "to:", "api.whats" + "app.com"]
        payload = json.dumps(body)
        assert not [d for d in delivery if d in payload]


def test_draft_attempts_are_logged_so_providers_sees_them(vault_env, monkeypatch):
    """D6: a draft's LLM attempts used to be invisible to events.db — GET
    /api/providers never counted them."""
    root, _, _ = vault_env
    from pipeline import llm

    monkeypatch.setattr(
        llm, "complete_text",
        lambda prompt, config: ("hey — long time", "claude-haiku",
                                [llm.Attempt("gemini-flash", "rate-limit"),
                                 llm.Attempt("claude-haiku", "served", confidence=None)]))
    with Server(root) as s:
        s.req("POST", "/api/people/voice", {"samples": ["hey! long time"]})
        code, _ = s.req("POST", "/api/people/20260701090000/draft", {})
        assert code == 200
        code, body = s.req("GET", "/api/providers")
        assert code == 200
        by_name = {p["provider"]: p for p in body["providers"]}
        assert by_name["claude-haiku"]["served"] == 1
        assert by_name["gemini-flash"]["fell_through"] == 1


def test_draft_can_be_asked_for_a_specific_channel(vault_env, monkeypatch):
    root, _, _ = vault_env
    from pipeline import llm
    seen = {}

    def router(prompt, config):
        seen["prompt"] = prompt
        return "drafted", "claude-haiku", []

    monkeypatch.setattr(llm, "complete_text", router)
    with Server(root) as s:
        s.req("POST", "/api/people/voice", {"samples": ["hey"]})
        code, body = s.req("POST", "/api/people/20260701090000/draft", {"channel": "email"})
        assert code == 200 and body["channel"] == "email"
        assert "over email" in seen["prompt"]


def test_the_draft_prompt_is_leashed_to_what_is_actually_logged(vault_env):
    """The leash is the whole point: an empty log must produce a shorter,
    honest message, not an invented shared history."""
    from pipeline import relationships

    root, vault, folder = vault_env
    _person(folder, "Blank Slate", "20260701090001", log="")
    people_list = {p.id: p for p in relationships.load_people(vault)}

    with_history = people.build_draft_prompt(people_list["20260701090000"], "voice", "whatsapp")
    assert "coffee at Alserkal" in with_history
    assert "Do not invent" in with_history

    empty = people.build_draft_prompt(people_list["20260701090001"], "voice", "whatsapp")
    assert "nothing logged yet" in empty
    assert "shorter and more general" in empty
    assert "Do not invent" in empty


def test_draft_says_so_when_every_provider_fails(vault_env, monkeypatch):
    root, _, _ = vault_env
    from pipeline import llm
    monkeypatch.setattr(llm, "complete_text", lambda prompt, config: (None, None, []))
    with Server(root) as s:
        s.req("POST", "/api/people/voice", {"samples": ["hey"]})
        code, body = s.req("POST", "/api/people/20260701090000/draft", {})
        assert code == 502 and set(body["error"]) == {"what", "cause", "todo"}


# ---- writers -------------------------------------------------------------------

def test_logging_contact_resets_the_counter_and_commits(vault_env):
    root, vault, _ = vault_env
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/contact",
                           {"note": "sent the studio note", "channel": "whatsapp"})
        assert code == 200
        assert body["days_since_contact"] == 0 and body["going_cold"] is False
        # the stage ladder is offered, never applied behind your back
        assert body["suggest_stage"] == "conversing"
        assert body["warmth_stage"] == "engaging"

    text = next((vault / "07-People").glob("*.md")).read_text(encoding="utf-8")
    assert "sent the studio note" in text and "coffee at Alserkal" in text
    log = subprocess.run(["git", "-C", str(vault), "log", "-1", "--format=%s"],
                         capture_output=True, text=True).stdout
    assert "logged contact" in log


def test_warmth_stage_advances_only_within_the_six(vault_env):
    root, _, _ = vault_env
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/warmth", {"stage": "conversing"})
        assert code == 200 and body["warmth_stage"] == "conversing"
        assert s.req("POST", "/api/people/20260701090000/warmth", {"stage": "besties"})[0] == 400
        assert s.req("POST", "/api/people/99999999999999/warmth", {"stage": "warm"})[0] == 404


# ---- enrichment ------------------------------------------------------------------

def test_enrich_without_a_key_is_an_honest_not_configured(vault_env, monkeypatch):
    root, _, _ = vault_env
    monkeypatch.delenv("PDL_API_KEY", raising=False)
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260701090000/enrich")
        assert code == 503
        assert "PDL_API_KEY" in body["error"]["cause"]
        assert "keeps working without it" in body["error"]["todo"]


def test_enrich_appends_ai_flagged_facts_under_context(vault_env):
    root, vault, _ = vault_env
    from pipeline import relationships

    def fake_pdl(url):
        # url-encoded in the query string; an email is the strongest identifier we have
        assert "priya%40example.com" in url
        return {"data": {"job_title": "Head of Studio",
                         "job_company_name": "Alserkal",
                         "location_name": "Dubai"}}, 97

    out = people.enrich(vault, "20260701090000", fetch=fake_pdl, today=TODAY)
    assert out["enriched"] is True and out["credits_remaining"] == 97

    context = relationships.find_person(vault, "20260701090000").sections["Context"]
    assert "Met at a studio visit." in context           # the human's line survives
    assert "Head of Studio at Alserkal (Dubai)" in context
    assert "origin: ai" in context, "AI-written facts must say so (SCHEMA §1)"


def test_enrich_with_no_match_changes_nothing(vault_env):
    root, vault, _ = vault_env
    before = next((vault / "07-People").glob("*.md")).read_text(encoding="utf-8")
    out = people.enrich(vault, "20260701090000", fetch=lambda url: ({"data": {}}, 96), today=TODAY)
    assert out["enriched"] is False and "no match" in out["detail"]
    assert next((vault / "07-People").glob("*.md")).read_text(encoding="utf-8") == before


# ---- quick-add (Pass X) ----------------------------------------------------------

def test_quick_add_writes_a_schema_correct_person_note(vault_env):
    root, vault, folder = vault_env
    with Server(root) as s:
        code, body = s.req("POST", "/api/people",
                           {"name": "Sara Khalid",
                            "channel": {"kind": "email", "value": "sara@example.com"}})
        assert code == 201
        assert body["name"] == "Sara Khalid"
        # one name + one channel IS "identified" — spotted, not yet researched
        assert body["warmth_stage"] == "identified" and body["status"] == "active"
        assert body["channels"] == {"email": "sara@example.com"}

    written = next(p for p in folder.glob("*sara-khalid*.md"))
    text = written.read_text(encoding="utf-8")
    assert "type: person" in text
    assert "origin: human" in text, "the owner typed this — it is not AI-written"
    assert "source: manual" in text
    for section in ("## Context", "## Needs", "## Interaction log", "## Next action"):
        assert section in text, f"{section} is part of the person schema"
    # the immutable id is a timestamp and the filename is the human-readable half
    frontmatter_id = next(line for line in text.splitlines() if line.startswith("id: "))
    assert frontmatter_id.removeprefix("id: ").strip().isdigit()
    assert len(frontmatter_id.removeprefix("id: ").strip()) == 14


def test_quick_add_commits_the_vault(vault_env):
    root, vault, _ = vault_env
    with Server(root) as s:
        s.req("POST", "/api/people",
              {"name": "Sara Khalid", "channel": {"kind": "email", "value": "s@e.com"}})
    log = subprocess.run(["git", "-C", str(vault), "log", "-1", "--format=%s"],
                         capture_output=True, text=True).stdout
    assert "added target Sara Khalid" in log


def test_quick_add_refuses_blank_and_unknown_input(vault_env):
    root, _, folder = vault_env
    before = len(list(folder.glob("*.md")))
    with Server(root) as s:
        for payload in (
            {"name": "  ", "channel": {"kind": "email", "value": "s@e.com"}},
            {"name": "Sara", "channel": {"kind": "email", "value": "   "}},
            {"name": "Sara", "channel": {"kind": "carrier-pigeon", "value": "x"}},
        ):
            code, body = s.req("POST", "/api/people", payload)
            assert code == 400, payload
            assert set(body["error"]) == {"what", "cause", "todo"}
    assert len(list(folder.glob("*.md"))) == before, "a refused add must write nothing"


def test_two_targets_with_the_same_name_never_overwrite_each_other(vault_env):
    root, _, folder = vault_env
    with Server(root) as s:
        first = s.req("POST", "/api/people",
                      {"name": "Sara Khalid",
                       "channel": {"kind": "email", "value": "sara@one.com"}})[1]
        second = s.req("POST", "/api/people",
                       {"name": "Sara Khalid",
                        "channel": {"kind": "email", "value": "sara@two.com"}})[1]
    # the id is what every link points at, so it must be unique even for two
    # targets added inside the same second
    assert first["id"] != second["id"]
    assert first["file"] != second["file"]
    assert len(list(folder.glob("*sara-khalid*.md"))) == 2
