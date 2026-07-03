"""Config loading. API keys come from the environment ONLY (CLAUDE.md §7)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    vault_path: Path
    inbox_path: Path
    archive_path: Path
    failed_path: Path
    engine: str = "whispercpp"          # whispercpp | openai
    whispercpp_binary: str = ""
    whispercpp_model: str = ""
    ntfy_url: str = ""
    ntfy_topic: str = ""
    confidence_threshold: float = 0.7
    raw: dict = field(default_factory=dict)

    # Keys resolved from env, never from config.json.
    @property
    def anthropic_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY")

    @property
    def openai_key(self) -> str | None:
        return os.environ.get("OPENAI_API_KEY")


def load(path: str | Path = "config.json") -> Config:
    data = json.loads(Path(path).read_text())
    t = data.get("transcription", {})
    w = t.get("whispercpp", {})
    n = data.get("ntfy", {})
    c = data.get("classification", {})
    return Config(
        vault_path=Path(data["vault_path"]).expanduser(),
        inbox_path=Path(data["inbox_path"]).expanduser(),
        archive_path=Path(data["archive_path"]).expanduser(),
        failed_path=Path(data["failed_path"]).expanduser(),
        engine=t.get("engine", "whispercpp"),
        whispercpp_binary=w.get("binary_path", ""),
        whispercpp_model=w.get("model_path", ""),
        ntfy_url=n.get("url", ""),
        ntfy_topic=n.get("topic", ""),
        confidence_threshold=float(c.get("confidence_threshold", 0.7)),
        raw=data,
    )
