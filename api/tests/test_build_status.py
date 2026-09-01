"""binary_runs probe (Pass 10): the literal-binary shape used by the
deployment milestones, and its graceful off-platform degradation — a machine
without launchctl must show 'not done' with a plain detail, never an error.

Pass X adds the probe types the newer milestones need, and pins the whole
manifest: reality IS the checklist, so an item nobody can probe is a lie on
the Build screen."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from api import build_status
from api.build_status import (_PROBES, _probe_binary_runs, _probe_config_field_contains,
                              _probe_file_contains, _probe_file_exists, _probe_url_ok,
                              _probe_vault_sync_configured, _probe_whisper_model_present)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_literal_binary_and_args(tmp_path):
    fake = tmp_path / "fake-launchctl"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(fake, 0o755)
    ok, detail = _probe_binary_runs(tmp_path, {"binary": str(fake), "args": ["list", "x"]},
                                    None, None)
    assert ok is True and detail == "The binary runs."


def test_literal_binary_nonzero_exit(tmp_path):
    fake = tmp_path / "fake-launchctl"
    fake.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
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
    fake = tmp_path / "whisper-cli"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(fake, 0o755)
    config = SimpleNamespace(raw={"transcription": {"whispercpp": {"binary_path": str(fake)}}})
    ok, detail = _probe_binary_runs(
        tmp_path, {"config_field": "transcription.whispercpp.binary_path"}, config, None)
    assert ok is True


# ---- Pass X probe types ----------------------------------------------------------

def test_file_exists_accepts_several_paths(tmp_path):
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    ok, detail = _probe_file_exists(tmp_path, {"paths": ["a.py", "b.py"]}, None, None)
    assert ok is False and "b.py" in detail and "a.py" not in detail.split("isn't")[0]

    (tmp_path / "b.py").write_text("", encoding="utf-8")
    ok, _ = _probe_file_exists(tmp_path, {"paths": ["a.py", "b.py"]}, None, None)
    assert ok is True


def test_file_exists_can_anchor_to_the_vault(tmp_path):
    vault = tmp_path / "vault"
    (vault / "_System").mkdir(parents=True)
    config = SimpleNamespace(raw={}, vault_path=vault)
    item = {"in": "vault", "path": "_System/my-voice.md"}

    ok, _ = _probe_file_exists(tmp_path, item, config, None)
    assert ok is False, "the repo has no _System/my-voice.md — the vault is what's asked about"

    (vault / "_System" / "my-voice.md").write_text("hey!", encoding="utf-8")
    ok, _ = _probe_file_exists(tmp_path, item, config, None)
    assert ok is True
    # and with no config at all it reports, it never raises
    assert _probe_file_exists(tmp_path, item, None, None) == (
        False, "config.json doesn't exist yet.")


def test_file_contains_distinguishes_shipped_from_merely_present(tmp_path):
    """The point of this probe: Today.tsx existed long before the mic did."""
    target = tmp_path / "Today.tsx"
    target.write_text("export function Today() { return null; }", encoding="utf-8")
    item = {"path": "Today.tsx", "needle": "MediaRecorder"}
    ok, detail = _probe_file_contains(tmp_path, item, None, None)
    assert ok is False and "doesn't have it yet" in detail

    target.write_text("const rec = new MediaRecorder(stream);", encoding="utf-8")
    ok, _ = _probe_file_contains(tmp_path, item, None, None)
    assert ok is True

    target.unlink()
    ok, detail = _probe_file_contains(tmp_path, item, None, None)
    assert ok is False and "isn't there yet" in detail


def test_config_field_contains_reads_the_granted_google_scopes(tmp_path):
    item = {"field": "google.scopes", "needle": "auth/contacts"}
    gmail_only = SimpleNamespace(raw={"google": {
        "scopes": "https://www.googleapis.com/auth/gmail.readonly"}})
    assert _probe_config_field_contains(tmp_path, item, gmail_only, None)[0] is False

    with_contacts = SimpleNamespace(raw={"google": {
        "scopes": "https://www.googleapis.com/auth/gmail.readonly "
                  "https://www.googleapis.com/auth/contacts"}})
    assert _probe_config_field_contains(tmp_path, item, with_contacts, None)[0] is True

    # a link that predates the scope has no google.scopes key at all
    assert _probe_config_field_contains(tmp_path, item, SimpleNamespace(raw={}), None)[0] is False
    assert _probe_config_field_contains(tmp_path, item, None, None)[0] is False


# ---- the manifest itself ---------------------------------------------------------

def test_every_manifest_item_has_a_probe_that_exists():
    """An item whose probe type is unknown renders as a permanently-unfinished
    milestone with no way to ever tick — worse than not listing it."""
    manifest = json.loads((REPO_ROOT / "checks.json").read_text(encoding="utf-8"))
    unknown = [i["id"] for i in manifest["items"] if i["type"] not in _PROBES]
    assert not unknown, f"checks.json uses probe types nothing implements: {unknown}"


def test_every_manifest_item_carries_the_fields_its_probe_needs():
    manifest = json.loads((REPO_ROOT / "checks.json").read_text(encoding="utf-8"))
    required = {
        "file_exists": lambda i: i.get("path") or i.get("paths"),
        "file_contains": lambda i: i.get("path") and i.get("needle"),
        "config_field_set": lambda i: i.get("field"),
        "config_field_contains": lambda i: i.get("field") and i.get("needle"),
        "url_ok": lambda i: True,
        "binary_runs": lambda i: i.get("binary") or i.get("config_field"),
        "env_var_set": lambda i: i.get("name") or i.get("any_of"),
        "git_log_contains": lambda i: i.get("needle"),
        "vault_query": lambda i: i.get("query"),
        "endpoint_ok": lambda i: True,
        "vault_sync_configured": lambda i: True,
        "whisper_model_present": lambda i: True,
    }
    for item in manifest["items"]:
        assert required[item["type"]](item), f"{item['id']} is missing a field its probe reads"
        # every unfinished item must be able to tell the owner what to do next
        assert item.get("next_action"), f"{item['id']} has no next_action"
        assert len(item["next_action"]) > 20, f"{item['id']}'s next_action says too little"


# ---- Pass H probe types -----------------------------------------------------

def test_vault_sync_configured_via_env(monkeypatch):
    monkeypatch.setenv("VAULT_GIT_REMOTE", "https://github.com/me/vault.git")
    monkeypatch.delenv("VAULT_GIT_BRANCH", raising=False)
    config = SimpleNamespace(raw={})
    ok, detail = _probe_vault_sync_configured(REPO_ROOT, {}, config, None)
    assert ok is True and "configured" in detail.lower()


def test_vault_sync_configured_via_config_json(monkeypatch):
    monkeypatch.delenv("VAULT_GIT_REMOTE", raising=False)
    config = SimpleNamespace(raw={"vault_sync": {"remote": "https://github.com/me/vault.git"}})
    ok, _detail = _probe_vault_sync_configured(REPO_ROOT, {}, config, None)
    assert ok is True


def test_vault_sync_not_configured(monkeypatch):
    monkeypatch.delenv("VAULT_GIT_REMOTE", raising=False)
    config = SimpleNamespace(raw={})
    ok, detail = _probe_vault_sync_configured(REPO_ROOT, {}, config, None)
    assert ok is False and "neither" in detail.lower()


def test_vault_sync_no_config_at_all():
    ok, detail = _probe_vault_sync_configured(REPO_ROOT, {}, None, None)
    assert ok is False and "config.json doesn't exist" in detail


def test_whisper_model_present(tmp_path):
    model = tmp_path / "ggml-small.en.bin"
    model.write_bytes(b"fake model")
    config = SimpleNamespace(whispercpp_model=str(model))
    ok, detail = _probe_whisper_model_present(REPO_ROOT, {}, config, None)
    assert ok is True and "is present" in detail


def test_whisper_model_missing(tmp_path):
    config = SimpleNamespace(whispercpp_model=str(tmp_path / "not-here.bin"))
    ok, detail = _probe_whisper_model_present(REPO_ROOT, {}, config, None)
    assert ok is False and "isn't on this machine" in detail


def test_whisper_model_path_unset():
    config = SimpleNamespace(whispercpp_model="")
    ok, detail = _probe_whisper_model_present(REPO_ROOT, {}, config, None)
    assert ok is False and "empty" in detail.lower()


# ---- Task I3: url_ok --------------------------------------------------------

class _FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_url_ok_success(monkeypatch):
    monkeypatch.setattr(build_status.urllib.request, "urlopen",
                        lambda url, timeout=None: _FakeResponse(200))
    config = SimpleNamespace(raw={"deploy": {"public_url": "https://cockpit.example.com"}})
    ok, detail = _probe_url_ok(REPO_ROOT, {}, config, None)
    assert ok is True and "answered" in detail
    assert "cockpit.example.com/api/health" in detail


def test_url_ok_connection_failure(monkeypatch):
    def boom(url, timeout=None):
        raise OSError("connection refused")
    monkeypatch.setattr(build_status.urllib.request, "urlopen", boom)
    config = SimpleNamespace(raw={"deploy": {"public_url": "https://cockpit.example.com"}})
    ok, detail = _probe_url_ok(REPO_ROOT, {}, config, None)
    assert ok is False and "didn't answer" in detail


def test_url_ok_non_2xx(monkeypatch):
    monkeypatch.setattr(build_status.urllib.request, "urlopen",
                        lambda url, timeout=None: _FakeResponse(503))
    config = SimpleNamespace(raw={"deploy": {"public_url": "https://cockpit.example.com"}})
    ok, detail = _probe_url_ok(REPO_ROOT, {}, config, None)
    assert ok is False and "status 503" in detail


def test_url_ok_no_public_url_set_makes_no_network_call(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("url_ok must not open a connection with no public_url set")
    monkeypatch.setattr(build_status.urllib.request, "urlopen", boom)
    ok, detail = _probe_url_ok(REPO_ROOT, {}, SimpleNamespace(raw={}), None)
    assert ok is False and "deploy.public_url isn't set" in detail


def test_url_ok_no_config_at_all():
    ok, detail = _probe_url_ok(REPO_ROOT, {}, None, None)
    assert ok is False and "config.json doesn't exist" in detail


def test_the_shipped_passes_actually_probe_true():
    """These milestones describe code in this repo, so they must tick here."""
    manifest = json.loads((REPO_ROOT / "checks.json").read_text(encoding="utf-8"))
    by_id = {i["id"]: i for i in manifest["items"]}
    for pass_id in ("passV", "passP", "passMW", "passD", "passX", "pass12", "passS", "passV2", "passH"):
        item = by_id[pass_id]
        ok, detail = _PROBES[item["type"]](REPO_ROOT, item, None, None)
        assert ok is True, f"{pass_id} should be done in this repo but probed false: {detail}"
