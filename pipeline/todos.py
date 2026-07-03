"""Todo lines: Obsidian Tasks-compatible checkboxes in 06-Todos/<date>.md.

Line format (markers only when known; block-id enables API round-trips):
    - [ ] task text (from [[20260703140000]]) 📅 2026-07-05 ⏰ 14:00 ^20260703140000-1

Completing a todo flips "- [ ]" to "- [x]" IN PLACE — lines are never deleted.
The reminder tick runs inside the watcher's --loop (no new process) and fires
each reminder exactly once via the reminders table in events.db. The user's
timezone is Asia/Kolkata for everything date-shaped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import errors

TZ = ZoneInfo("Asia/Kolkata")
TODOS_FOLDER = "06-Todos"

_FROM_RE = re.compile(r"\s*\(from \[\[[\w-]+\]\]\)$")

_LINE_RE = re.compile(
    r"^- \[(?P<done>[ x])\] (?P<task>.*?)"
    r"(?: 📅 (?P<due>\d{4}-\d{2}-\d{2}))?"
    r"(?: ⏰ (?P<time>\d{2}:\d{2}))?"
    r"(?: \^(?P<block>[\w-]+))?$"
)


@dataclass
class Todo:
    file: Path
    line_no: int
    task: str
    done: bool
    due: str | None        # YYYY-MM-DD
    time: str | None       # HH:MM — presence means "remind me at this time"
    block_id: str | None


def format_line(task: str, note_id: str, index: int,
                due: str | None, time: str | None) -> str:
    parts = [f"- [ ] {task} (from [[{note_id}]])"]
    if due:
        parts.append(f"📅 {due}")
        if time:
            parts.append(f"⏰ {time}")
    parts.append(f"^{note_id}-{index}")
    return " ".join(parts)


def parse_line(line: str) -> tuple[str, bool, str | None, str | None, str | None] | None:
    m = _LINE_RE.match(line.rstrip())
    if not m:
        return None
    task = _FROM_RE.sub("", m["task"].strip())  # provenance stays in the file, not the UI
    return (task, m["done"] == "x", m["due"], m["time"], m["block"])


def scan(vault: Path) -> list[Todo]:
    todos_dir = Path(vault) / TODOS_FOLDER
    if not todos_dir.is_dir():
        return []
    out: list[Todo] = []
    for path in sorted(todos_dir.glob("*.md")):
        for i, line in enumerate(path.read_text().splitlines()):
            parsed = parse_line(line)
            if parsed:
                task, done, due, time, block = parsed
                out.append(Todo(path, i, task, done, due, time, block))
    return out


def toggle(vault: Path, block_id: str) -> bool:
    """Flip the checkbox of the line carrying ^block_id. Returns the new done
    state. Raises LookupError if no line carries that id."""
    for todo in scan(vault):
        if todo.block_id == block_id:
            lines = todo.file.read_text().splitlines()
            line = lines[todo.line_no]
            if todo.done:
                lines[todo.line_no] = line.replace("- [x]", "- [ ]", 1)
            else:
                lines[todo.line_no] = line.replace("- [ ]", "- [x]", 1)
            todo.file.write_text("\n".join(lines) + "\n")
            return not todo.done
    raise LookupError(block_id)


# ---- ranges (all dates in the user's timezone) --------------------------------

def today_kolkata() -> date:
    return datetime.now(TZ).date()

def in_range(todo: Todo, range_name: str, today: date | None = None) -> bool:
    if todo.due is None:
        return False
    today = today or today_kolkata()
    try:
        due = date.fromisoformat(todo.due)
    except ValueError:
        return False
    if range_name == "today":
        return due == today
    if range_name == "overdue":
        return due < today and not todo.done
    if range_name == "tomorrow":
        return due == today + timedelta(days=1)
    if range_name == "week":
        return today + timedelta(days=1) < due <= today + timedelta(days=7)
    return False


# ---- the --loop tick: reminders + optional 8am digest --------------------------

def tick(config, events, now: datetime | None = None) -> None:
    """Called every watcher --loop pass. Never raises — a reminder hiccup must
    not stop the pipeline (same never-abort rule as ntfy itself)."""
    try:
        _tick(config, events, now or datetime.now(TZ))
    except Exception:
        # the event log is the durable record; reminders are best-effort
        import logging
        logging.getLogger("pipeline").exception("todo tick failed")


def _tick(config, events, now: datetime) -> None:
    todos = scan(config.vault_path)

    # 1. due-time reminders: a ⏰ time on an open todo means "push me at that
    # time". Fire on the first tick at/after the due moment, exactly once.
    for todo in todos:
        if todo.done or not (todo.due and todo.time and todo.block_id):
            continue
        try:
            due_dt = datetime.fromisoformat(f"{todo.due}T{todo.time}").replace(tzinfo=TZ)
        except ValueError:
            continue
        if due_dt <= now and not events.reminder_fired(todo.block_id):
            errors.ntfy(config.ntfy_url, config.ntfy_topic,
                        f"Due now: {todo.task}", title="Brain Cockpit — reminder")
            events.mark_reminder(todo.block_id)

    # 2. optional daily digest (config todos.digest = true): first tick at/after
    # 08:00 Kolkata each day. Overdue items persist in every digest until done —
    # nothing silently expires.
    digest_on = bool(((config.raw.get("todos") or {}).get("digest")))
    if not digest_on or now.hour < 8:
        return
    digest_key = f"digest-{now.date().isoformat()}"
    if events.reminder_fired(digest_key):
        return
    today = now.date()
    due_today = [t for t in todos if not t.done and in_range(t, "today", today)]
    overdue = [t for t in todos if in_range(t, "overdue", today)]
    if not due_today and not overdue:
        events.mark_reminder(digest_key)  # nothing to say today; don't re-check
        return
    lines = []
    if overdue:
        lines.append("Overdue:")
        lines += [f"• {t.task} (was {t.due})" for t in overdue]
    if due_today:
        lines.append("Today:")
        lines += [f"• {t.task}" + (f" at {t.time}" if t.time else "") for t in due_today]
    errors.ntfy(config.ntfy_url, config.ntfy_topic, "\n".join(lines),
                title="Brain Cockpit — today")
    events.mark_reminder(digest_key)
