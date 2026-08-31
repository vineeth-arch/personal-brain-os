"""Stdlib-only multipart/form-data parsing.

FastAPI's idiomatic `UploadFile`/`File(...)` needs the `python-multipart`
package at runtime — that is NOT in the locked dependency list (CLAUDE.md §7:
"ask before adding any dependency, no exceptions"). Rather than adding one for
a single endpoint, this hand-rolls just enough of RFC 7578 to pull named text
fields and a file field out of a raw request body — the same "stdlib over a
new dependency" choice this codebase already makes for OAuth, LLM calls, and
ntfy pushes (all plain `urllib`)."""
from __future__ import annotations

import re

_BOUNDARY_RE = re.compile(r'boundary="?([^";]+)"?', re.IGNORECASE)
_DISPOSITION_RE = re.compile(rb'Content-Disposition:\s*form-data;(.*)', re.IGNORECASE)
_NAME_RE = re.compile(r'name="([^"]*)"')
_FILENAME_RE = re.compile(r'filename="([^"]*)"')


class MultipartError(ValueError):
    """The body wasn't parseable multipart/form-data."""


def _boundary(content_type: str) -> bytes:
    m = _BOUNDARY_RE.search(content_type or "")
    if not m:
        raise MultipartError("no boundary in Content-Type")
    return b"--" + m.group(1).encode()


def parse(body: bytes, content_type: str) -> dict[str, dict]:
    """Returns {field_name: {"data": bytes, "filename": str | None,
    "content_type": str | None}}. Plain text fields have filename=None —
    decode `.data` for those. The client-supplied `filename` is parsed but
    deliberately never used to build a server path anywhere in this codebase
    (path-traversal-by-construction is avoided by not trusting it at all)."""
    boundary = _boundary(content_type)
    fields: dict[str, dict] = {}
    chunks = body.split(boundary)
    if len(chunks) < 2:
        raise MultipartError("boundary not found in body")
    for chunk in chunks[1:]:
        if chunk.startswith(b"--"):
            continue  # the closing boundary marker; nothing follows it
        chunk = chunk.lstrip(b"\r\n")
        if b"\r\n\r\n" not in chunk:
            continue
        headers_blob, content = chunk.split(b"\r\n\r\n", 1)
        if content.endswith(b"\r\n"):
            # that trailing CRLF is the delimiter before the next boundary,
            # not part of the field's actual bytes
            content = content[:-2]
        name = None
        filename = None
        part_ctype = None
        for header_line in headers_blob.split(b"\r\n"):
            m = _DISPOSITION_RE.match(header_line)
            if m:
                rest = m.group(1).decode("utf-8", errors="replace")
                nm = _NAME_RE.search(rest)
                fm = _FILENAME_RE.search(rest)
                name = nm.group(1) if nm else None
                filename = fm.group(1) if fm else None
            elif header_line.lower().startswith(b"content-type:"):
                part_ctype = header_line.split(b":", 1)[1].strip().decode("utf-8", errors="replace")
        if name:
            fields[name] = {"data": content, "filename": filename, "content_type": part_ctype}
    return fields
