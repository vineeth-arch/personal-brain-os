"""Pass T tests: date resolution against a fake clock, toggle round-trip,
reminder fire-once. Hermetic — the fake llm reads the anchor date out of the
prompt (proving capture-time threading), and pushes are captured, not sent."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import extract, todos
from pipeline.events import EventLog

# fake clock: capture happened Wednesday 2026-07-01 14:30 Asia/Kolkata
CAPTURED = datetime(2026, 7, 1, 14, 30, tzinfo=todos.TZ)


def make_config(tmp_path, **raw_extra):
    return SimpleNamespace(
        vault_path=tmp_path / "vault",
        ntfy_url="https://ntfy.example", ntfy_topic="t",
        anthropic_key=None,
        raw={"todos": {"digest": True}, **raw_extra},
    )


def resolving_llm(prompt: str, config) -> str:
    """A fake model that RESOLVES relative dates from the anchor in the prompt —
    so the test proves the capture timestamp is threaded correctly."""
    m = re.search(r"Captured at: \w+ (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})", prompt)
    anchor = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
    out = []
    for line in prompt.splitlines():
        if not line.startswith("- "):
            continue
        task = line[2:]
        if "tomorrow" in task and "2pm" in task:
            due = (anchor + timedelta(days=1)).strftime("%Y-%m-%dT14:00")
            out.append({"task": task, "due": due, "remind": True})
        elif "Friday" in task:
            days_ahead = (4 - anchor.weekday()) % 7 or 7  # NEXT Friday after capture
            due = (anchor + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
            out.append({"task": task, "due": due, "remind": False})
        else:
            out.append({"task": task, "due": None, "remind": False})
    return json.dumps(out)


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "vault").mkdir()
    return tmp_path


def test_date_resolution_tomorrow_friday_none(vault):
    config = make_config(vault)
    transcript = ("I need to call the dentist tomorrow 2pm. "
                  "Remember to send the invoice Friday. "
                  "I should read that pricing book sometime.")
    items = extract.extract(transcript, "20260701143000", CAPTURED, config, llm_fn=resolving_llm)
    by_due = {i.due_iso for i in items}
    assert "2026-07-02T14:00" in by_due          # tomorrow 2pm, from the fake clock
    assert "2026-07-03" in by_due                # Friday after Wednesday capture
    assert None in by_due                        # "sometime" → never guessed
    # written lines: markers only when known, block ids present
    text = (vault / "vault" / "06-Todos" / "2026-07-01.md").read_text()
    assert "📅 2026-07-02 ⏰ 14:00" in text
    assert "📅 2026-07-03" in text and "⏰" not in text.split("📅 2026-07-03")[1].splitlines()[0]
    assert "^20260701143000-1" in text


def test_no_llm_degrades_to_undated(vault):
    config = make_config(vault)  # anthropic_key None + no llm_fn → heuristic only
    items = extract.extract("Don't forget to water the plants tomorrow.",
                            "20260701143000", CAPTURED, config, llm_fn=None)
    assert len(items) == 1 and items[0].due_iso is None  # degraded, never guessed


def test_invalid_llm_json_degrades(vault):
    config = make_config(vault)
    items = extract.extract("I must fix the sink.", "20260701143000", CAPTURED, config,
                            llm_fn=lambda p, c: "not json at all")
    assert len(items) == 1 and items[0].due_iso is None


def test_toggle_round_trip(vault):
    config = make_config(vault)
    extract.extract("I need to call the dentist tomorrow 2pm.", "20260701143000",
                    CAPTURED, config, llm_fn=resolving_llm)
    [todo] = [t for t in todos.scan(config.vault_path) if t.block_id]
    assert not todo.done
    assert todos.toggle(config.vault_path, todo.block_id) is True
    line = (todo.file).read_text().splitlines()[todo.line_no]
    assert line.startswith("- [x]") and f"^{todo.block_id}" in line  # edited in place
    assert todos.toggle(config.vault_path, todo.block_id) is False   # round-trip back
    assert "- [ ]" in todo.file.read_text()
    with pytest.raises(LookupError):
        todos.toggle(config.vault_path, "nope")


def test_reminder_fire_once(vault, tmp_path, monkeypatch):
    config = make_config(vault)
    extract.extract("I need to call the dentist tomorrow 2pm.", "20260701143000",
                    CAPTURED, config, llm_fn=resolving_llm)
    events = EventLog(tmp_path / "events.db", config.vault_path)
    pushes = []
    monkeypatch.setattr(todos.errors, "ntfy", lambda url, topic, msg, title="": pushes.append(msg))

    before_due = datetime(2026, 7, 2, 13, 0, tzinfo=todos.TZ)
    at_due = datetime(2026, 7, 2, 14, 1, tzinfo=todos.TZ)

    todos.tick(config, events, now=before_due)
    reminder_pushes = [p for p in pushes if p.startswith("Due now")]
    assert reminder_pushes == []                       # not due yet
    todos.tick(config, events, now=at_due)
    todos.tick(config, events, now=at_due + timedelta(minutes=5))
    reminder_pushes = [p for p in pushes if p.startswith("Due now")]
    assert len(reminder_pushes) == 1                   # fired exactly once
    assert "dentist" in reminder_pushes[0]

    # digest: fires once per day after 8am, overdue items persist next day
    pushes.clear()
    day_after = datetime(2026, 7, 3, 9, 0, tzinfo=todos.TZ)
    todos.tick(config, events, now=day_after)
    todos.tick(config, events, now=day_after + timedelta(hours=1))
    digests = [p for p in pushes if "Overdue" in p]
    assert len(digests) == 1 and "dentist" in digests[0]  # still listed until done
    events.close()
