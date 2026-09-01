"""Pass I, task I2: Dex contacts pull. Matches Dex contacts to vault people by
normalized email or phone only — never by name (see pipeline/dex.py's pull-side
module docstring for why: a wrong match would silently redirect a real
person's Dex profile-push link to a stranger's contact).

Hermetic — `fetch` is injected as `(method, url, payload) -> dict`, the same
fake-fetch style as api/tests/test_push.py's FakeDex and this build's closest
structural twin, pipeline/tests/test_gmailpull.py's FakeGmail/gmail_tick
tests. Commit-count assertions follow that same file's `_commit_count`
convention. `monkeypatch.setenv`/`delenv("DEX_API_KEY", ...)` follows
api/tests/test_push.py's key-presence convention. No conftest.py."""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import dex, relationships
from pipeline.events import EventLog

PEOPLE_FOLDER = "07-People"


def _person(folder: Path, name: str, person_id: str, *, channels="", dex_id="") -> Path:
    """A real person note, schema-shaped per SCHEMA-REFERENCE.md §7 — mirrors
    api/tests/test_people.py's `_person` / pipeline/tests/test_relationships.py's
    `person_note` fixtures, parametrized by id so ambiguity (two people, same
    id-space) can be seeded."""
    path = folder / f"2026-07-01-{name.lower().replace(' ', '-')}.md"
    path.write_text(
        "---\n"
        f"id: {person_id}\n"
        "type: person\n"
        "created: 2026-07-01\n"
        "source: manual\n"
        "origin: human\n"
        "relationship: client\n"
        "company: Alserkal\n"
        f"channels: {channels}\n"
        "cadence_days:\n"
        "last_contact: 2026-06-01\n"
        "warmth_stage: engaging\n"
        f"dex_id: {dex_id}\n"
        "status: active\n"
        "---\n\n"
        f"# {name}\n\n## Context\n\n\n## Needs\n\n\n"
        "## Interaction log\n\n- 2026-06-01 — coffee at Alserkal\n\n## Next action\n\n\n",
        encoding="utf-8")
    return path


def make_config(vault: Path) -> SimpleNamespace:
    return SimpleNamespace(vault_path=vault)


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(vault), *args], check=True,
                          capture_output=True, text=True).stdout


def _init_git_vault(vault: Path) -> None:
    _git(vault, "init", "-q")
    _git(vault, "config", "user.email", "t@example.com")
    _git(vault, "config", "user.name", "Test")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "--allow-empty", "-m", "initial vault state")


def _commit_count(vault: Path) -> int:
    return len(_git(vault, "log", "--oneline").splitlines())


class FakeDex:
    """Stands in for the network. Records every (method, url, payload) call.
    One page of contacts, then an empty page — the natural pagination
    terminator `pull_contacts` relies on, mirroring FakeGmail's single-page
    shape in pipeline/tests/test_gmailpull.py."""

    def __init__(self, contacts: list[dict]):
        self.contacts = contacts
        self.calls: list[tuple[str, str, object]] = []

    def __call__(self, method: str, url: str, payload) -> dict:
        self.calls.append((method, url, payload))
        if "page=1" in url:
            return {"contacts": self.contacts}
        return {"contacts": []}


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    (v / PEOPLE_FOLDER).mkdir(parents=True)
    return v


@pytest.fixture
def events(tmp_path, vault):
    log = EventLog(tmp_path / "events.db", vault)
    yield log
    log.close()


# ---- 1. email match stamps once, exactly one commit --------------------------

def test_email_match_stamps_dex_id_and_commits_once(vault, events, monkeypatch):
    monkeypatch.setenv("DEX_API_KEY", "x")
    _init_git_vault(vault)
    before = _commit_count(vault)
    path = _person(vault / PEOPLE_FOLDER, "Priya Raman", "20260701090000",
                   channels="{email: a@b.com}")

    # deliberately different case than the seeded note, to prove normalization
    fake = FakeDex([{"id": "dex-1", "emails": ["A@B.com"]}])
    dex.pull_contacts(make_config(vault), events, fetch=fake)

    text = path.read_text(encoding="utf-8")
    assert "dex_id: dex-1" in text
    assert _commit_count(vault) == before + 1


# ---- 2. second run: the already-stamped guard, isolated from the throttle ----

