"""Task I3: backup_config's rotation. A `.bak-<timestamp>` copy is pure
disposable safety margin ahead of every config.json write — nothing in the
app ever reads one back, so the only behaviors worth pinning are "at most 5
survive" and "the newest 5 survive", plus the no-config-yet no-op."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from api.integrations import backup_config


def test_rotation_keeps_only_the_newest_five(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    for i in range(7):
        backup_config(config_path, now=datetime(2026, 9, 1, 12, 0, i))

    backups = sorted(p.name for p in tmp_path.glob("config.json.bak-*"))
    assert len(backups) == 5
    expected = sorted(
        f"config.json.bak-{datetime(2026, 9, 1, 12, 0, i).strftime('%Y%m%d%H%M%S')}"
        for i in range(2, 7)
    )
    assert backups == expected


def test_missing_config_is_a_noop(tmp_path):
    config_path = tmp_path / "config.json"
    assert not config_path.exists()
    backup_config(config_path)
    assert list(tmp_path.glob("config.json.bak-*")) == []
    assert not config_path.exists()
