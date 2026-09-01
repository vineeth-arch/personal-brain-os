"""Todo lines: Obsidian Tasks-compatible checkboxes in 06-Todos/<date>.md.

Line format (markers only when known; block-id enables API round-trips):
    - [ ] task text (from [[20260703140000]]) 📅 2026-07-05 ⏰ 14:00 ^20260703140000-1

A todo may carry a micro-step breakdown (B10): 2-4 indented (4-space) child
checkboxes right below it, and a 🎚N (1-5) "how hard does it feel" marker
appended to the parent line:
    - [ ] plan the offsite (from [[20260901100000]]) 📅 2026-09-03 ^20260901100000-1 🎚4
        - [ ] book the room ^20260901100000-1a
        - [ ] draft the agenda ^20260901100000-1b
Children have no provenance/due/time markers of their own — those live on the
parent line only. Completing all children auto-completes the parent, and
reopening one un-marks it (see toggle()); toggling the parent directly never
cascades down to its children.

Completing a todo flips "- [ ]" to "- [x]" IN PLACE — lines are never deleted.
The reminder tick runs inside the watcher's --loop (no new process) and fires
each reminder exactly once via the reminders table in events.db. The user's
timezone is Asia/Kolkata for everything date-shaped.

A todo may recur (E2): a 🔁 daily/weekly marker right after the task text
(parent lines only, never children):
    - [ ] water plants 🔁 daily 📅 2026-09-01 ^id-1
Completing a recurring todo — directly, or via a child-rollup completion —
appends the next occurrence (due date advanced 1 or 7 days, block id suffixed
`r2`, `r3`, ...) to that day's file; the just-completed line is never touched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import errors, morning
from . import resurface as resurface_mod

TZ = ZoneInfo("Asia/Kolkata")
TODOS_FOLDER = "06-Todos"

_FROM_RE = re.compile(r"\s*\(from \[\[[\w-]+\]\]\)$")

_DRAIN_FILED_RE = re.compile(r"filed=(\d+)")

_LINE_RE = re.compile(
    r"^(?P<indent>    )?- \[(?P<done>[ x])\] (?P<task>.*?)"
    r"(?: 🔁 (?P<recur>daily|weekly))?"
    r"(?: 📅 (?P<due>\d{4}-\d{2}-\d{2}))?"
    r"(?: ⏰ (?P<time>\d{2}:\d{2}))?"
    r"(?: \^(?P<block>[\w-]+))?"
    r"(?: 🎚(?P<feel>\d))?$"
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
    feel: int | None = None            # 1-5 "how hard does it feel" dial (parent only)
    recur: str | None = None           # "daily" | "weekly" | None (E2, parent only)
    children: list["Todo"] = field(default_factory=list)  # micro-steps, one level deep


def format_line(task: str, note_id: str, index: int,
                due: str | None, time: str | None) -> str:
    parts = [f"- [ ] {task} (from [[{note_id}]])"]
    if due:
        parts.append(f"📅 {due}")
        if time:
            parts.append(f"⏰ {time}")
    parts.append(f"^{note_id}-{index}")
    return " ".join(parts)


def format_child_line(task: str, block_id: str) -> str:
    """A micro-step under a parent todo — no provenance, no due/time; those
    live on the parent line only (B10)."""
    return f"    - [ ] {task} ^{block_id}"


def parse_line(line: str) -> tuple[str, bool, str | None, str | None, str | None, bool, int | None, str | None] | None:
    m = _LINE_RE.match(line.rstrip())
    if not m:
        return None
    task = _FROM_RE.sub("", m["task"].strip())  # provenance stays in the file, not the UI
    feel = int(m["feel"]) if m["feel"] else None
    return (task, m["done"] == "x", m["due"], m["time"], m["block"], bool(m["indent"]), feel, m["recur"])


def scan(vault: Path) -> list[Todo]:
    todos_dir = Path(vault) / TODOS_FOLDER
    if not todos_dir.is_dir():
        return []
    out: list[Todo] = []
    for path in sorted(todos_dir.glob("*.md")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue        # one unreadable day must not empty the agenda
        i = 0
        while i < len(lines):
            parsed = parse_line(lines[i])
            if parsed is None or parsed[5]:  # not a line, or an orphaned child (no parent above it) — skip
                i += 1
                continue
            task, done, due, time, block, _indent, feel, recur = parsed
            children: list[Todo] = []
            j = i + 1
            while j < len(lines):
                child_parsed = parse_line(lines[j])
                if child_parsed is not None and child_parsed[5]:
                    c_task, c_done, c_due, c_time, c_block, _, _, _ = child_parsed
                    children.append(Todo(path, j, c_task, c_done, c_due, c_time, c_block))
                    j += 1
                else:
                    break
            out.append(Todo(path, i, task, done, due, time, block, feel=feel, recur=recur, children=children))
            i = j
    return out


def toggle(vault: Path, block_id: str) -> bool:
    """Flip the checkbox of the line carrying ^block_id — a top-level todo OR
    one of its children. Returns the new done state. Raises LookupError if no
    line carries that id. Toggling a child rolls up onto its parent (all
    children done → parent auto-marks done; reopening one un-marks it).
    Toggling a parent directly never cascades down to its children."""
    for todo in scan(vault):
        if todo.block_id == block_id:
            new_done = _flip_line(todo.file, todo.line_no, todo.done)
            if new_done and todo.recur:
                _spawn_next_occurrence(vault, todo)
            return new_done
        for child in todo.children:
            if child.block_id == block_id:
                new_done = _flip_line(child.file, child.line_no, child.done)
                parent_became_done = _rollup_parent(todo, child, new_done)
                if parent_became_done and todo.recur:
                    _spawn_next_occurrence(vault, todo)
                return new_done
    raise LookupError(block_id)


def _flip_line(file: Path, line_no: int, was_done: bool) -> bool:
    lines = file.read_text(encoding="utf-8").splitlines()
    line = lines[line_no]
    lines[line_no] = (line.replace("- [x]", "- [ ]", 1) if was_done
                      else line.replace("- [ ]", "- [x]", 1))
    file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return not was_done


def _rollup_parent(parent: Todo, flipped_child: Todo, flipped_child_new_done: bool) -> bool:
    """Returns True only when this call is what flipped the parent open→done
    (never on a no-op, never on the reverse reopen direction) — the signal
    toggle() needs to know whether to spawn a recurring parent's next
    occurrence."""
    all_done = all(
        flipped_child_new_done if c.line_no == flipped_child.line_no else c.done
        for c in parent.children
    )
    if all_done and not parent.done:
        _flip_line(parent.file, parent.line_no, was_done=False)
        return True
    elif not all_done and parent.done:
        _flip_line(parent.file, parent.line_no, was_done=True)
    return False


_RECUR_DAYS = {"daily": 1, "weekly": 7}


def _next_recur_block_id(vault: Path, block_id: str) -> str:
    """{block_id}r2, then r3, ... A respawned occurrence can land in ANY
    future day's file (not just tomorrow's — a weekly todo jumps 7 days), so
    the collision check has to look at the WHOLE vault's 06-Todos folder, not
    just one file."""
    existing = {t.block_id for t in scan(vault)}
    existing |= {c.block_id for t in scan(vault) for c in t.children}
    n = 2
    while f"{block_id}r{n}" in existing:
        n += 1
    return f"{block_id}r{n}"


def _spawn_next_occurrence(vault: Path, todo: Todo) -> None:
    """Append the next occurrence of a recurring todo, `_RECUR_DAYS[todo.recur]`
    days out. Never touches the just-completed line — todos.py's law, lines
    are never deleted or edited once written, only flipped in place."""
    if todo.recur not in _RECUR_DAYS or not todo.due:
        return
    try:
        next_due = date.fromisoformat(todo.due) + timedelta(days=_RECUR_DAYS[todo.recur])
    except ValueError:
        return
    next_block_id = _next_recur_block_id(vault, todo.block_id) if todo.block_id else None
    parts = [f"- [ ] {todo.task}", f"🔁 {todo.recur}", f"📅 {next_due.isoformat()}"]
    if todo.time:
        parts.append(f"⏰ {todo.time}")
    if next_block_id:
        parts.append(f"^{next_block_id}")
    line = " ".join(parts)

    todos_dir = Path(vault) / TODOS_FOLDER
    todos_dir.mkdir(parents=True, exist_ok=True)
    day = next_due.isoformat()
    target = todos_dir / f"{day}.md"
    with target.open("a", encoding="utf-8") as f:
        if target.stat().st_size == 0:
            f.write(f"# Todos — {day}\n\n")
        f.write(line + "\n")


def add_breakdown(vault: Path, block_id: str, feel: int, steps: list[str]) -> Todo:
    """Insert 2-4 child checkbox lines under the todo carrying ^block_id, and
    stamp the feel-dial marker on the parent line. Raises LookupError if the
    id is unknown, ValueError if it already has children or a feel marker
    (never overwrite an existing breakdown — the route this backs returns 409
    for that case)."""
    todos = scan(vault)
    parent = next((t for t in todos if t.block_id == block_id), None)
    if parent is None:
        raise LookupError(block_id)
    if parent.children:
        raise ValueError(f"{block_id} already has children")
    if parent.feel is not None:
        raise ValueError(f"{block_id} already has a feel marker")

    lines = parent.file.read_text(encoding="utf-8").splitlines()
    parent_line = lines[parent.line_no].rstrip() + f" 🎚{feel}"
    child_lines = [
        format_child_line(step, f"{block_id}{chr(ord('a') + i)}")
        for i, step in enumerate(steps)
    ]
    lines[parent.line_no:parent.line_no + 1] = [parent_line] + child_lines
    parent.file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return next(t for t in scan(vault) if t.block_id == block_id)


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


def _yesterday_drain_filed(events, day: date) -> int:
    """Total items the drain filed yesterday, for the morning digest's one
    quiet line — 0 when the drain didn't run or filed nothing."""
    total = 0
    for _file, message in events._drain_events_on(day):
        m = _DRAIN_FILED_RE.search(message or "")
        if m:
            total += int(m.group(1))
    return total


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
    drain_filed = _yesterday_drain_filed(events, today - timedelta(days=1))
    # lazy import: api/notes.py isn't a dependency of the pipeline package
    # under normal operation (only the FastAPI app imports it), and a
    # top-level import here would create a pipeline→api coupling nothing
    # else in this package has — same precedent as watcher.py::drain_tick.
    from api import notes as notes_mod
    triage_count = notes_mod.count_review(Path(config.vault_path))
    # the relationship half of the morning — folded into THIS push, never a second one
    people_lines = morning.people_section(config, today)
    people_lines += morning.push_section(config, events.db_path)
    if not due_today and not overdue and quiet_pipeline and not drain_filed and not triage_count \
            and not people_lines:
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
    if drain_filed:
        lines.append(f"{drain_filed} old items filed at best guess — one command undoes it.")
    if triage_count:
        lines.append(f"{triage_count} waiting in triage.")
    picked = resurface_mod.pick(Path(config.vault_path), events.db_path, k=1, now=now.date())
    if picked:
        lines.append(f"Resurfaced: {picked[0]['title']}")
    if overdue:
        lines.append("Overdue:")
        lines += [f"• {t.task} (was {t.due})" for t in overdue]
    if due_today:
        lines.append("Today:")
        lines += [f"• {t.task}" + (f" at {t.time}" if t.time else "") for t in due_today]
    lines += people_lines
    public_url = ((config.raw.get("deploy") or {}).get("public_url") or "").rstrip("/")
    click = f"{public_url}/#people" if public_url and people_lines else public_url
    errors.ntfy(config.ntfy_url, config.ntfy_topic, "\n".join(lines),
                title="Brain Cockpit — today", click=click)
    events.mark_reminder(digest_key)
