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
    text = (vault / "vault" / "06-Todos" / "2026-07-01.md").read_text(encoding="utf-8")
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
    line = (todo.file).read_text(encoding="utf-8").splitlines()[todo.line_no]
    assert line.startswith("- [x]") and f"^{todo.block_id}" in line  # edited in place
    assert todos.toggle(config.vault_path, todo.block_id) is False   # round-trip back
    assert "- [ ]" in todo.file.read_text(encoding="utf-8")
    with pytest.raises(LookupError):
        todos.toggle(config.vault_path, "nope")


def test_reminder_fire_once(vault, tmp_path, monkeypatch):
    config = make_config(vault)
    extract.extract("I need to call the dentist tomorrow 2pm.", "20260701143000",
                    CAPTURED, config, llm_fn=resolving_llm)
    events = EventLog(tmp_path / "events.db", config.vault_path)
    pushes = []
    monkeypatch.setattr(todos.errors, "ntfy", lambda url, topic, msg, title="", click="": pushes.append(msg))

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
                        lambda url, topic, msg, title="", click="": pushes.append(msg))
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
                        lambda url, topic, msg, title="", click="": pushes.append(msg))
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
                        lambda url, topic, msg, title="", click="": pushes.append(msg))
    now = datetime(2026, 7, 2, 8, 5, tzinfo=todos.TZ)
    todos.tick(config, events, now=now)
    assert pushes == []
    assert events.reminder_fired("digest-2026-07-02")
    events.close()


# ---- the People section rides the SAME digest (Pass MW) -------------------------

def _person(vault_path, name, person_id, last_contact, cadence="7"):
    folder = vault_path / "07-People"
    folder.mkdir(exist_ok=True)
    (folder / f"2026-07-01-{name.lower().replace(' ', '-')}.md").write_text(
        f"---\nid: {person_id}\ntype: person\ncreated: 2026-07-01\nsource: manual\n"
        f"origin: human\nchannels: {{whatsapp: +971}}\ncadence_days: {cadence}\n"
        f"last_contact: {last_contact}\nwarmth_stage: engaging\nstatus: active\n---\n\n"
        f"# {name}\n\n## Interaction log\n\n- {last_contact} — spoke\n", encoding="utf-8")


def test_people_ride_the_unified_digest_and_never_a_second_push(vault, tmp_path, monkeypatch):
    """MW1: one notification in the morning. The People section is part of the
    todo digest, not a push of its own."""
    config = make_config(vault)
    _person(config.vault_path, "Priya Raman", "20260701090000", "2026-06-01")
    todos_file = config.vault_path / "06-Todos" / "2026-07-01.md"
    todos_file.parent.mkdir(parents=True, exist_ok=True)
    todos_file.write_text("# Todos — 2026-07-01\n\n- [ ] send the deck 📅 2026-07-01 ^a-1\n", encoding="utf-8")

    pushes = []
    monkeypatch.setattr(todos.errors, "ntfy",
                        lambda url, topic, msg, title="", click="": pushes.append((title, msg)))
    events = EventLog(tmp_path / "events.db", config.vault_path)
    todos.tick(config, events, now=datetime(2026, 7, 1, 8, 30, tzinfo=todos.TZ))

    assert len(pushes) == 1, "one morning, one notification"
    title, body = pushes[0]
    assert title == "Brain Cockpit — today"
    assert "send the deck" in body            # the todo half
    assert "People:" in body                  # the relationship half
    assert "Priya Raman" in body and "days quiet" in body

    # fire-once: a later tick the same day says nothing more
    todos.tick(config, events, now=datetime(2026, 7, 1, 11, 0, tzinfo=todos.TZ))
    assert len(pushes) == 1
    events.close()


