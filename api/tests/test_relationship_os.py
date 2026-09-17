"""Task 8 — the Relationship OS read endpoints: GET /api/people/today, GET
/api/people/greene, POST /api/people/lint, and the v2 fields added to
people.summary()/detail(). These are read-only surfaces over the pure-logic
pipeline modules (relationships, touchlog, ledger, queue, greene, draftlint)
built in Tasks 2-7 — this task only wires them into the API."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# the tmp vault/config/server harness is shared with the main API suite
from api.tests.test_api import TOKEN, Server, env  # noqa: F401


def _person_v2(folder: Path, name: str, person_id: str, **fields) -> Path:
    """A full-shape v2.2 person note. `fields` overrides any frontmatter key
    below; body sections default to blank."""
    defaults = dict(
        created="2026-01-01", relationship="client", company="Alserkal",
        channels="{whatsapp: +971500000000}", tier="core", cadence_days="",
        last_contact="2026-08-01", last_give="", last_ask="", quiet_until="",
        energy="", known_for="", recall_trigger="", dates="",
        referred_by="", list_of_20="false", conversation_stage="", buyer_role="",
        fit="", status="active", interaction_log="", next_action="",
        current_state="", future_state="", can_help="", how_they_communicate="",
        facts="", interpretations="", context="", needs="", updates="",
    )
    defaults.update(fields)
    f = defaults
    path = folder / f"2026-07-01-{name.lower().replace(' ', '-')}.md"
    path.write_text(
        "---\n"
        f"id: {person_id}\n"
        "type: person\n"
        f"created: {f['created']}\n"
        "source: manual\n"
        "origin: human\n"
        f"relationship: {f['relationship']}\n"
        f"company: {f['company']}\n"
        f"channels: {f['channels']}\n"
        f"tier: {f['tier']}\n"
        f"cadence_days: {f['cadence_days']}\n"
        f"last_contact: {f['last_contact']}\n"
        f"last_give: {f['last_give']}\n"
        f"last_ask: {f['last_ask']}\n"
        f"quiet_until: {f['quiet_until']}\n"
        f"energy: {f['energy']}\n"
        f"known_for: {f['known_for']}\n"
        f"recall_trigger: {f['recall_trigger']}\n"
        f"dates: {f['dates']}\n"
        f"referred_by: {f['referred_by']}\n"
        f"list_of_20: {f['list_of_20']}\n"
        f"conversation_stage: {f['conversation_stage']}\n"
        f"buyer_role: {f['buyer_role']}\n"
        f"fit: {f['fit']}\n"
        f"status: {f['status']}\n"
        "---\n\n"
        f"# {name}\n\n"
        f"## Context\n\n{f['context']}\n\n"
        f"## Current state\n\n{f['current_state']}\n\n"
        f"## Future state\n\n{f['future_state']}\n\n"
        f"## Needs\n\n{f['needs']}\n\n"
        f"## Can help with\n\n{f['can_help']}\n\n"
        f"## How they communicate\n\n{f['how_they_communicate']}\n\n"
        f"## Facts\n\n{f['facts']}\n\n"
        f"## Interpretations\n\n{f['interpretations']}\n\n"
        f"## Interaction log\n\n{f['interaction_log']}\n\n"
        f"## Next action\n\n{f['next_action']}\n\n"
        f"## Updates\n\n{f['updates']}\n", encoding="utf-8")
    return path


@pytest.fixture
def vault_env(env):
    root, vault, inbox, failed = env
    folder = vault / "07-People"
    folder.mkdir(exist_ok=True)
    return root, vault, folder


# ---- route order (P7) ------------------------------------------------------

def test_static_people_routes_not_captured_by_id(vault_env):
    root, _, _ = vault_env
    with Server(root) as s:
        code, body = s.req("GET", "/api/people/today")
        assert code == 200 and "strip" in body and "queues" in body

        code, body = s.req("GET", "/api/people/greene")
        assert code == 200 and "situations" in body

        code, body = s.req("POST", "/api/people/lint", {"text": "hello there"})
        assert code == 200 and "lints" in body


# ---- GET /api/people/today --------------------------------------------------

def test_today_strip_capped_and_labelled(vault_env):
    root, vault, folder = vault_env
    for i in range(7):
        _person_v2(folder, f"Person {i}", f"2026070109000{i}", tier="core",
                  last_contact="2025-01-01", cadence_days="7",
                  next_action="- open · I promised: send the deck")
    with Server(root) as s:
        code, body = s.req("GET", "/api/people/today")
        assert code == 200
        assert len(body["strip"]) <= 5
        assert body["overflow"] >= 1
        assert body["labels"]["owe_reply"] == "I owe a reply"
        assert set(body["queues"]) == {
            "owe_reply", "promises", "ask_about", "celebrate",
            "follow_up", "waiting_on_them", "reconnect"}
        assert body["tiers"]["core"]["count"] == 7
        assert body["tiers"]["core"]["cap"] == 35
        assert body["untiered"] == 0


# ---- GET /api/people/{id} v2 fields -----------------------------------------

def test_detail_has_v2_blocks(vault_env):
    root, _, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000", tier="core",
              relationship="client",
              interaction_log=(
                  "- 2026-06-01 · out · whatsapp · give_know · shared an article\n"
                  "- 2026-06-05 · in · whatsapp · reply · thanks!\n"),
              next_action="- open · I promised: send the deck")
    with Server(root) as s:
        code, body = s.req("GET", "/api/people/20260701090000")
        assert code == 200
        assert body["ledger"]["gives180"] == 1
        assert body["reliability"] == {"kept": 0, "late": 0, "dropped": 0}
        assert body["reliability_line"] == ""
        assert body["flags"] == []
        assert body["inside_floor"] is False
        assert body["quiet"] is None
        assert body["working_together"] == {
            "conversation_stage": "", "buyer_role": "", "fit": "",
            "no_economic_buyer": False}
        assert len(body["next_actions"]) == 1
        row = body["next_actions"][0]
        assert row["text"] == "I promised: send the deck"
        assert row["closed"] is False
        assert row["view"] == "promises"
        assert len(body["touches"]) == 2
        assert body["touches"][0]["day"] == "2026-06-05"  # newest first
        assert body["reads"] == {"pride": "", "record": ""}
        assert body["presets"] == []
        assert body["known_for"] == "" and body["language"] == ""
        assert "energy" not in body


def test_detail_hides_energy_without_desk(vault_env):
    root, _, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000", energy="draining")
    with Server(root) as s:
        code, body = s.req("GET", "/api/people/20260701090000")
        assert code == 200 and "energy" not in body

        code, body = s.req("GET", "/api/people/20260701090000?desk=1")
        assert code == 200 and body["energy"] == "draining"


def test_family_detail_has_no_working_together(vault_env):
    root, _, folder = vault_env
    _person_v2(folder, "Mom", "20260701090001", relationship="family",
              buyer_role="economic")
    with Server(root) as s:
        code, body = s.req("GET", "/api/people/20260701090001")
        assert code == 200
        assert body["commercial"] is False
        assert body["working_together"] is None


# ---- POST /api/people/lint ---------------------------------------------------

def test_lint_uses_person_tier_for_complaint(vault_env):
    root, _, folder = vault_env
    _person_v2(folder, "Priya Raman", "20260701090000", tier="inner")
    text = "I'm exhausted, work has been a nightmare this week."
    with Server(root) as s:
        # inner-tier person: the complaint lint is explicitly exempt
        code, body = s.req("POST", "/api/people/lint",
                           {"text": text, "person_id": "20260701090000"})
        assert code == 200
        assert "complaint" not in {l["code"] for l in body["lints"]}

        # no person_id resolves: tier defaults blank, complaint lint fires
        code, body = s.req("POST", "/api/people/lint", {"text": text})
        assert code == 200
        assert "complaint" in {l["code"] for l in body["lints"]}


# ---- GET /api/people/greene --------------------------------------------------

def test_greene_seeds_vault_once_and_commits(vault_env):
    root, vault, _ = vault_env
    with Server(root) as s:
        code, body = s.req("GET", "/api/people/greene")
        assert code == 200
        assert len(body["situations"]) == 18
        assert (vault / "_System" / "greene-helper.md").is_file()

    log1 = subprocess.run(["git", "-C", str(vault), "log", "--oneline"],
                          capture_output=True, text=True, check=True).stdout
    assert "api: seeded greene-helper.md" in log1
    commits_after_first = len(log1.splitlines())

    with Server(root) as s:
        code, body = s.req("GET", "/api/people/greene")
        assert code == 200 and len(body["situations"]) == 18

    log2 = subprocess.run(["git", "-C", str(vault), "log", "--oneline"],
                          capture_output=True, text=True, check=True).stdout
    assert len(log2.splitlines()) == commits_after_first  # no second seed commit


# ---- read-only ---------------------------------------------------------------

def test_detail_is_read_only(vault_env):
    root, _, folder = vault_env
    path = _person_v2(folder, "Priya Raman", "20260701090000")
    before = path.read_bytes()
    with Server(root) as s:
        code, _ = s.req("GET", "/api/people/20260701090000?desk=1")
        assert code == 200
    assert path.read_bytes() == before
