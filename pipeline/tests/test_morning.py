"""Task 13 — the People digest rewritten on top of the v2.2 queue engine
(`queue.build`/`queue.strip`) and the status heartbeat (`ledger.advance_statuses`)
instead of the legacy `relationships.needs_attention` read."""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import morning, queue, relationships as rel

TODAY = date(2026, 9, 17)


def _run(*args, cwd=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r


def cfg(vault):
    return SimpleNamespace(vault_path=vault, raw={})


_next_id = iter(range(1, 1000))


def _write_person(folder: Path, name: str, *, next_action="", last_contact="2026-08-01",
                  cadence="", tier="", status="active"):
    path = folder / f"2026-07-01-{name.lower().replace(' ', '-')}.md"
    path.write_text(
        "---\n"
        f"id: 2026070109{next(_next_id):04d}\n"
        "type: person\n"
        "created: 2026-07-01\n"
        "source: manual\n"
        "origin: human\n"
        "relationship: client\n"
        "company: Alserkal\n"
        "channels: {whatsapp: +971500000000}\n"
        f"tier: {tier}\n"
        f"cadence_days: {cadence}\n"
        f"last_contact: {last_contact}\n"
        f"status: {status}\n"
        "---\n\n"
        f"# {name}\n\n## Interaction log\n\n- 2026-08-01 · out · whatsapp · remember · caught up\n\n"
        f"## Next action\n\n{next_action}\n", encoding="utf-8")
    return path


@pytest.fixture
def vault(tmp_path):
    (tmp_path / rel.PEOPLE_FOLDER).mkdir()
    return tmp_path


def test_no_people_no_lines(vault):
    assert morning.people_section(cfg(vault), TODAY) == []


def test_digest_lists_at_most_five(vault):
    folder = vault / rel.PEOPLE_FOLDER
    for i in range(6):
        _write_person(folder, f"Person {i}", next_action=f"- open · I promised: send doc {i}")
    lines = morning.people_section(cfg(vault), TODAY)
    assert lines[0] == "People:"
    # five people plus the header plus one overflow line
    assert len(lines) == 7
    assert all(LABEL in lines[i] for i, LABEL in
              [(1, queue.LABELS["promises"]), (5, queue.LABELS["promises"])])
    assert "1 more on the People screen" in lines[-1]


def test_digest_falls_back_to_reconnect_without_payload(vault):
    folder = vault / rel.PEOPLE_FOLDER
    # No promises, no dates, no touch to thank for, tier "" (not inner) — the
    # reconnect item this person gets has an empty payload, which `strip()`
    # would otherwise drop entirely. The digest rule says a going-cold person
    # still has to push.
    _write_person(folder, "Quiet Friend", last_contact="2026-08-01", cadence="7")
    lines = morning.people_section(cfg(vault), TODAY)
    assert lines[0] == "People:"
    assert any("Quiet Friend" in line and "no payload yet" in line for line in lines)
    assert any(queue.LABELS["reconnect"] in line for line in lines)


def test_status_heartbeat_commits_before_and_after(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    _run("init", "-q", cwd=vault)
    _run("config", "user.email", "t@t", cwd=vault)
    _run("config", "user.name", "t", cwd=vault)
    (vault / "README.md").write_text("seed\n", encoding="utf-8")
    _run("add", "-A", cwd=vault)
    _run("commit", "-q", "-m", "seed", cwd=vault)
    (vault / rel.PEOPLE_FOLDER).mkdir(parents=True)

    # tiered, well past 3x cadence -> status_for computes "dormant" while the
    # note on disk still says "active": advance_statuses must flip it.
    _write_person(vault / rel.PEOPLE_FOLDER, "Cold Client", last_contact="2026-01-01",
                 cadence="7", tier="core", status="active")

    morning.people_section(cfg(vault), TODAY)

    log = _run("log", "--format=%s", cwd=vault).stdout.splitlines()
    assert "morning: before status heartbeat" in log
    assert any(m.startswith("morning: status heartbeat (") for m in log)
    # the before-commit must land BEFORE the after-commit (log is newest-first)
    before_i = log.index("morning: before status heartbeat")
    after_i = next(i for i, m in enumerate(log) if m.startswith("morning: status heartbeat ("))
    assert after_i < before_i

    people = rel.load_people(vault)
    assert people[0].status == "dormant"
