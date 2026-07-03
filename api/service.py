"""events.db reads. Fresh read-only connection per call — never EventLog (its
connection is bound to the creating thread; API handlers run in a threadpool),
and never create the db as a side effect."""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger("api")

_EVENT_COLS = "id, timestamp, file, stage, status, duration_ms, message, plain_english_error"


def _rows(db_path: Path, sql: str, params: tuple = ()) -> list:
    if not Path(db_path).exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        log.exception("events.db read failed")
        return []


def processed_today(db_path: Path) -> int:
    # local date — events.py stamps datetime.now(), never compare against UTC
    rows = _rows(
        db_path,
        "SELECT COUNT(DISTINCT file) FROM events "
        "WHERE stage='archive' AND status='ok' AND timestamp LIKE ?",
        (date.today().isoformat() + "%",),
    )
    return rows[0][0] if rows else 0


def failed_items(db_path: Path) -> list[dict]:
    """Latest event per file where that latest event is a failure — files that
    were retried and later succeeded drop out naturally."""
    rows = _rows(
        db_path,
        "SELECT e.id, e.file, e.timestamp, e.plain_english_error, e.message "
        "FROM events e JOIN (SELECT file, MAX(id) AS mid FROM events GROUP BY file) m "
        "ON e.id = m.mid WHERE e.status='failed' ORDER BY e.id DESC",
    )
    return [
        {
            "id": row[0],
            "file": Path(row[1]).name,
            "timestamp": row[2],
            "error": parse_plain_error(row[3] or row[4] or ""),
        }
        for row in rows
    ]


def failed_row(db_path: Path, event_id: int) -> tuple[str, str] | None:
    """(original file path, status) for a specific event row."""
    rows = _rows(db_path, "SELECT file, status FROM events WHERE id = ?", (event_id,))
    return (rows[0][0], rows[0][1]) if rows else None


def events_list(db_path: Path, status: str | None, limit: int, before_id: int | None) -> list[dict]:
    sql = f"SELECT {_EVENT_COLS} FROM events"
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if before_id is not None:
        where.append("id < ?")
        params.append(before_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    cols = [c.strip() for c in _EVENT_COLS.split(",")]
    return [dict(zip(cols, row)) for row in _rows(db_path, sql, tuple(params))]


def streak(db_path: Path) -> dict:
    """Consecutive captured days ending today — but a not-yet-captured today is
    skipped (the streak ends yesterday), never zeroed: a 9am glance before the
    first capture must not read '0-day streak'."""
    captured_days = {
        row[0]
        for row in _rows(
            db_path,
            "SELECT DISTINCT substr(timestamp, 1, 10) FROM events "
            "WHERE stage='archive' AND status='ok'",
        )
    }
    today = date.today()
    days = []
    for i in range(29, -1, -1):
        d = today - timedelta(days=i)
        days.append({"date": d.isoformat(), "captured": d.isoformat() in captured_days})

    current = 0
    cursor = today
    if today.isoformat() not in captured_days:
        cursor = today - timedelta(days=1)  # today isn't over — don't punish it
    while cursor.isoformat() in captured_days:
        current += 1
        cursor -= timedelta(days=1)
    return {"current": current, "days": days}


def parse_plain_error(plain: str) -> dict:
    """Parse a StageError.plain() string back into the three parts. Unparseable
    text lands whole in `what` with generic cause/todo — never a partial envelope."""
    what = cause = todo = ""
    for line in plain.splitlines():
        low = line.strip()
        if low.startswith("What happened:"):
            what = low.removeprefix("What happened:").strip()
        elif low.startswith("Likely cause:"):
            cause = low.removeprefix("Likely cause:").strip()
        elif low.startswith("What to do:"):
            todo = low.removeprefix("What to do:").strip()
    if not (what and cause and todo):
        return {
            "what": plain.strip() or "This file failed to process.",
            "cause": cause or "The failure didn't include a structured reason.",
            "todo": todo or "Check the event log's technical detail, fix the cause, then retry.",
        }
    return {"what": what, "cause": cause, "todo": todo}