def test_a_person_going_cold_is_reason_enough_to_push(vault, tmp_path, monkeypatch):
    """An otherwise silent day still speaks up if someone is slipping away."""
    config = make_config(vault)
    _person(config.vault_path, "Quiet Friend", "20260701090001", "2026-05-01")

    pushes = []
    monkeypatch.setattr(todos.errors, "ntfy",
                        lambda url, topic, msg, title="", click="": pushes.append(msg))
    events = EventLog(tmp_path / "events.db", config.vault_path)
    todos.tick(config, events, now=datetime(2026, 7, 1, 8, 5, tzinfo=todos.TZ))
    assert len(pushes) == 1 and "Quiet Friend" in pushes[0]
    events.close()


def test_a_truly_quiet_day_stays_quiet(vault, tmp_path, monkeypatch):
    """Nobody cold, nothing due — still no push (the silence rule survives)."""
    config = make_config(vault)
    _person(config.vault_path, "Fresh Contact", "20260701090002", "2026-07-01", cadence="30")

    pushes = []
    monkeypatch.setattr(todos.errors, "ntfy",
                        lambda url, topic, msg, title="", click="": pushes.append(msg))
    events = EventLog(tmp_path / "events.db", config.vault_path)
    todos.tick(config, events, now=datetime(2026, 7, 1, 8, 5, tzinfo=todos.TZ))
    assert pushes == []
    events.close()


def test_digest_click_links_to_people_when_the_section_is_non_empty(vault, tmp_path, monkeypatch):
    """D5: the digest was planned to deep-link into the People screen when it
    has something to say there, so the push is one tap from the going-cold
    list instead of a dead-end notification."""
    config = make_config(vault, deploy={"public_url": "https://cockpit.example.com"})
    _person(config.vault_path, "Quiet Friend", "20260701090001", "2026-05-01")

    calls = []
    monkeypatch.setattr(todos.errors, "ntfy",
                        lambda url, topic, msg, title="", click="": calls.append(click))
    events = EventLog(tmp_path / "events.db", config.vault_path)
    todos.tick(config, events, now=datetime(2026, 7, 1, 8, 5, tzinfo=todos.TZ))
    assert calls == ["https://cockpit.example.com/#people"]
    events.close()


def test_digest_click_is_the_plain_url_when_people_has_nothing_to_say(vault, tmp_path, monkeypatch):
    config = make_config(vault, deploy={"public_url": "https://cockpit.example.com"})
    extract.extract("I need to call the dentist tomorrow 2pm.", "20260701143000",
                    CAPTURED, config, llm_fn=resolving_llm)

    calls = []
    monkeypatch.setattr(todos.errors, "ntfy",
                        lambda url, topic, msg, title="", click="": calls.append(click))
    events = EventLog(tmp_path / "events.db", config.vault_path)
    todos.tick(config, events, now=datetime(2026, 7, 2, 8, 5, tzinfo=todos.TZ))
    assert calls == ["https://cockpit.example.com"]
    events.close()


def test_digest_click_is_empty_without_a_configured_public_url(vault, tmp_path, monkeypatch):
    config = make_config(vault)
    _person(config.vault_path, "Quiet Friend", "20260701090001", "2026-05-01")

    calls = []
    monkeypatch.setattr(todos.errors, "ntfy",
                        lambda url, topic, msg, title="", click="": calls.append(click))
    events = EventLog(tmp_path / "events.db", config.vault_path)
    todos.tick(config, events, now=datetime(2026, 7, 1, 8, 5, tzinfo=todos.TZ))
    assert calls == [""]
    events.close()


# ---- triage queue count line (Task R4, B9) ---------------------------------

def _needs_review_note(vault_path, name: str) -> None:
    inbox = vault_path / "00-Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"{name}.md").write_text(
        "---\nstatus: needs-review\n---\n\nbody\n", encoding="utf-8")


