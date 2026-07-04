"""Watchdog (Pass 5): stale heartbeat → ONE push per 6h window, fire-once via
the reminders table. All clock-driven, so every test injects `now` and a push
recorder — no sleeping, no network."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from api import watchdog

NOW = datetime(2026, 7, 4, 9, 0, 0)  # 09:00 → window "2026-07-04-1" (6h buckets)

CONFIG = SimpleNamespace(ntfy_url="https://ntfy.example", ntfy_topic="t")


def _heartbeat(tmp_path, age: timedelta):
    hb = tmp_path / ".watcher-heartbeat"
    hb.write_text((NOW - age).isoformat(timespec="seconds") + "\n")
    return hb


def test_no_alert_when_heartbeat_fresh(tmp_path):
    hb = _heartbeat(tmp_path, timedelta(minutes=5))
    pushes = []
    fired = watchdog.maybe_alert(tmp_path / "events.db", hb, CONFIG, now=NOW,
                                 push=lambda *a, **k: pushes.append(a))
    assert fired is False and pushes == []


def test_no_alert_when_loop_never_ran(tmp_path):
    pushes = []
    fired = watchdog.maybe_alert(tmp_path / "events.db", tmp_path / "nope", CONFIG, now=NOW,
                                 push=lambda *a, **k: pushes.append(a))
    assert fired is False and pushes == []


def test_alert_fires_once_per_6h_window(tmp_path):
    """The rate limit, with a fake clock: repeated checks inside one window
    push exactly once; the next window pushes again."""
    hb = _heartbeat(tmp_path, timedelta(hours=2))
    db = tmp_path / "events.db"
    pushes = []
    push = lambda url, topic, message, title: pushes.append(message)

    assert watchdog.maybe_alert(db, hb, CONFIG, now=NOW, push=push) is True
    # still stale 10 and 90 minutes later — same window, no second push
    assert watchdog.maybe_alert(db, hb, CONFIG, now=NOW + timedelta(minutes=10), push=push) is False
    assert watchdog.maybe_alert(db, hb, CONFIG, now=NOW + timedelta(minutes=90), push=push) is False
    assert len(pushes) == 1
    assert pushes[0] == ("The watcher looks stopped. Likely cause: machine asleep "
                         "or process crashed. Fix: restart the loop.")
    # 6 hours on → new window → one more push
    assert watchdog.maybe_alert(db, hb, CONFIG, now=NOW + timedelta(hours=6), push=push) is True
    assert len(pushes) == 2


def test_alert_works_on_fresh_db(tmp_path):
    """The API may check before the pipeline ever created events.db — the
    watchdog creates the reminders table itself."""
    hb = _heartbeat(tmp_path, timedelta(hours=1))
    db = tmp_path / "brand-new.db"
    pushes = []
    assert watchdog.maybe_alert(db, hb, CONFIG, now=NOW,
                                push=lambda *a, **k: pushes.append(a)) is True
    assert db.exists() and len(pushes) == 1


def test_unreadable_heartbeat_counts_as_stale(tmp_path):
    hb = tmp_path / ".watcher-heartbeat"
    hb.write_text("not a timestamp\n")
    pushes = []
    assert watchdog.maybe_alert(tmp_path / "events.db", hb, CONFIG, now=NOW,
                                push=lambda *a, **k: pushes.append(a)) is True
