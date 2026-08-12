"""Todo lines: Obsidian Tasks-compatible checkboxes in 06-Todos/<date>.md.

Line format (markers only when known; block-id enables API round-trips):
    - [ ] task text (from [[20260703140000]]) 📅 2026-07-05 ⏰ 14:00 🔁 every week ^20260703140000-1

Completing a todo flips "- [ ]" to "- [x]" IN PLACE — lines are never deleted.
A 🔁 line additionally spawns its next occurrence when it's completed, as a new
line with a fresh block id and the date advanced; the completed one stays put
as the record that it happened. The marker syntax matches the Obsidian Tasks
plugin, so someone ticking the box in Obsidian gets the same behaviour from the
plugin — the two agree rather than fighting. (Hand-editing the checkbox in a
plain editor, with no plugin, won't spawn anything; that's the same class of
drift as hand-deleting a line, and the vault stays readable either way.)

The reminder tick runs inside the watcher's --loop (no new process) and fires
each reminder exactly once via the reminders table in events.db. The user's
timezone is Asia/Kolkata for everything date-shaped.
"""
from __future__ import annotations

import calendar
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
    r"(?: 🔁 (?P<recur>every [^\^\n]+?))?"
    r"(?: \^(?P<block>[\w-]+))?$"
)

# "every day" | "every week" | "every month" | "every N days"
_RECUR_RE = re.compile(r"^every (?:(?P<n>\d+) )?(?P<unit>day|days|week|weeks|month|months)$",
                       re.IGNORECASE)
# a spawned occurrence is <base>-r<N>; the base is the original block id
_OCCURRENCE_RE = re.compile(r"^(?P<base>.+?)-r(?P<seq>\d+)$")


@dataclass
class Recurrence:
    n: int
    unit: str      # day | week | month


@dataclass
class Todo:
    file: Path
    line_no: int
    task: str
    done: bool
    due: str | None        # YYYY-MM-DD
    time: str | None       # HH:MM — presence means "remind me at this time"
    block_id: str | None
    recurrence: str | None = None   # the raw rule text, e.g. "every week"


@dataclass
class ToggleResult:
    done: bool
    spawned_block_id: str | None = None
    spawned_due: str | None = None


def parse_recurrence(text: str | None) -> Recurrence | None:
    """A rule this module knows how to advance, or None. Anything else — a
    hand-typed "every other Tuesday" — leaves the line perfectly valid and
    simply doesn't repeat, rather than breaking the todo or guessing a date."""
    if not text:
        return None
    m = _RECUR_RE.match(text.strip())
    if not m:
        return None
    unit = m["unit"].lower().rstrip("s")
    n = int(m["n"]) if m["n"] else 1
    return Recurrence(n=n, unit=unit) if n > 0 else None


def next_due(due: date, rule: Recurrence) -> date:
    """The next occurrence after `due`. Month steps clamp to the end of the
    target month, so a task due the 31st doesn't skip February entirely."""
    if rule.unit == "day":
        return due + timedelta(days=rule.n)
    if rule.unit == "week":
        return due + timedelta(weeks=rule.n)
    month_index = due.month - 1 + rule.n
    year = due.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(due.day, calendar.monthrange(year, month)[1]))


def format_line(task: str, note_id: str, index: int,
                due: str | None, time: str | None, recurrence: str | None = None) -> str:
    parts = [f"- [ ] {task} (from [[{note_id}]])"]
    if due:
        parts.append(f"📅 {due}")
        if time:
            parts.append(f"⏰ {time}")
    if recurrence:
        parts.append(f"🔁 {recurrence}")
    parts.append(f"^{note_id}-{index}")
    return " ".join(parts)


def parse_line(line: str):
    m = _LINE_RE.match(line.rstrip())
    if not m:
        return None
    task = _FROM_RE.sub("", m["task"].strip())  # provenance stays in the file, not the UI
    return (task, m["done"] == "x", m["due"], m["time"], m["block"], m["recur"])


def scan(vault: Path) -> list[Todo]:
    todos_dir = Path(vault) / TODOS_FOLDER
    if not todos_dir.is_dir():
        return []
    out: list[Todo] = []
    for path in sorted(todos_dir.glob("*.md")):
        for i, line in enumerate(path.read_text().splitlines()):
            parsed = parse_line(line)
            if parsed:
                task, done, due, time, block, recur = parsed
                out.append(Todo(path, i, task, done, due, time, block, recur))
    return out


def _lineage_base(block_id: str) -> str:
    m = _OCCURRENCE_RE.match(block_id)
    return m["base"] if m else block_id


