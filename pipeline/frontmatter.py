"""A real frontmatter parser (D6) — scalars, inline lists/maps, and block
lists, round-trip preserving. Every existing writer in this repo
(api/notes.py, pipeline/route.py, pipeline/relationships.py) uses its own
narrower reader; this one exists because C1's union merge rule (merge.py) turns
single-valued fields like `channels` into list/map-valued ones, and the
column-0 `key: value` scanners those modules use can't represent that.
Locked deps (CLAUDE.md §7) rule out PyYAML — this is a small, deliberately
narrow parser for exactly the shapes this vault's notes use, not a general
YAML implementation."""
from __future__ import annotations

import re


class BlockList(list):
    """A list value parsed from YAML block-list syntax (`key:\\n  - item`),
    as opposed to inline `[a, b]` syntax. serialize() uses this marker to
    preserve the source shape — an untouched note must round-trip with its
    original formatting, never silently reformatted (D6's core guarantee).
    isinstance(x, list) is still True for a BlockList, so every existing
    caller that checks `isinstance(value, list)` keeps working unchanged."""


ALWAYS_LIST = frozenset({"categories", "subjects", "tags"})

_FM_DELIM = "---\n"
_BLOCK_ITEM_RE = re.compile(r"^  - (.*)$")


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def _quote_if_needed(value: str) -> str:
    if ": " in value or value.startswith(("[", "{", "'", '"')) or value.strip() != value:
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _parse_inline_list(raw: str) -> list[str]:
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return []
    return [_unquote(p) for p in inner.split(",")]


def _parse_inline_map(raw: str) -> dict[str, str]:
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return {}
    out: dict[str, str] = {}
    for part in inner.split(","):
        key, _, value = part.partition(":")
        key, value = _unquote(key), _unquote(value)
        if key:
            out[key] = value
    return out


def parse(text: str) -> tuple[dict, str]:
    """(frontmatter, body). A missing/malformed frontmatter block returns
    ({}, text) unchanged — same "never lose the note" posture as every other
    reader in this repo."""
    if not text.startswith(_FM_DELIM):
        return {}, text
    parts = text.split(_FM_DELIM, 2)
    if len(parts) < 3:
        return {}, text
    raw_fm, body = parts[1], parts[2]

    fm: dict = {}
    lines = raw_fm.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.startswith((" ", "\t")):
            i += 1
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if not key:
            i += 1
            continue
        if rest.startswith("[") and rest.endswith("]"):
            fm[key] = _parse_inline_list(rest)
        elif rest.startswith("{") and rest.endswith("}"):
            fm[key] = _parse_inline_map(rest)
        elif rest == "" and i + 1 < len(lines) and _BLOCK_ITEM_RE.match(lines[i + 1]):
            items = []
            i += 1
            while i < len(lines) and _BLOCK_ITEM_RE.match(lines[i]):
                items.append(_unquote(_BLOCK_ITEM_RE.match(lines[i]).group(1)))
                i += 1
            fm[key] = BlockList(items)
            continue
        elif rest == "" and i + 1 < len(lines) and lines[i + 1].startswith((" ", "\t")) \
                and lines[i + 1].strip():
            # blank value followed by SOME other indented content this
            # parser doesn't understand (e.g. a block-style nested map) —
            # fail closed rather than silently drop it and let a caller
            # re-serialize an empty key over real content
            return {}, text
        else:
            fm[key] = _unquote(rest)
        i += 1
    return fm, body


def serialize(frontmatter: dict, body: str) -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            is_block = isinstance(value, BlockList)
            if not is_block and key not in ALWAYS_LIST and len(value) == 1:
                lines.append(f"{key}: {_quote_if_needed(value[0])}")
            elif is_block:
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {_quote_if_needed(item)}")
            else:
                inner = ", ".join(_quote_if_needed(v) for v in value)
                lines.append(f"{key}: [{inner}]")
        elif isinstance(value, dict):
            inner = ", ".join(f"{k}: {v}" for k, v in value.items())
            lines.append(f"{key}: {{{inner}}}")
        else:
            lines.append(f"{key}: {_quote_if_needed(str(value))}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body
