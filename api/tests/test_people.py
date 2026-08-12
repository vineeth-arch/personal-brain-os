"""People endpoints (Pass 7): the list groups by computed lifecycle, log-contact
and PATCH edit named fields and commit, and a person note written before these
fields existed still renders."""
from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta

from pipeline import people

from .test_api import TOKEN, Server, env  # noqa: F401  (env is a fixture)

TODAY = date.today()


def _write_person(vault, note_id, name, *, last_contact=None, cadence=None,
                  warmth="identified", extra="", body=None):
    folder = vault / people.PEOPLE_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        "---", f"id: {note_id}", "type: person", f"name: {name}",
        "created: 2026-01-01", "source: manual", "origin: human", "status: active",
        "relationship: friend", "company: Example Co",
        "channels: {whatsapp: , email: a@example.com, linkedin: }",
        f"warmth_stage: {warmth}",
    ]
    if cadence is not None:
        lines.append(f"cadence_days: {cadence}")
    if last_contact is not None:
        lines.append(f"last_contact: {last_contact}")
    if extra:
        lines.append(extra)
    lines.append("---")
    body = body or ("## Context\nMet at a workshop.\n\n## Needs\nIntros.\n\n"
                    "## Interaction log\n- 2026-01-02 — first call\n\n## Next action\nFollow up.")
    (folder / f"2026-01-01-{name.lower().replace(' ', '-')}.md").write_text(
        "\n".join(lines) + "\n\n" + body + "\n")


def _days_ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


def _git_log(vault):
    out = subprocess.run(["git", "-C", str(vault), "log", "--format=%s"],
                         capture_output=True, text=True).stdout
    return out.strip().splitlines()


def test_missing_folder_is_an_empty_list_not_an_error(env):
    root, _, _, _ = env
    with Server(root) as s:
        code, body = s.req("GET", "/api/people")
        assert code == 200 and body == {"items": []}


def test_people_grouped_by_computed_lifecycle(env):
    root, vault, _, _ = env
    _write_person(vault, "20260101090001", "Ada Active", last_contact=_days_ago(2), cadence=7)
    _write_person(vault, "20260101090002", "Cass Cold", last_contact=_days_ago(12), cadence=7)
    _write_person(vault, "20260101090003", "Dara Dormant", last_contact=_days_ago(40), cadence=7)
    _write_person(vault, "20260101090004", "Uma Unset")
    with Server(root) as s:
        _, body = s.req("GET", "/api/people")
        by_name = {i["name"]: i for i in body["items"]}
        assert by_name["Ada Active"]["status"] == "active"
        assert by_name["Cass Cold"]["status"] == "cold"
        assert by_name["Dara Dormant"]["status"] == "dormant"
        assert by_name["Uma Unset"]["status"] == "unset"
        assert by_name["Uma Unset"]["unset"] is True
        assert by_name["Cass Cold"]["days_since_contact"] == 12
        assert by_name["Cass Cold"]["days_overdue"] == 5
        assert by_name["Uma Unset"]["days_since_contact"] is None

        # most overdue first; the unset note sorts last (a chore, not a nudge)
        names = [i["name"] for i in body["items"]]
        assert names[0] == "Dara Dormant" and names[-1] == "Uma Unset"

        for f, expected in (("cold", ["Cass Cold"]), ("active", ["Ada Active"]),
                            ("dormant", ["Dara Dormant"]), ("unset", ["Uma Unset"])):
            _, filtered = s.req("GET", f"/api/people?filter={f}")
            assert [i["name"] for i in filtered["items"]] == expected

        code, err = s.req("GET", "/api/people?filter=warm")
        assert code == 400 and set(err["error"]) == {"what", "cause", "todo"}