def test_digest_triage_count_line_ordered_between_drain_and_resurfaced(vault, tmp_path, monkeypatch):
    """Order: pipeline summary → drain → triage count → Resurfaced → Overdue
    → Today → People."""
    config = make_config(vault)
    for name in ("a", "b", "c"):
        _needs_review_note(config.vault_path, name)
    resurface_dir = config.vault_path / "02-Musings"
    resurface_dir.mkdir(parents=True, exist_ok=True)
    (resurface_dir / "2020-01-01-an-old-hunch.md").write_text(
        "---\nid: n1\ntype: musing\ncreated: 2020-01-01\nstatus: active\n---\n\n"
        "A hunch worth revisiting.\n", encoding="utf-8")

    events = EventLog(tmp_path / "events.db", config.vault_path)
    events.conn.execute(
        "INSERT INTO events (timestamp, file, stage, status, message) VALUES (?,?,?,?,?)",
        ("2026-06-30T03:00:00", str(config.vault_path), "drain", "ok", "filed=7 parked=2"))
    events.conn.commit()

    pushes = []
    monkeypatch.setattr(todos.errors, "ntfy",
                        lambda url, topic, msg, title="", click="": pushes.append(msg))
    todos.tick(config, events, now=datetime(2026, 7, 1, 8, 5, tzinfo=todos.TZ))
    assert len(pushes) == 1
    digest = pushes[0]
    assert "3 waiting in triage." in digest
    assert digest.index("old items filed") < digest.index("3 waiting in triage.") \
        < digest.index("Resurfaced:")
    events.close()


def test_digest_omits_triage_line_when_queue_is_empty(vault, tmp_path, monkeypatch):
    config = make_config(vault)
    todos_dir = config.vault_path / "06-Todos"
    todos_dir.mkdir(parents=True, exist_ok=True)
    (todos_dir / "2026-07-01.md").write_text(
        "# Todos — 2026-07-01\n\n- [ ] water the plants 📅 2026-07-01 ^t-1\n", encoding="utf-8")

    events = EventLog(tmp_path / "events.db", config.vault_path)
    pushes = []
    monkeypatch.setattr(todos.errors, "ntfy",
                        lambda url, topic, msg, title="", click="": pushes.append(msg))
    todos.tick(config, events, now=datetime(2026, 7, 1, 8, 5, tzinfo=todos.TZ))
    assert len(pushes) == 1
    assert "waiting in triage" not in pushes[0]
    events.close()


def test_digest_fires_when_triage_count_is_the_only_signal(vault, tmp_path, monkeypatch):
    """No captures, no drain, no resurface candidates, no todos, no people —
    a nonzero triage queue alone must still fire the digest (the same class
    of early-return bug A5 already fixed for drain_filed)."""
    config = make_config(vault)
    _needs_review_note(config.vault_path, "only-signal")

    events = EventLog(tmp_path / "events.db", config.vault_path)
    pushes = []
    monkeypatch.setattr(todos.errors, "ntfy",
                        lambda url, topic, msg, title="", click="": pushes.append(msg))
    todos.tick(config, events, now=datetime(2026, 7, 1, 8, 5, tzinfo=todos.TZ))
    assert len(pushes) == 1
    assert "1 waiting in triage." in pushes[0]
    events.close()


# ---- Task E2: recurring todos ------------------------------------------------

def _write_todos_file(vault_path, name: str, text: str):
    todos_dir = vault_path / todos.TODOS_FOLDER
    todos_dir.mkdir(parents=True, exist_ok=True)
    (todos_dir / name).write_text(text, encoding="utf-8")


def test_recur_marker_parses_and_coexists_with_other_markers(vault):
    daily = todos.parse_line("- [ ] water plants 🔁 daily 📅 2026-09-01 ^id-1")
    assert daily[7] == "daily"
    weekly = todos.parse_line("- [ ] water plants 🔁 weekly 📅 2026-09-01 ^id-1")
    assert weekly[7] == "weekly"
    plain = todos.parse_line("- [ ] water plants 📅 2026-09-01 ^id-1")
    assert plain[7] is None
    # every other marker still parses correctly with 🔁 present too
    full = todos.parse_line("- [ ] water plants 🔁 daily 📅 2026-09-01 ⏰ 09:00 ^id-1 🎚3")
    task, done, due, time, block, indent, feel, recur = full
    assert task == "water plants"
    assert done is False
    assert due == "2026-09-01"
    assert time == "09:00"
    assert block == "id-1"
    assert indent is False
    assert feel == 3
    assert recur == "daily"