def _next_occurrence_id(block_id: str, existing: list[Todo]) -> str:
    """<base>-r<N>, one past the highest occurrence already in the vault, so a
    lineage never reuses an id (each occurrence gets its own ⏰ reminder)."""
    base = _lineage_base(block_id)
    highest = 0
    for todo in existing:
        if not todo.block_id:
            continue
        m = _OCCURRENCE_RE.match(todo.block_id)
        if m and m["base"] == base:
            highest = max(highest, int(m["seq"]))
    return f"{base}-r{highest + 1}"


def _spawn_line(line: str, new_due: date, new_block: str) -> str:
    """The completed line, copied forward: unchecked, date advanced, new id.
    String surgery rather than a rebuild so the ⏰ time, the 🔁 rule and the
    (from [[…]]) provenance all carry over exactly as the user wrote them."""
    spawned = line.replace("- [x]", "- [ ]", 1)
    spawned = re.sub(r"📅 \d{4}-\d{2}-\d{2}", f"📅 {new_due.isoformat()}", spawned, count=1)
    return re.sub(r"\^[\w-]+$", f"^{new_block}", spawned.rstrip(), count=1)


def _append_to_daily(vault: Path, day: date, line: str) -> None:
    todos_dir = Path(vault) / TODOS_FOLDER
    todos_dir.mkdir(parents=True, exist_ok=True)
    path = todos_dir / f"{day.isoformat()}.md"
    with path.open("a") as f:
        if path.stat().st_size == 0:
            f.write(f"# Todos — {day.isoformat()}\n\n")
        f.write(line + "\n")


def toggle(vault: Path, block_id: str) -> ToggleResult:
    """Flip the checkbox of the line carrying ^block_id, and — when completing a
    recurring todo — write its next occurrence. Raises LookupError if no line
    carries that id.

    Reopening never deletes the occurrence it spawned: lines are never deleted
    here, and a duplicate guard means completing it again won't spawn a second
    copy of the same date."""
    todos = scan(vault)
    for todo in todos:
        if todo.block_id != block_id:
            continue
        lines = todo.file.read_text().splitlines()
        line = lines[todo.line_no]
        completing = not todo.done
        lines[todo.line_no] = (line.replace("- [ ]", "- [x]", 1) if completing
                               else line.replace("- [x]", "- [ ]", 1))
        todo.file.write_text("\n".join(lines) + "\n")

        result = ToggleResult(done=completing)
        rule = parse_recurrence(todo.recurrence)
        if not (completing and rule and todo.due):
            return result
        try:
            nd = next_due(date.fromisoformat(todo.due), rule)
        except ValueError:
            return result
        base = _lineage_base(block_id)
        already = any(t.block_id and _lineage_base(t.block_id) == base
                      and t.due == nd.isoformat() for t in todos)
        if already:
            return result
        new_block = _next_occurrence_id(block_id, todos)
        _append_to_daily(vault, nd, _spawn_line(lines[todo.line_no], nd, new_block))
        result.spawned_block_id = new_block
        result.spawned_due = nd.isoformat()
        return result
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

    # 2. optional UNIFIED morning digest (config todos.digest = true): first
    # tick at/after 08:00 Kolkata each day. ONE push merges the todo agenda
    # with yesterday's pipeline summary — never several notifications.
    # Overdue items persist in every digest until done — nothing silently
    # expires.
    digest_on = bool(((config.raw.get("todos") or {}).get("digest")))
    if not digest_on or now.hour < 8:
        return
    digest_key = f"digest-{now.date().isoformat()}"
    if events.reminder_fired(digest_key):
        return
    today = now.date()
    due_today = [t for t in todos if not t.done and in_range(t, "today", today)]
    overdue = [t for t in todos if in_range(t, "overdue", today)]
    stats = events.digest_stats(today - timedelta(days=1))
    quiet_pipeline = not (stats["captured"] or stats["needs_review"] or stats["failed"])
    if not due_today and not overdue and quiet_pipeline:
        events.mark_reminder(digest_key)  # nothing to say today; don't re-check
        return
    lines = []
    if not quiet_pipeline:
        summary = [f"{stats['captured']} captured yesterday"]
        if stats["needs_review"]:
            summary.append(f"{stats['needs_review']} waiting for review")
        if stats["failed"]:
            summary.append(f"{stats['failed']} failed")
        lines.append(" · ".join(summary))
    if overdue:
        lines.append("Overdue:")
        lines += [f"• {t.task} (was {t.due})" for t in overdue]
    if due_today:
        lines.append("Today:")
        lines += [f"• {t.task}" + (f" at {t.time}" if t.time else "") for t in due_today]
    errors.ntfy(config.ntfy_url, config.ntfy_topic, "\n".join(lines),
                title="Brain Cockpit — today")
    events.mark_reminder(digest_key)
