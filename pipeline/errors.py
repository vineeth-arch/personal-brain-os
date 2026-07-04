"""Plain-English error handling (CLAUDE.md §5): what happened / likely cause /
what to do. Quarantine the file, log the failure, send one ntfy push."""
from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path


class StageError(Exception):
    """A stage failure with the three user-facing parts.

    `transient` classifies the failure for the retry policy: True for things
    that fix themselves (network blips, rate limits, 5xx), False for things
    that don't (bad audio, missing binaries, bad keys) — permanent failures
    quarantine immediately, transient ones get retried first. `attempts` is
    stamped by the retry loop before the error escapes."""

    def __init__(self, what: str, cause: str, todo: str, transient: bool = False):
        self.what = what
        self.cause = cause
        self.todo = todo
        self.transient = transient
        self.attempts = 1
        super().__init__(f"{what} — {cause} — {todo}")

    def plain(self) -> str:
        return f"What happened: {self.what}\nLikely cause: {self.cause}\nWhat to do: {self.todo}"


def ntfy(url: str, topic: str, message: str, title: str = "Brain Cockpit") -> None:
    """Best-effort push. Never raises — a dead ntfy must not stop the pipeline."""
    if not url or not topic:
        return
    try:
        req = urllib.request.Request(
            f"{url.rstrip('/')}/{topic}", data=message.encode("utf-8"),
            headers={"Title": title, "Priority": "high"})
        urllib.request.urlopen(req, timeout=10).close()
    except Exception:
        pass  # ponytail: swallow push failures; the event log is the durable record


def quarantine(src: Path, failed_path: Path) -> Path:
    """Move a bad file to failed_path (never delete — CLAUDE.md source-of-truth safety)."""
    failed_path.mkdir(parents=True, exist_ok=True)
    dest = failed_path / src.name
    if dest.exists():
        dest = failed_path / f"{src.stem}-{src.stat().st_mtime_ns}{src.suffix}"
    shutil.move(str(src), str(dest))
    return dest
