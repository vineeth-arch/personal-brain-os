"""The seeds shipped in pipeline/seeds/ are what a Docker image (no docs/ at
runtime) has to fall back to — they must match the repo docs copy byte for
byte, or a rebuild silently drifts from the spec (P8)."""
from __future__ import annotations

from pathlib import Path

SEEDS = Path(__file__).resolve().parents[1] / "seeds"
DOCS = Path(__file__).resolve().parents[2] / "docs" / "relationship-os"


def test_greene_seed_matches_docs_copy():
    seed = (SEEDS / "greene-helper.md").read_text(encoding="utf-8")
    docs = (DOCS / "GREENE-HELPER.md").read_text(encoding="utf-8")
    assert seed == docs
