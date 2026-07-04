"""Watchdog (Pass 5): the API notices when the watcher loop has gone quiet and
sends ONE ntfy push per 6-hour window — the watcher can't report its own death.

Stale = a heartbeat file exists (the loop ran at some point) but is older than
30 minutes. No heartbeat at all means the loop was never started on this
machine — that's a setup state the watcher card and Build screen already
surface, not an alarm.

Fire-once uses the same reminders-table pattern as todo reminders and the
morning digest (INSERT OR IGNORE on a window-bucketed key), via a short-lived
WRITABLE connection — the API's read paths (service.py) stay read-only."""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from pipeline import errors

log = logging.getLogger("api")

STALE_MINUTES = 30
WINDOW_HOURS = 6
CHECK_SECONDS = 300

MESSAGE = ("The watcher looks stopped. Likely cause: machine asleep or process "
           "crashed. Fix: restart the loop.")

_REMINDERS_DDL = """
CREATE TABLE IF NOT EXISTS reminders (
    key TEXT PRIMARY KEY,
    fired_at TEXT NOT NULL
);
"""


def _heartbeat_age_minutes(heartbeat_path: Path, now: datetime) -> float | None:
    """None = no heartbeat file (loop never ran here). Unparseable = treat as
    very stale — a corrupt heartbeat is not a live watcher."""
    if not heartbeat_path.exists():
        return None
    try:
        stamp = datetime.fromisoformat(heartbeat_path.read_text().strip())
    except ValueError:
        return float("inf")
    return (now - stamp).total_seconds() / 60


def _won_window(db_path: Path, key: str, now: datetime) -> bool:
    """Atomically claim the fire-once key. INSERT OR IGNORE means exactly one
    caller wins per window, even across API restarts."""
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.executescript(_REMINDERS_DDL)  # a fresh events.db has no tables yet
        cur = conn.execute(
            "INSERT OR IGNORE INTO reminders (key, fired_at) VALUES (?, ?)",
            (key, now.isoformat(timespec="seconds")))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def maybe_alert(db_path: Path, heartbeat_path: Path, config,
                now: datetime | None = None, push=None) -> bool:
    """One check: push (once per 6h window) if the heartbeat is stale.
    Returns True when a push was sent. `now` and `push` are test seams."""
    now = now or datetime.now()
    age = _heartbeat_age_minutes(heartbeat_path, now)
    if age is None or age <= STALE_MINUTES:
        return False
    window_key = f"watchdog-{now.date().isoformat()}-{now.hour // WINDOW_HOURS}"
    if not _won_window(db_path, window_key, now):
        return False
    (push or errors.ntfy)(config.ntfy_url, config.ntfy_topic, MESSAGE,
                          title="Brain Cockpit — watcher")
    log.warning("watchdog: heartbeat stale (%.0f min) — sent the once-per-window push", age)
    return True


def start(db_path: Path, heartbeat_path: Path, load_config,
          interval: int = CHECK_SECONDS) -> threading.Thread:
    """Background checker for the API process. Sleeps FIRST (short-lived test
    servers never tick), reloads config each round (edits apply live), and
    never lets an exception escape — a broken check must not hurt the API."""

    def _loop():
        while True:
            time.sleep(interval)
            try:
                maybe_alert(db_path, heartbeat_path, load_config())
            except Exception:
                log.exception("watchdog check failed")

    thread = threading.Thread(target=_loop, name="watchdog", daemon=True)
    thread.start()
    return thread
