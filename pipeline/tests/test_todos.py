"""Pass T tests: date resolution against a fake clock, toggle round-trip,
reminder fire-once. Hermetic — the fake llm reads the anchor date out of the
prompt (proving capture-time threading), and pushes are captured, not sent."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
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
    result = todos.toggle(config.vault_path, todo.block_id)
    assert result.done is True and result.spawned_block_id is None   # not recurring
    line = (todo.file).read_text().splitlines()[todo.line_no]
    assert line.startswith("- [x]") and f"^{todo.block_id}" in line  # edited in place
    assert todos.toggle(config.vault_path, todo.block_id).done is False   # round-trip back
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


def test_unified_digest_single_push_with_pipeline_summary(vault, tmp_path, monkeypatch):
    """Pass 5: ONE 8am push carries both the todo agenda and yesterday's
    pipeline summary — never two notifications."""
    config = make_config(vault)
    extract.extract("I need to call the dentist tomorrow 2pm.", "20260701143000",
                    CAPTURED, config, llm_fn=resolving_llm)  # due 2026-07-02
    events = EventLog(tmp_path / "events.db", config.vault_path)
    # yesterday (2026-07-01): two captures archived ok, one failure
    for f in ("a.m4a", "b.m4a"):
        events.conn.execute(
            "INSERT INTO events (timestamp, file, stage, status) VALUES (?,?,?,?)",
            ("2026-07-01T09:00:00", f"/in/{f}", "archive", "ok"))
    events.conn.execute(
        "INSERT INTO events (timestamp, file, stage, status, message) VALUES (?,?,?,?,?)",
        ("2026-07-01T10:00:00", "/in/c.m4a", "pipeline", "failed", "boom"))
    events.conn.commit()

    pushes = []
    monkeypatch.setattr(todos.errors, "ntfy",
                        lambda url, topic, msg, title="": pushes.append(msg))
    digest_morning = datetime(2026, 7, 2, 8, 5, tzinfo=todos.TZ)
    todos.tick(config, events, now=digest_morning)
    todos.tick(config, events, now=digest_morning + timedelta(hours=2))  # fire-once
    assert len(pushes) == 1                        # ONE notification, never several
    digest = pushes[0]
    assert "2 captured yesterday" in digest
    assert "1 failed" in digest
    assert "Today:" in digest and "dentist" in digest
    events.close()


def test_digest_pipeline_summary_only(vault, tmp_path, monkeypatch):
    """No todos at all — the digest still reports yesterday's pipeline work."""
    config = make_config(vault)
    events = EventLog(tmp_path / "events.db", config.vault_path)
    events.conn.execute(
        "INSERT INTO events (timestamp, file, stage, status) VALUES (?,?,?,?)",
        ("2026-07-01T09:00:00", "/in/a.m4a", "archive", "ok"))
    events.conn.commit()
    pushes = []
    monkeypatch.setattr(todos.errors, "ntfy",
                        lambda url, topic, msg, title="": pushes.append(msg))
    todos.tick(config, events, now=datetime(2026, 7, 2, 8, 5, tzinfo=todos.TZ))
    assert len(pushes) == 1
    assert "1 captured yesterday" in pushes[0]
    assert "Today:" not in pushes[0]
    events.close()


def test_digest_silent_when_nothing_happened(vault, tmp_path, monkeypatch):
    """Quiet pipeline + no todos → no push, and the key is marked so the day
    isn't re-checked."""
    config = make_config(vault)
    events = EventLog(tmp_path / "events.db", config.vault_path)
    pushes = []
    monkeypatch.setattr(todos.errors, "ntfy",
                        lambda url, topic, msg, title="": pushes.append(msg))
    now = datetime(2026, 7, 2, 8, 5, tzinfo=todos.TZ)
    todos.tick(config, events, now=now)
    assert pushes == []
    assert events.reminder_fired("digest-2026-07-02")
    events.close()


# ---- recurring todos (deferred sweep) ------------------------------------------

def _write_todo(vault, day, line):
    folder = vault / todos.TODOS_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{day}.md"
    with path.open("a") as f:
        if not path.exists() or path.stat().st_size == 0:
            f.write(f"# Todos — {day}\n\n")
        f.write(line + "\n")
    return path


def test_format_and_parse_round_trip_with_and_without_a_rule():
    plain = todos.format_line("water the plants", "20260701140000", 1, "2026-07-05", "09:00")
    assert "🔁" not in plain
    assert todos.parse_line(plain)[5] is None

    weekly = todos.format_line("water the plants", "20260701140000", 1,
                               "2026-07-05", "09:00", "every week")
    assert "🔁 every week" in weekly
    task, done, due, time, block, recur = todos.parse_line(weekly)
    assert (task, done, due, time, block, recur) == (
        "water the plants", False, "2026-07-05", "09:00", "20260701140000-1", "every week")


@pytest.mark.parametrize("text,expected", [
    ("every day", (1, "day")),
    ("every week", (1, "week")),
    ("every month", (1, "month")),
    ("every 3 days", (3, "day")),
    ("every 2 weeks", (2, "week")),
    ("EVERY WEEK", (1, "week")),
])
def test_parse_recurrence(text, expected):
    rule = todos.parse_recurrence(text)
    assert (rule.n, rule.unit) == expected


