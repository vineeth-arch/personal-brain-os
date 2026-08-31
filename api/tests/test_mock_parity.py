"""D16: web/mock-api.py hand-mirrors api/notes.AUDIO_MIME_EXT (the mock server
can't import the real package — it runs standalone with no dependencies). This
is a tripwire so the two sets can't silently drift apart."""
from __future__ import annotations

import ast
from pathlib import Path

from api import notes

MOCK_PATH = Path(__file__).resolve().parents[2] / "web" / "mock-api.py"


def _extract_audio_mime_types(source: str) -> set[str]:
    """Parse the `AUDIO_MIME_TYPES = {...}` literal out of mock-api.py without
    importing it (the mock is a standalone stdlib script, not a package)."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "AUDIO_MIME_TYPES" for t in node.targets
        ):
            return set(ast.literal_eval(node.value))
    raise AssertionError("AUDIO_MIME_TYPES not found in mock-api.py")


def test_mock_audio_mime_types_match_the_real_allow_list():
    mock_types = _extract_audio_mime_types(MOCK_PATH.read_text())
    real_types = set(notes.AUDIO_MIME_EXT)
    assert mock_types == real_types, (
        f"web/mock-api.py's AUDIO_MIME_TYPES has drifted from api.notes.AUDIO_MIME_EXT — "
        f"only in mock: {mock_types - real_types}; only in real: {real_types - mock_types}")
