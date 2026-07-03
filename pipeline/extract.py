"""Stage 5 — extract action items and APPEND them to the daily todo note. The
source note body is never touched (SCHEMA §8 — this is an extra write).

Pass T upgrade: each item is {task, due_iso|null, remind}. Natural-language
dates ("tomorrow 2pm", "Friday") are resolved by the classification model
RELATIVE TO THE CAPTURE TIMESTAMP in Asia/Kolkata. Ambiguous → due null,
never guessed. No API key / model failure → the items still land, undated
(graceful degradation — capture is never lost to a decoration failure).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import llm, todos

# ponytail: cue-phrase heuristic as the cheap prefilter (and the no-LLM
# fallback). Sentence starts/contains one of these action cues.
_CUES = re.compile(
    r"\b(todo|to-do|need to|needs to|have to|remember to|don'?t forget|"
    r"follow up|action item|make sure|should|must)\b", re.IGNORECASE)

_DUE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DUE_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")


@dataclass
class TodoItem:
    task: str
    due_iso: str | None      # "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM", else None
    remind: bool


def find_action_items(transcript: str) -> list[str]:
    items = []
    for sentence in re.split(r"(?<=[.!?\n])\s+", transcript):
        s = sentence.strip(" -•\t")
        if s and _CUES.search(s):
            items.append(s.rstrip(".!"))
    return items


def resolve_prompt(candidates: list[str], captured: datetime) -> str:
    """The anchor the model resolves against: capture moment in Asia/Kolkata."""
    anchored = captured if captured.tzinfo else captured.replace(tzinfo=todos.TZ)
    local = anchored.astimezone(todos.TZ)
    stamp = f"{local.strftime('%A')} {local.strftime('%Y-%m-%d %H:%M')} (Asia/Kolkata)"
    bullet_list = "\n".join(f"- {c}" for c in candidates)
    return (
        "Extract the action items below and resolve any natural-language dates.\n"
        f"Captured at: {stamp}.\n"
        "Return ONLY a JSON array, one object per action item:\n"
        '{"task": string, "due": "YYYY-MM-DD" | "YYYY-MM-DDTHH:MM" | null, "remind": boolean}\n'
        "Rules: resolve dates RELATIVE to the capture moment above. 'tomorrow' = the "
        "next calendar day. A weekday name = the NEXT such weekday after capture. "
        "If the date or time is ambiguous or not stated, use null — NEVER guess. "
        "remind is true only when a specific clock time is stated.\n\n"
        f"Action item candidates:\n{bullet_list}"
    )


def _validate(raw: object, fallback: list[str]) -> list[TodoItem]:
    """Schema-check the model output; anything invalid degrades to undated."""
    if not isinstance(raw, list):
        return [TodoItem(t, None, False) for t in fallback]
    items: list[TodoItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        task = str(entry.get("task") or "").strip()
        if not task:
            continue
        due = entry.get("due")
        if not (isinstance(due, str) and (_DUE_DATE_RE.match(due) or _DUE_DATETIME_RE.match(due))):
            due = None
        items.append(TodoItem(task=task, due_iso=due, remind=bool(entry.get("remind")) and due is not None))
    return items or [TodoItem(t, None, False) for t in fallback]


def _router_complete(prompt: str, config) -> str | None:
    """Default resolver: the Pass B model router (same chain as classification).
    None when no provider serves — extraction degrades to undated items."""
    text, _provider, _attempts = llm.complete_text(prompt, config)
    return text


def resolve_items(candidates: list[str], captured: datetime, config,
                  llm_fn=None) -> list[TodoItem]:
    llm_fn = llm_fn or _router_complete
    text = llm_fn(resolve_prompt(candidates, captured), config)
    if not text:
        return [TodoItem(t, None, False) for t in candidates]
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return _validate(json.loads(text), candidates)
    except json.JSONDecodeError:
        return [TodoItem(t, None, False) for t in candidates]


def extract(transcript: str, note_id: str, captured, config, llm_fn=None) -> list[TodoItem]:
    """Find action items, resolve dates, append Obsidian Tasks-compatible lines
    to 06-Todos/<capture-date>.md. Returns the written items."""
    candidates = find_action_items(transcript)
    if not candidates:
        return []
    items = resolve_items(candidates, captured, config, llm_fn)

    day = captured.strftime("%Y-%m-%d")
    todos_dir = Path(config.vault_path) / todos.TODOS_FOLDER
    todos_dir.mkdir(parents=True, exist_ok=True)
    todo_file = todos_dir / f"{day}.md"
    with todo_file.open("a") as f:
        if todo_file.stat().st_size == 0:
            f.write(f"# Todos — {day}\n\n")
        for i, item in enumerate(items, start=1):
            due = time = None
            if item.due_iso:
                due, _, hhmm = item.due_iso.partition("T")
                time = hhmm if (hhmm and item.remind) else None
            f.write(todos.format_line(item.task, note_id, i, due, time) + "\n")
    return items
