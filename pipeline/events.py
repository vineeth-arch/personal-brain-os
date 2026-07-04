"""Event log: disposable SQLite (events/logs only, never note content — CLAUDE.md §1)
plus the human-readable vault artifacts (capture_log, PIPELINE-STATUS, heartbeat)."""
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
"""


class EventLog:
    def __init__(self, db_path: Path, vault_path: Path):
        self.db_path = Path(db_path)
        self.vault = Path(vault_path)
        self._system = self.vault / "_System"
        self._system.mkdir(parents=True, exist_ok=True)
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

    def append_capture_log(self, line: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with (self._system / "capture_log.md").open("a") as f:
            f.write(f"- {stamp} — {line}\n")

    def reminder_fired(self, key: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM reminders WHERE key = ?", (key,))
        return cur.fetchone() is not None

    def mark_reminder(self, key: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO reminders (key, fired_at) VALUES (?, ?)",
            (key, datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()

    def heartbeat(self, path: Path) -> None:
        Path(path).write_text(datetime.now().isoformat(timespec="seconds") + "\n")

    def _count(self, where: str, params: tuple = ()) -> int:
        cur = self.conn.execute(f"SELECT COUNT(*) FROM events WHERE {where}", params)
        return cur.fetchone()[0]

    def _captured_on(self, day: date) -> int:
        """Distinct files that reached the archive stage ok on that day."""
        cur = self.conn.execute(
            "SELECT COUNT(DISTINCT file) FROM events WHERE stage='archive' AND status='ok' "
            "AND timestamp LIKE ?", (day.isoformat() + "%",))
        return cur.fetchone()[0]

    def _failed_latest(self) -> list[tuple[str, str]]:
        """Latest failure per file with its reason."""
        cur = self.conn.execute(
            "SELECT file, message FROM events WHERE status='failed' ORDER BY id DESC")
        seen, failed_rows = set(), []
        for f, msg in cur.fetchall():
            if f not in seen:
                seen.add(f)
                failed_rows.append((f, msg))
        return failed_rows

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
        (self._system / "PIPELINE-STATUS.md").write_text("\n".join(lines) + "\n")

    def close(self) -> None:
        self.conn.close()
