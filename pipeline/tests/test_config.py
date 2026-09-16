"""pipeline/tests/test_config.py"""
import json
from pathlib import Path

from pipeline import config as config_module


def test_timezone_defaults_to_utc(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "vault_path": str(tmp_path / "vault"), "inbox_path": str(tmp_path / "inbox"),
        "archive_path": str(tmp_path / "archive"), "failed_path": str(tmp_path / "failed"),
    }))
    cfg = config_module.load(cfg_path)
    assert cfg.timezone == "UTC"
    assert cfg.tzinfo.key == "UTC"


def test_timezone_reads_from_config_json(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "vault_path": str(tmp_path / "vault"), "inbox_path": str(tmp_path / "inbox"),
        "archive_path": str(tmp_path / "archive"), "failed_path": str(tmp_path / "failed"),
        "timezone": "Asia/Kolkata",
    }))
    cfg = config_module.load(cfg_path)
    assert cfg.timezone == "Asia/Kolkata"
    assert cfg.tzinfo.key == "Asia/Kolkata"
