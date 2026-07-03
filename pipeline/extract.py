"""Stage 5 — extract action items and APPEND them to the daily todo note. The
source note body is never touched (SCHEMA §8 — this is an extra write)."""
from __future__ import annotations

import re
from pathlib import Path

# ponytail: cue-phrase heuristic, no LLM. Upgrade to a Haiku extractor if recall
# matters. Sentence starts/contains one of these action cues.
_CUES = re.compile(
    r"\b(todo|to-do|need to|needs to|have to|remember to|don'?t forget|"
    r"follow up|action item|make sure|should|must)\b", re.IGNORECASE)


def find_action_items(transcript: str) -> list[str]:
    items = []
    for sentence in re.split(r"(?<=[.!?\n])\s+", transcript):
        s = sentence.strip(" -•\t")
        if s and _CUES.search(s):
            items.append(s.rstrip(".!"))
    return items


def extract(transcript: str, note_id: str, captured, vault_path: Path) -> list[str]:
    items = find_action_items(transcript)
    if not items:
        return []
    day = captured.strftime("%Y-%m-%d")
    todos_dir = Path(vault_path) / "06-Todos"
    todos_dir.mkdir(parents=True, exist_ok=True)
    todo_file = todos_dir / f"{day}.md"
    with todo_file.open("a") as f:
        if todo_file.stat().st_size == 0:
            f.write(f"# Todos — {day}\n\n")
        for it in items:
            f.write(f"- [ ] {it} (from [[{note_id}]])\n")
    return items