@pytest.mark.parametrize("text", [
    None, "", "every other Tuesday", "weekly", "every fortnight", "every 0 days",
])
def test_an_unknown_rule_is_inert_rather_than_broken(text):
    """A hand-typed rule this module can't advance must leave the todo working —
    it just doesn't repeat."""
    assert todos.parse_recurrence(text) is None


def test_an_unparseable_rule_still_leaves_a_valid_todo_line():
    line = "- [ ] water the plants 📅 2026-07-05 🔁 every other Tuesday ^abc-1"
    parsed = todos.parse_line(line)
    assert parsed is not None
    assert parsed[0] == "water the plants" and parsed[4] == "abc-1"
    assert todos.parse_recurrence(parsed[5]) is None


@pytest.mark.parametrize("start,rule,expected", [
    ("2026-07-05", "every day", "2026-07-06"),
    ("2026-07-05", "every 3 days", "2026-07-08"),
    ("2026-07-05", "every week", "2026-07-12"),
    ("2026-07-05", "every 2 weeks", "2026-07-19"),
    ("2026-07-05", "every month", "2026-08-05"),
    # month-end clamps rather than overflowing into the next month
    ("2026-01-31", "every month", "2026-02-28"),
    ("2028-01-31", "every month", "2028-02-29"),   # leap year
    ("2026-03-31", "every month", "2026-04-30"),
    ("2026-12-15", "every month", "2027-01-15"),   # year rollover
])
def test_next_due(start, rule, expected):
    assert todos.next_due(date.fromisoformat(start),
                          todos.parse_recurrence(rule)).isoformat() == expected


def test_completing_a_recurring_todo_spawns_the_next_occurrence(tmp_path):
    vault = tmp_path / "vault"
    _write_todo(vault, "2026-07-05",
                "- [ ] water the plants (from [[20260701140000]]) 📅 2026-07-05 "
                "⏰ 09:00 🔁 every week ^20260701140000-1")

    result = todos.toggle(vault, "20260701140000-1")
    assert result.done is True
    assert result.spawned_due == "2026-07-12"
    assert result.spawned_block_id == "20260701140000-1-r1"

    spawned = (vault / todos.TODOS_FOLDER / "2026-07-12.md").read_text()
    assert "- [ ] water the plants" in spawned
    assert "📅 2026-07-12" in spawned
    assert "⏰ 09:00" in spawned                      # the time carries over
    assert "🔁 every week" in spawned                 # so does the rule
    assert "(from [[20260701140000]])" in spawned     # and the provenance
    assert "^20260701140000-1-r1" in spawned          # a fresh id → its own reminder

    original = (vault / todos.TODOS_FOLDER / "2026-07-05.md").read_text()
    assert "- [x] water the plants" in original       # the completed one stays as the record


def test_reopening_never_deletes_the_spawned_occurrence_and_never_duplicates(tmp_path):
    vault = tmp_path / "vault"
    _write_todo(vault, "2026-07-05",
                "- [ ] water the plants 📅 2026-07-05 🔁 every week ^base-1")
    todos.toggle(vault, "base-1")
    todos.toggle(vault, "base-1")      # reopened
    again = todos.toggle(vault, "base-1")   # completed a second time

    assert again.spawned_block_id is None   # the duplicate guard held
    spawned_file = (vault / todos.TODOS_FOLDER / "2026-07-12.md").read_text()
    assert spawned_file.count("water the plants") == 1
    assert "^base-1-r1" in spawned_file     # and the occurrence was never removed


def test_occurrences_chain_without_reusing_an_id(tmp_path):
    vault = tmp_path / "vault"
    _write_todo(vault, "2026-07-05",
                "- [ ] standup notes 📅 2026-07-05 🔁 every week ^base-1")
    first = todos.toggle(vault, "base-1")
    second = todos.toggle(vault, first.spawned_block_id)
    third = todos.toggle(vault, second.spawned_block_id)

    assert [first.spawned_block_id, second.spawned_block_id, third.spawned_block_id] == \
        ["base-1-r1", "base-1-r2", "base-1-r3"]
    assert third.spawned_due == "2026-07-26"
    ids = {t.block_id for t in todos.scan(vault)}
    assert len(ids) == 4                      # every occurrence kept its own id


def test_a_recurring_todo_without_a_due_date_does_not_spawn(tmp_path):
    """Nothing to advance from — the rule needs a date to repeat."""
    vault = tmp_path / "vault"
    _write_todo(vault, "2026-07-05", "- [ ] someday thing 🔁 every week ^base-1")
    result = todos.toggle(vault, "base-1")
    assert result.done is True and result.spawned_block_id is None


def test_non_recurring_todos_behave_exactly_as_before(tmp_path):
    vault = tmp_path / "vault"
    path = _write_todo(vault, "2026-07-05", "- [ ] one-off 📅 2026-07-05 ^base-1")
    before = {p.name for p in (vault / todos.TODOS_FOLDER).iterdir()}
    result = todos.toggle(vault, "base-1")
    assert result.done is True and result.spawned_block_id is None
    assert {p.name for p in (vault / todos.TODOS_FOLDER).iterdir()} == before
    assert "- [x] one-off" in path.read_text()
