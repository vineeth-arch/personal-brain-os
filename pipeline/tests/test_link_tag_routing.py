"""D13: a capture tag wins over automatic link-detection. Hermetic — no
network, no LLM; process_file is called directly against a temp vault."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pipeline import watcher


class _Item:
    def __init__(self, path: Path, tag: str | None = None):
        self.path = path
        self.kind = "link"          # what intake.poll would set: body carries a URL
        self.captured = datetime(2026, 8, 30, 9, 0, 0)
        self.name = path.stem
        self.tag = tag
        self.source = "manual"


class _Config:
    def __init__(self, tmp: Path):
        self.vault_path = tmp / "vault"
        self.inbox_path = tmp / "inbox"
        self.archive_path = tmp / "archive"
        self.failed_path = tmp / "failed"
        self.ntfy_url = self.ntfy_topic = ""
        self.raw = {}


class _Events:
    def __init__(self):
        self.rows = []

    def log(self, *a, **kw):
        self.rows.append((a, kw))

    def append_capture_log(self, line):
        pass


def _write(config: _Config, name: str, text: str) -> Path:
    for d in (config.inbox_path, config.vault_path, config.archive_path):
        d.mkdir(parents=True, exist_ok=True)
    p = config.inbox_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_a_tagged_capture_with_a_url_is_not_hijacked_into_a_resource(tmp_path):
    config = _Config(tmp_path)
    text = "#journal today was good, here's the article https://example.com/piece"
    src = _write(config, "a.txt", text)
    item = _Item(src, tag="journal")
    res = watcher.process_file(item, config, _Events(), watcher.Deps(transcriber=None))
    assert res.status == "ok"
    journal_files = list((config.vault_path / "01-Journal").glob("*.md"))
    assert len(journal_files) == 1, "the #journal tag should have won, filing it as a journal note"
    note = journal_files[0].read_text(encoding="utf-8")
    assert "type: journal" in note
    assert "https://example.com/piece" in note   # the URL is not thrown away
    assert not (config.vault_path / "04-Resources").exists()


def test_an_untagged_url_still_becomes_a_resource(tmp_path):
    config = _Config(tmp_path)
    text = "found this https://example.com/piece worth reading"
    src = _write(config, "b.txt", text)
    item = _Item(src, tag=None)
    res = watcher.process_file(item, config, _Events(), watcher.Deps(transcriber=None))
    assert res.status == "ok"
    resource_files = list((config.vault_path / "04-Resources").glob("*.md"))
    assert len(resource_files) == 1


def test_an_explicit_resource_tag_on_a_url_still_becomes_a_resource(tmp_path):
    config = _Config(tmp_path)
    text = "#resource found this https://example.com/piece worth reading"
    src = _write(config, "c.txt", text)
    item = _Item(src, tag="resource")
    res = watcher.process_file(item, config, _Events(), watcher.Deps(transcriber=None))
    assert res.status == "ok"
    resource_files = list((config.vault_path / "04-Resources").glob("*.md"))
    assert len(resource_files) == 1