def test_daily_spawn_math(vault):
    config = make_config(vault)
    _write_todos_file(config.vault_path, "2026-07-05.md",
                       "- [ ] water plants 🔁 daily 📅 2026-07-05 ^id-1\n")

    assert todos.toggle(config.vault_path, "id-1") is True

    spawned_file = config.vault_path / todos.TODOS_FOLDER / "2026-07-06.md"
    assert spawned_file.exists()
    text = spawned_file.read_text(encoding="utf-8")
    assert "- [ ] water plants 🔁 daily 📅 2026-07-06 ^id-1r2" in text
    assert text.startswith("# Todos — 2026-07-06\n\n")


def test_weekly_spawn_math(vault):
    config = make_config(vault)
    _write_todos_file(config.vault_path, "2026-07-05.md",
                       "- [ ] water plants 🔁 weekly 📅 2026-07-05 ^id-1\n")

    assert todos.toggle(config.vault_path, "id-1") is True

    spawned_file = config.vault_path / todos.TODOS_FOLDER / "2026-07-12.md"
    assert spawned_file.exists()
    text = spawned_file.read_text(encoding="utf-8")
    assert "- [ ] water plants 🔁 weekly 📅 2026-07-12 ^id-1r2" in text


def test_month_boundary_spawn_math(vault):
    config = make_config(vault)
    _write_todos_file(config.vault_path, "2026-07-28.md",
                       "- [ ] water plants 🔁 daily 📅 2026-07-28 ^id-daily\n")
    todos.toggle(config.vault_path, "id-daily")
    daily_next = config.vault_path / todos.TODOS_FOLDER / "2026-07-29.md"
    assert daily_next.exists()
    assert "📅 2026-07-29" in daily_next.read_text(encoding="utf-8")

    _write_todos_file(config.vault_path, "2026-07-30.md",
                       "- [ ] pay rent 🔁 weekly 📅 2026-07-30 ^id-weekly\n")
    todos.toggle(config.vault_path, "id-weekly")
    weekly_next = config.vault_path / todos.TODOS_FOLDER / "2026-08-06.md"  # crosses into August
    assert weekly_next.exists()
    assert "📅 2026-08-06" in weekly_next.read_text(encoding="utf-8")


def test_double_toggle_spawns_once_and_never_deletes_spawned_line(vault):
    config = make_config(vault)
    _write_todos_file(config.vault_path, "2026-07-05.md",
                       "- [ ] water plants 🔁 daily 📅 2026-07-05 ^id-1\n")
    r2_file = config.vault_path / todos.TODOS_FOLDER / "2026-07-06.md"

    assert todos.toggle(config.vault_path, "id-1") is True   # open -> done: spawns r2
    assert "^id-1r2" in r2_file.read_text(encoding="utf-8")

    assert todos.toggle(config.vault_path, "id-1") is False  # done -> open: no delete, no spawn
    assert "^id-1r2" in r2_file.read_text(encoding="utf-8")  # r2 line still there, untouched
    ids_after_reopen = {t.block_id for t in todos.scan(config.vault_path)}
    assert "id-1r3" not in ids_after_reopen

    assert todos.toggle(config.vault_path, "id-1") is True   # open -> done again: spawns r3, not a dup r2
    assert r2_file.read_text(encoding="utf-8").count("^id-1r2") == 1
    ids_after_recomplete = {t.block_id for t in todos.scan(config.vault_path)}
    assert "id-1r3" in ids_after_recomplete


