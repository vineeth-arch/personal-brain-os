"""Dex → vault sync (Pass 7). The network is an injected callable in every test
here: nothing in this file can reach getdex.com even if the key were set."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from pipeline import dex, notefm, people
from pipeline.config import Config
from pipeline.errors import StageError

NOW = datetime(2026, 8, 12, 9, 0, 0)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv(dex.ENV_KEY, "dex-test-key")
    v = tmp_path / "vault"
    (v / people.PEOPLE_FOLDER).mkdir(parents=True)
    subprocess.run(["git", "-C", str(v), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(v), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(v), "config", "user.name", "Test"], check=True)
    return v


def make_config(vault, **dex_settings):
    settings = {"base_url": "https://dex.test/api", "deeplink_base": "https://getdex.com/c/"}
    settings.update(dex_settings)
    return Config(vault_path=vault, inbox_path=vault, archive_path=vault, failed_path=vault,
                  raw={"dex": settings})


def fetcher(*pages):
    """An injected fetch that serves the given pages in order and records URLs."""
    calls: list[str] = []

    def _fetch(url, headers, timeout=0):
        assert headers[dex._AUTH_HEADER] == "dex-test-key"
        calls.append(url)
        index = min(len(calls) - 1, len(pages) - 1)
        return json.dumps(pages[index]).encode()

    _fetch.calls = calls
    return _fetch


def contact_row(dex_id, name, **extra):
    row = {"id": dex_id, "full_name": name}
    row.update(extra)
    return row


def git_log(vault) -> list[str]:
    out = subprocess.run(["git", "-C", str(vault), "log", "--format=%s"],
                         capture_output=True, text=True).stdout
    return out.strip().splitlines()


def read_person(vault, name_fragment):
    for path, fm, body in people.person_notes(vault):
        if name_fragment.lower() in path.stem.lower():
            return path, fm, body
    raise AssertionError(f"no person note matching {name_fragment!r}")


# ---- fetching ------------------------------------------------------------------

def test_pagination_follows_until_a_short_page(vault):
    full = {"contacts": [contact_row(f"d{i}", f"Person {i}") for i in range(dex.PAGE_LIMIT)]}
    tail = {"contacts": [contact_row("last", "Last One")]}
    fetch = fetcher(full, tail)
    contacts, skipped = dex.fetch_contacts(make_config(vault), fetch)
    assert len(contacts) == dex.PAGE_LIMIT + 1
    assert skipped == 0
    assert len(fetch.calls) == 2 and "offset=100" in fetch.calls[1]


def test_rows_without_an_id_or_name_are_skipped_not_guessed(vault):
    page = {"contacts": [contact_row("d1", "Ada Lovelace"), {"full_name": "No Id"},
                         {"id": "d3"}, "junk"]}
    contacts, skipped = dex.fetch_contacts(make_config(vault), fetcher(page))
    assert [c.name for c in contacts] == ["Ada Lovelace"]
    assert skipped == 3


def test_name_and_channels_are_assembled_from_the_shapes_dex_returns(vault):
    page = {"data": [{"id": "d1", "first_name": "Ada", "last_name": "Lovelace",
                      "emails": [{"email": "ada@example.com"}],
                      "phones": ["+15550001111"], "job_company_name": "Analytical Co",
                      "last_seen": "2026-08-01"}]}
    (contact,), _ = dex.fetch_contacts(make_config(vault), fetcher(page))
    assert contact.name == "Ada Lovelace"
    assert contact.emails == ["ada@example.com"] and contact.phones == ["+15550001111"]
    assert contact.company == "Analytical Co" and contact.last_seen == "2026-08-01"
    assert contact.deeplink("https://getdex.com/c/") == "https://getdex.com/c/d1"


def test_missing_key_is_a_three_part_error_and_never_calls_out(vault, monkeypatch):
    monkeypatch.delenv(dex.ENV_KEY, raising=False)

    def never(*a, **k):
        raise AssertionError("fetch must not run without a key")

    with pytest.raises(StageError) as exc:
        dex.fetch_contacts(make_config(vault), never)
    assert dex.ENV_KEY in exc.value.cause and exc.value.todo
    assert exc.value.transient is False


@pytest.mark.parametrize("code,transient", [(500, True), (429, True), (401, False), (404, False)])
def test_http_failures_are_classified(vault, code, transient):
    import io
    import urllib.error

    def boom(url, headers, timeout=0):
        raise urllib.error.HTTPError(url, code, "boom", {}, io.BytesIO(b""))

    with pytest.raises(StageError) as exc:
        dex.fetch_contacts(make_config(vault), boom)
    assert exc.value.transient is transient


# ---- syncing -------------------------------------------------------------------

def test_creates_notes_with_ai_provenance_and_commits_before_writing(vault):
    page = {"contacts": [contact_row("d1", "Ada Lovelace", last_seen="2026-08-01"),
                         contact_row("d2", "Grace Hopper")]}
    result = dex.sync(make_config(vault), fetch=fetcher(page), now=NOW)
    assert result.as_dict() == {"created": 2, "updated": 0, "unchanged": 0, "skipped": 0}

    _, fm, body = read_person(vault, "ada-lovelace")
    assert fm["origin"] == "ai" and fm["source"] == "dex"      # provenance firewall
    assert fm["dex_id"] == "d1" and fm["dex_deeplink"] == "https://getdex.com/c/d1"
    assert fm["last_contact"] == "2026-08-01"
    assert fm["id"] == "20260812090000"
    for heading in people.BODY_SECTIONS:
        assert heading in body

    # ids are unique even though both notes were written in the same second
    ids = {fm["id"] for _, fm, _ in people.person_notes(vault)}
    assert len(ids) == 2

    log = git_log(vault)
    assert log[0].startswith("dex: sync +2 new")
    assert log[1] == "pre-dex-sync"      # the batch is one revert away


def test_second_sync_of_the_same_data_changes_nothing(vault):
    page = {"contacts": [contact_row("d1", "Ada Lovelace", last_seen="2026-08-01")]}
    dex.sync(make_config(vault), fetch=fetcher(page), now=NOW)
    path, _, _ = read_person(vault, "ada")
    before, commits = path.read_text(), len(git_log(vault))

    result = dex.sync(make_config(vault), fetch=fetcher(page), now=NOW)
    assert result.as_dict() == {"created": 0, "updated": 0, "unchanged": 1, "skipped": 0}
    assert path.read_text() == before          # no churn
    assert len(git_log(vault)) == commits + 1  # only the pre-write commit


def test_existing_human_note_keeps_its_prose_and_gains_only_dex_fields(vault):
    path = vault / people.PEOPLE_FOLDER / "2026-01-05-ada-lovelace.md"
    path.write_text(
        "---\nid: 20260105120000\ntype: person\nname: Ada Lovelace\norigin: human\n"
        "status: active\ndex_id: d1\ncadence_days: 14\nlast_contact: 2026-07-01\n"
        "relationship: collaborator\n---\n\n"
        "## Context\nWe met at the Analytical Engine demo. She thinks in loops.\n\n"
        "## Needs\nAn intro to the numbers people.\n\n"
        "## Interaction log\n- 2026-07-01 — long call about looms\n\n"
        "## Next action\nSend the notation draft.\n")
    page = {"contacts": [contact_row("d1", "Ada Lovelace", last_seen="2026-08-05")]}

    result = dex.sync(make_config(vault), fetch=fetcher(page), now=NOW)
    assert result.updated == 1 and result.created == 0

    text = path.read_text()
    fm, body = notefm.parse_frontmatter(text)
    assert fm["origin"] == "human"                       # never flipped by an import
    assert fm["relationship"] == "collaborator"          # untouched field survives
    assert fm["last_contact"] == "2026-08-05"
    assert fm["dex_deeplink"] == "https://getdex.com/c/d1"
    assert fm["status"] == "active"                      # 7 days on a 14-day cadence
    assert "She thinks in loops." in body                # prose byte-identical
    assert "An intro to the numbers people." in body
    assert "- 2026-07-01 — long call about looms" in body    # history intact
    assert "- 2026-08-05 — last interaction, synced from Dex" in body


def test_sync_never_moves_last_contact_backwards(vault):
    path = vault / people.PEOPLE_FOLDER / "2026-01-05-ada.md"
    path.write_text("---\nid: 20260105120000\ntype: person\nname: Ada\norigin: human\n"
                    "dex_id: d1\nlast_contact: 2026-08-10\ncadence_days: 30\n---\n\n## Context\n")
    page = {"contacts": [contact_row("d1", "Ada", last_seen="2026-07-01")]}
    dex.sync(make_config(vault), fetch=fetcher(page), now=NOW)
    fm, _ = notefm.parse_frontmatter(path.read_text())
    assert fm["last_contact"] == "2026-08-10"   # a contact logged here outranks Dex history


def test_interaction_append_is_idempotent_across_runs(vault):
    page = {"contacts": [contact_row("d1", "Ada Lovelace", last_seen="2026-08-05")]}
    dex.sync(make_config(vault), fetch=fetcher(page), now=NOW)
    dex.sync(make_config(vault), fetch=fetcher(page), now=NOW)
    dex.sync(make_config(vault), fetch=fetcher(page), now=NOW)
    _, _, body = read_person(vault, "ada")
    assert body.count("synced from Dex") <= 1


def test_unreachable_dex_leaves_the_vault_completely_untouched(vault):
    import urllib.error

    def boom(url, headers, timeout=0):
        raise urllib.error.URLError("no route to host")

    before = git_log(vault)
    with pytest.raises(StageError) as exc:
        dex.sync(make_config(vault), fetch=boom, now=NOW)
    assert exc.value.transient is True
    assert list((vault / people.PEOPLE_FOLDER).iterdir()) == []
    assert git_log(vault) == before          # not even a pre-write commit


# ---- nightly tick ------------------------------------------------------------------

class FakeEvents:
    def __init__(self):
        self.reminders: set[str] = set()
        self.rows: list[tuple] = []

    def reminder_fired(self, key):
        return key in self.reminders

    def mark_reminder(self, key):
        self.reminders.add(key)

    def log(self, *a, **k):
        self.rows.append((a, k))


def test_tick_syncs_once_a_day_after_the_sync_hour(vault):
    config = make_config(vault, sync_daily=True)
    events = FakeEvents()
    page = {"contacts": [contact_row("d1", "Ada Lovelace")]}

    dex.tick(config, events, now=datetime(2026, 8, 12, 1, 0), fetch=fetcher(page))
    assert list((vault / people.PEOPLE_FOLDER).iterdir()) == []      # too early

    dex.tick(config, events, now=datetime(2026, 8, 12, 4, 0), fetch=fetcher(page))
    assert len(list((vault / people.PEOPLE_FOLDER).iterdir())) == 1

    dex.tick(config, events, now=datetime(2026, 8, 12, 9, 0), fetch=fetcher(page))
    assert len(list((vault / people.PEOPLE_FOLDER).iterdir())) == 1  # once per day

    dex.tick(config, events, now=datetime(2026, 8, 13, 4, 0), fetch=fetcher(page))
    assert len(list((vault / people.PEOPLE_FOLDER).iterdir())) == 1  # same contact, no new note


def test_tick_is_off_by_default_and_never_raises(vault):
    events = FakeEvents()
    dex.tick(make_config(vault), events, now=NOW, fetch=fetcher({"contacts": []}))
    assert events.reminders == set()

    def boom(url, headers, timeout=0):
        raise RuntimeError("dex exploded")

    dex.tick(make_config(vault, sync_daily=True), events, now=NOW, fetch=boom)   # must not raise
