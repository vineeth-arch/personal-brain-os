"""Event log: disposable SQLite (events/logs only, never note content — CLAUDE.md §1)
plus the human-readable vault artifacts (capture_log, PIPELINE-STATUS, heartbeat).

Every table here is disposable state (CLAUDE.md §1) — including `resurface`
(Pass R, B6): it only tracks which notes have been shown and when, so the
hybrid picker's cooldown works. Losing events.db just means every note
becomes eligible to resurface again; nothing durable is lost. Same for
`capture_keys` (idempotency) and `gmail_ingested` (Pass E, E4) — both are
disposable dedupe bookkeeping, never the note content itself."""
from __future__ import annotations

import sqlite3
from datetime import datetime, date
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    file TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,            -- ok | failed | needs_review
    duration_ms INTEGER,
    message TEXT,
    plain_english_error TEXT
);
CREATE TABLE IF NOT EXISTS reminders (
    key TEXT PRIMARY KEY,            -- todo block-id, or digest-<date>
    fired_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingested_files (
    src TEXT NOT NULL,               -- absolute path in a watched app folder
    mtime_ns INTEGER NOT NULL,       -- (src, mtime, size) identifies one recording
    size INTEGER NOT NULL,
    copied_at TEXT NOT NULL,
    PRIMARY KEY (src, mtime_ns, size)
);
CREATE TABLE IF NOT EXISTS resurface (
    note_id TEXT PRIMARY KEY,
    last_shown TEXT,                 -- ISO date, NULL until first shown
    shows INTEGER NOT NULL DEFAULT 0,
    response TEXT                    -- NULL | connected | acted | archived
);
CREATE TABLE IF NOT EXISTS capture_keys (
    key TEXT PRIMARY KEY,
    note_id TEXT NOT NULL,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gmail_ingested (
    message_id TEXT PRIMARY KEY,
    ingested_at TEXT NOT NULL
);
"""


class EventLog:
    def __init__(self, db_path: Path, vault_path: Path):
        self.db_path = Path(db_path)
        self.vault = Path(vault_path)
        # _System is created when something is actually written there, not on
        # open: the API constructs an EventLog per push request, and opening a
        # log should not reach into the vault at all.
        self._system = self.vault / "_System"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def log(self, file: str, stage: str, status: str, duration_ms: int | None = None,
            message: str = "", plain_english_error: str = "") -> None:
        self.conn.execute(
            "INSERT INTO events (timestamp, file, stage, status, duration_ms, message, "
            "plain_english_error) VALUES (?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), file, stage, status,
             duration_ms, message, plain_english_error),
        )
        self.conn.commit()

    def already_ingested(self, src: str, mtime_ns: int, size: int) -> bool:
        """Has this exact file (path + mtime + size) been copied in already?
        Pipeline bookkeeping only — the recording itself lives in the inbox and
        the vault, never here (CLAUDE.md §1)."""
        cur = self.conn.execute(
            "SELECT 1 FROM ingested_files WHERE src = ? AND mtime_ns = ? AND size = ?",
            (src, mtime_ns, size))
        return cur.fetchone() is not None

    def mark_ingested(self, src: str, mtime_ns: int, size: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO ingested_files (src, mtime_ns, size, copied_at) "
            "VALUES (?,?,?,?)",
            (src, mtime_ns, size, datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()

    def append_capture_log(self, line: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._system.mkdir(parents=True, exist_ok=True)
        with (self._system / "capture_log.md").open("a", encoding="utf-8") as f:
            f.write(f"- {stamp} — {line}\n")

    def reminder_fired(self, key: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM reminders WHERE key = ?", (key,))
        return cur.fetchone() is not None

    def mark_reminder(self, key: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO reminders (key, fired_at) VALUES (?, ?)",
            (key, datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()

    def capture_key_seen(self, key: str) -> str | None:
        """The note_id already captured under this idempotency key, or None
        if this key is new. A capture_keys row is disposable bookkeeping
        (CLAUDE.md §1) — losing events.db just means a retry after that
        point could (harmlessly) create a second, distinct note instead of
        being recognized as a repeat; no data is ever lost either way."""
        cur = self.conn.execute("SELECT note_id FROM capture_keys WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else None

    def mark_capture_key(self, key: str, note_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO capture_keys (key, note_id, created) VALUES (?, ?, ?)",
            (key, note_id, datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()

    def gmail_ingested(self, message_id: str) -> bool:
        """Has this Gmail message already been filed as a note? Disposable
        bookkeeping (CLAUDE.md §1) — losing events.db just means a message
        could be re-filed as a second note on the next pull; the vault note
        itself is never lost either way."""
        cur = self.conn.execute("SELECT 1 FROM gmail_ingested WHERE message_id = ?", (message_id,))
        return cur.fetchone() is not None

    def mark_gmail_ingested(self, message_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO gmail_ingested (message_id, ingested_at) VALUES (?, ?)",
            (message_id, datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()

    def heartbeat(self, path: Path) -> None:
        Path(path).write_text(datetime.now().isoformat(timespec="seconds") + "\n", encoding="utf-8")

    def _count(self, where: str, params: tuple = ()) -> int:
        cur = self.conn.execute(f"SELECT COUNT(*) FROM events WHERE {where}", params)
        return cur.fetchone()[0]

    def _captured_on(self, day: date) -> int:
        """Distinct files that reached the archive stage ok on that day."""
        cur = self.conn.execute(
            "SELECT COUNT(DISTINCT file) FROM events WHERE stage='archive' AND status='ok' "
            "AND timestamp LIKE ?", (day.isoformat() + "%",))
        return cur.fetchone()[0]

    def _drain_events_on(self, day: date) -> list[tuple[str, str]]:
        """(file, message) for every successful drain run logged on `day` —
        at most one per day given the once-daily reminder gate, but a manual
        retry could add more, so the caller sums defensively."""
        cur = self.conn.execute(
            "SELECT file, message FROM events WHERE stage='drain' AND status='ok' "
            "AND timestamp LIKE ?", (day.isoformat() + "%",))
        return cur.fetchall()

    def _failed_latest(self) -> list[tuple[str, str]]:
        """Files whose LATEST event is a failure, with the reason.

        It used to take the latest *failure* per file, which never forgot: a
        file that failed and was then retried successfully stayed on the list
        forever, so PIPELINE-STATUS.md and the 08:00 digest kept reporting
        failures that GET /api/status had already cleared. Same query as
        api/service.failed_items now, so every surface agrees."""
        cur = self.conn.execute(
            "SELECT e.file, e.message FROM events e "
            "JOIN (SELECT file, MAX(id) AS mid FROM events GROUP BY file) m "
            "ON e.id = m.mid WHERE e.status='failed' ORDER BY e.id DESC")
        return [(f, msg) for f, msg in cur.fetchall()]

    def digest_stats(self, day: date) -> dict:
        """The pipeline half of the morning digest: captures on `day`, plus
        the current needs-review and failed counts (same semantics as
        write_status / GET /api/status)."""
        return {
            "captured": self._captured_on(day),
            "needs_review": self._count("status='needs_review'"),
            "failed": len(self._failed_latest()),
        }

    def write_status(self, pending: int) -> None:
        processed_today = self._captured_on(date.today())
        needs_review = self._count("status='needs_review'")
        failed_rows = self._failed_latest()
        lines = [
            "# Pipeline Status",
            "",
            f"- Last run: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"- Pending in inbox: {pending}",
            f"- Processed today: {processed_today}",
            f"- Needs review: {needs_review}",
            f"- Failed: {len(failed_rows)}",
        ]
        if failed_rows:
            lines.append("")
            lines.append("## Failed files")
            for f, msg in failed_rows:
                lines.append(f"- `{Path(f).name}` — {msg}")
        self._system.mkdir(parents=True, exist_ok=True)
        (self._system / "PIPELINE-STATUS.md").write_text("\n".join(lines) + "\n",
                                                         encoding="utf-8")

    def close(self) -> None:
        self.conn.close()