def test_nonrecurring_todo_toggle_spawns_nothing(vault):
    config = make_config(vault)
    _write_todos_file(config.vault_path, "2026-07-05.md",
                       "- [ ] call the dentist 📅 2026-07-05 ^id-1\n")
    before = {p.name for p in (config.vault_path / todos.TODOS_FOLDER).glob("*.md")}

    assert todos.toggle(config.vault_path, "id-1") is True
    assert todos.toggle(config.vault_path, "id-1") is False

    after = {p.name for p in (config.vault_path / todos.TODOS_FOLDER).glob("*.md")}
    assert after == before  # no new file anywhere


def test_recurring_todo_with_no_due_date_does_not_crash_or_spawn(vault):
    config = make_config(vault)
    # hand-edited/malformed line: 🔁 present, 📅 absent
    _write_todos_file(config.vault_path, "2026-07-05.md",
                       "- [ ] water plants 🔁 daily ^id-1\n")
    before = {p.name for p in (config.vault_path / todos.TODOS_FOLDER).glob("*.md")}

    assert todos.toggle(config.vault_path, "id-1") is True  # no crash

    after = {p.name for p in (config.vault_path / todos.TODOS_FOLDER).glob("*.md")}
    assert after == before  # no spawn


def test_rollup_completion_of_recurring_parent_spawns_next_occurrence(vault):
    config = make_config(vault)
    _write_todos_file(config.vault_path, "2026-07-05.md",
                       "- [ ] plan the offsite 🔁 weekly 📅 2026-07-05 ^id-1\n"
                       "    - [ ] book the room ^id-1a\n"
                       "    - [ ] draft the agenda ^id-1b\n")
    spawned_dir = config.vault_path / todos.TODOS_FOLDER

    todos.toggle(config.vault_path, "id-1a")  # one sibling still open — parent not done yet
    assert not (spawned_dir / "2026-07-12.md").exists()

    todos.toggle(config.vault_path, "id-1b")  # both siblings done -> parent rolls up to done
    parent = next(t for t in todos.scan(config.vault_path) if t.block_id == "id-1")
    assert parent.done is True
    assert (spawned_dir / "2026-07-12.md").exists()
    assert "^id-1r2" in (spawned_dir / "2026-07-12.md").read_text(encoding="utf-8")


def test_rollup_parent_returns_true_only_on_open_to_done_transition(vault):
    config = make_config(vault)
    _write_todos_file(config.vault_path, "2026-07-05.md",
                       "- [ ] plan the offsite 📅 2026-07-05 ^id-1\n"
                       "    - [ ] book the room ^id-1a\n"
                       "    - [ ] draft the agenda ^id-1b\n")

    def rescan_parent():
        return next(t for t in todos.scan(config.vault_path) if t.block_id == "id-1")

    # flip 1a done: one sibling still open -> not a transition
    parent = rescan_parent()
    child_a = next(c for c in parent.children if c.block_id == "id-1a")
    new_done = todos._flip_line(child_a.file, child_a.line_no, child_a.done)
    assert todos._rollup_parent(parent, child_a, new_done) is False

    # flip 1b done: both now done -> genuine open -> done transition
    parent = rescan_parent()
    child_b = next(c for c in parent.children if c.block_id == "id-1b")
    new_done = todos._flip_line(child_b.file, child_b.line_no, child_b.done)
    assert todos._rollup_parent(parent, child_b, new_done) is True

    # calling again with the parent already done and all children done: no-op
    parent = rescan_parent()
    child_b = next(c for c in parent.children if c.block_id == "id-1b")
    assert todos._rollup_parent(parent, child_b, True) is False

    # reopen 1b: parent un-marks (reverse direction), must never return True
    parent = rescan_parent()
    child_b = next(c for c in parent.children if c.block_id == "id-1b")
    new_done = todos._flip_line(child_b.file, child_b.line_no, child_b.done)
    assert new_done is False
    assert todos._rollup_parent(parent, child_b, new_done) is False