def test_second_run_never_overwrites_an_existing_dex_id(vault, events, monkeypatch):
    """This is the load-bearing invariant of the whole task: a person who
    already has a dex_id is NEVER touched again. There are two independent
    reasons a second `pull_contacts` call could be a no-op — the once-a-day
    reminder throttle, and this "already linked" guard — and this test must
    prove the SECOND one, not accidentally just prove the throttle fired. So
    the reminder key is explicitly cleared between the two calls, and a
    second fake payload with a DIFFERENT contact id (still matching by email)
    is used, so fetch really runs again rather than the test only appearing
    to check something."""
    monkeypatch.setenv("DEX_API_KEY", "x")
    _init_git_vault(vault)
    path = _person(vault / PEOPLE_FOLDER, "Priya Raman", "20260701090000",
                   channels="{email: a@b.com}")

    fake1 = FakeDex([{"id": "dex-1", "emails": ["A@B.com"]}])
    dex.pull_contacts(make_config(vault), events, fetch=fake1)
    assert "dex_id: dex-1" in path.read_text(encoding="utf-8")
    after_first = _commit_count(vault)

    # Bypass the once-a-day reminder gate directly, so the second call below
    # genuinely re-runs the match/stamp logic instead of short-circuiting on
    # the throttle — proving THIS test exercises the "never overwrite" guard.
    today_key = f"dexpull-{date.today().isoformat()}"
    events.conn.execute("DELETE FROM reminders WHERE key = ?", (today_key,))
    events.conn.commit()
    assert events.reminder_fired(today_key) is False, \
        "the throttle must be cleared, or this test would just re-prove itself"

    fake2 = FakeDex([{"id": "dex-999", "emails": ["A@B.com"]}])
    dex.pull_contacts(make_config(vault), events, fetch=fake2)

    assert fake2.calls, "fetch must actually run on the second, un-throttled call"
    unchanged = path.read_text(encoding="utf-8")
    assert "dex_id: dex-1" in unchanged
    assert "dex-999" not in unchanged
    assert _commit_count(vault) == after_first, "nothing changed — no second commit"


# ---- 3. phone-only match (digit-only normalization) ---------------------------

def test_phone_only_match_stamps_correctly(vault, events, monkeypatch):
    monkeypatch.setenv("DEX_API_KEY", "x")
    path = _person(vault / PEOPLE_FOLDER, "Farah Khan", "20260701090001",
                   channels='{whatsapp: "+971 50 000 0001"}')

    # deliberately different formatting than the seeded note
    fake = FakeDex([{"id": "dex-2", "phones": ["971500000001"]}])
    dex.pull_contacts(make_config(vault), events, fetch=fake)

    assert "dex_id: dex-2" in path.read_text(encoding="utf-8")


# ---- 4. ambiguity: two vault people sharing one email — skipped, never guessed --

def test_ambiguous_contact_is_skipped_and_logged_never_guessed(vault, events, monkeypatch):
    monkeypatch.setenv("DEX_API_KEY", "x")
    _init_git_vault(vault)
    before = _commit_count(vault)
    folder = vault / PEOPLE_FOLDER
    p1 = _person(folder, "Priya Raman", "20260701090000", channels="{email: dup@b.com}")
    p2 = _person(folder, "Priyanka Rao", "20260701090001", channels="{email: dup@b.com}")

    fake = FakeDex([{"id": "dex-3", "emails": ["dup@b.com"]}])
    dex.pull_contacts(make_config(vault), events, fetch=fake)

    assert "dex-3" not in p1.read_text(encoding="utf-8")
    assert "dex-3" not in p2.read_text(encoding="utf-8")
    assert _commit_count(vault) == before

    rows = events.conn.execute(
        "SELECT message FROM events WHERE stage = 'dex_pull'").fetchall()
    ambiguous_rows = [r for r in rows if (r[0] or "").startswith("ambiguous dex_id=")]
    assert len(ambiguous_rows) == 1
    assert "matched 2 people" in ambiguous_rows[0][0]


# ---- 5. keyless: silent no-op, fetch never called -----------------------------

def test_keyless_is_a_silent_no_op(vault, events, monkeypatch):
    monkeypatch.delenv("DEX_API_KEY", raising=False)
    _person(vault / PEOPLE_FOLDER, "Priya Raman", "20260701090000",
            channels="{email: a@b.com}")

    def exploding_fetch(method, url, payload):
        raise AssertionError("fetch must never be called without DEX_API_KEY")

    dex.pull_contacts(make_config(vault), events, fetch=exploding_fetch)

    rows = events.conn.execute("SELECT 1 FROM events WHERE stage = 'dex_pull'").fetchall()
    assert rows == []


# ---- 6. name-similarity is structurally never consulted -----------------------

def test_matching_never_falls_back_to_a_contacts_name_field(vault, events, monkeypatch):
    """A fake contact whose emails/phones DON'T match any seeded person, but
    whose payload ALSO carries a tempting `"name"` field that closely
    resembles the real seeded person's name. If the matcher ever fell back to
    name-similarity, this contact would wrongly match — it must not.

    This also confirms, structurally, that the production code never reads a
    `name` key off a contact at all: pull_contacts, _emails_of, and
    _phones_of are the only functions that read a Dex contact dict, and none
    of them mention "name"."""
    import inspect

    for fn in (dex.pull_contacts, dex._emails_of, dex._phones_of, dex._build_indexes):
        assert '"name"' not in inspect.getsource(fn)
        assert "'name'" not in inspect.getsource(fn)

    monkeypatch.setenv("DEX_API_KEY", "x")
    path = _person(vault / PEOPLE_FOLDER, "Priya Raman", "20260701090000",
                   channels="{email: real@b.com}")

    fake = FakeDex([{"id": "dex-4", "name": "Priya Raman",
                     "emails": ["someone-else@b.com"], "phones": ["1234567890"]}])
    dex.pull_contacts(make_config(vault), events, fetch=fake)

    person = relationships.find_person(vault, "20260701090000")
    assert person.dex_id == ""
    assert "dex-4" not in path.read_text(encoding="utf-8")