def test_log_contact_moves_the_person_and_commits(env):
    root, vault, _, _ = env
    _write_person(vault, "20260101090002", "Cass Cold", last_contact=_days_ago(12), cadence=7)
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/20260101090002/log-contact")
        assert code == 200
        assert body["status"] == "active" and body["days_since_contact"] == 0

        _, listed = s.req("GET", "/api/people?filter=cold")
        assert listed["items"] == []      # it left the going-cold group

        _, detail = s.req("GET", "/api/people/20260101090002")
        assert f"{TODAY.isoformat()} — contact logged" in detail["interactions"]
        assert "2026-01-02 — first call" in detail["interactions"]   # history intact
        assert detail["sections"]["Context"] == "Met at a workshop."

    assert _git_log(vault)[0] == "api: logged contact 20260101090002"


def test_log_contact_on_an_unknown_id_is_a_404_envelope(env):
    root, vault, _, _ = env
    _write_person(vault, "20260101090001", "Ada", last_contact=_days_ago(1), cadence=7)
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/99999999999999/log-contact")
        assert code == 404 and set(body["error"]) == {"what", "cause", "todo"}


def test_patch_sets_cadence_warmth_and_status(env):
    root, vault, _, _ = env
    _write_person(vault, "20260101090001", "Ada", last_contact=_days_ago(10), cadence=30)
    with Server(root) as s:
        code, body = s.req("PATCH", "/api/people/20260101090001",
                           {"cadence_days": 7, "warmth_stage": "conversing"})
        assert code == 200
        assert body["cadence_days"] == 7 and body["warmth_stage"] == "conversing"
        # the tighter cadence re-reads as cold, and the file agrees with the screen
        assert body["status"] == "cold"

        _, detail = s.req("GET", "/api/people/20260101090001")
        assert detail["status"] == "cold"

    text = next((vault / people.PEOPLE_FOLDER).glob("*.md")).read_text()
    assert "cadence_days: 7" in text and "warmth_stage: conversing" in text
    assert "Met at a workshop." in text            # prose untouched by a field edit
    assert _git_log(vault)[0] == "api: person 20260101090001 updated"


def test_patch_rejects_values_outside_the_schema(env):
    root, vault, _, _ = env
    _write_person(vault, "20260101090001", "Ada", last_contact=_days_ago(1), cadence=7)
    with Server(root) as s:
        for change in ({"warmth_stage": "toasty"}, {"cadence_days": 0}, {"cadence_days": -3}):
            code, body = s.req("PATCH", "/api/people/20260101090001", change)
            assert code == 400, change
            assert set(body["error"]) == {"what", "cause", "todo"}
        code, body = s.req("PATCH", "/api/people/20260101090001", {})
        assert code == 400 and "nothing to change" in body["error"]["what"].lower()


def test_old_person_notes_without_the_new_fields_still_work(env):
    """Backward compatibility: a note from before Pass 7 renders as unset, and
    setting a cadence is enough to bring it into the lifecycle."""
    root, vault, _, _ = env
    folder = vault / people.PEOPLE_FOLDER
    folder.mkdir(parents=True)
    (folder / "2025-06-01-old-friend.md").write_text(
        "---\nid: 20250601090000\ntype: person\ncreated: 2025-06-01\norigin: human\n"
        "status: active\n---\n\nJust some notes about an old friend.\n")
    with Server(root) as s:
        _, body = s.req("GET", "/api/people")
        (item,) = body["items"]
        assert item["unset"] is True and item["status"] == "unset"
        assert item["name"] == "old-friend"      # falls back to the filename
        assert item["cadence_days"] is None

        _, detail = s.req("GET", "/api/people/20250601090000")
        assert detail["interactions"] == []      # no log section yet, no crash

        code, body = s.req("PATCH", "/api/people/20250601090000", {"cadence_days": 30})
        assert code == 200 and body["cadence_days"] == 30
        # last_contact is still unknown, so it stays unset until contact is logged
        assert body["status"] == "unset" and body["unset"] is True

        code, body = s.req("POST", "/api/people/20250601090000/log-contact")
        assert code == 200 and body["status"] == "active"


def test_sync_without_a_key_returns_a_three_part_envelope(env, monkeypatch):
    root, _, _, _ = env
    with Server(root) as s:
        code, body = s.req("POST", "/api/people/sync")
        assert code == 400
        assert set(body["error"]) == {"what", "cause", "todo"}
        assert "DEX_API_KEY" in body["error"]["cause"]
