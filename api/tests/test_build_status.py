"""binary_runs probe (Pass 10): the literal-binary shape used by the
deployment milestones, and its graceful off-platform degradation — a machine
without launchctl must show 'not done' with a plain detail, never an error."""
from __future__ import annotations

import os

from api.build_status import _probe_binary_runs


def test_literal_binary_and_args(tmp_path):
    fake = tmp_path / "fake-launchctl"
    fake.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(fake, 0o755)
    ok, detail = _probe_binary_runs(tmp_path, {"binary": str(fake), "args": ["list", "x"]},
                                    None, None)
    assert ok is True and detail == "The binary runs."


def test_literal_binary_nonzero_exit(tmp_path):
    fake = tmp_path / "fake-launchctl"
    fake.write_text("#!/bin/sh\nexit 3\n")
    os.chmod(fake, 0o755)
    ok, detail = _probe_binary_runs(tmp_path, {"binary": str(fake), "args": ["list", "x"]},
                                    None, None)
    assert ok is False and "exited with code 3" in detail


def test_missing_binary_degrades_gracefully(tmp_path):
    # launchctl on Linux, essentially — the probe reports, it never raises
    ok, detail = _probe_binary_runs(
        tmp_path, {"binary": "definitely-not-a-real-binary-xyz", "args": ["list"]}, None, None)
    assert ok is False
    assert "doesn't exist on this machine" in detail


def test_config_field_shape_still_works(tmp_path):
    from types import SimpleNamespace
    fake = tmp_path / "whisper-cli"
    fake.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(fake, 0o755)
    config = SimpleNamespace(raw={"transcription": {"whispercpp": {"binary_path": str(fake)}}})
    ok, detail = _probe_binary_runs(
        tmp_path, {"config_field": "transcription.whispercpp.binary_path"}, config, None)
    assert ok is True
