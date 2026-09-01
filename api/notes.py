"""Vault note operations: review queue, approve/retype, capture, resurface.

Reads and writes follow SCHEMA-REFERENCE.md exactly — flat frontmatter as
written by pipeline/route.py, immutable id, locked vocabularies imported from
the pipeline (never re-declared here). Every vault write is followed by a git
commit with a descriptive message (CLAUDE.md §3 — revertible AI-era changes).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

from pipeline import classify, relationships, route
from pipeline import resurface as resurface_mod
from pipeline.enrich import insight_text as _insight_text
from pipeline.events import EventLog

log = logging.getLogger("api")

_CONFIDENCE_RE = re.compile(r"confidence=(\d+(?:\.\d+)?)")
_EVIDENCE_RE = re.compile(r'evidence="([^"]*)"')
_RELATED_TITLE_RE = re.compile(r'related_title="([^"]*)"')
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")

EXCERPT_CHARS = 300


# ---- frontmatter ------------------------------------------------------------

def read_note(path: Path) -> str | None:
    """A note's text, or None when it can't be read.

    The vault is user-managed: a stray binary, a half-synced file, or a note
    saved in another encoding must cost that ONE note, not the whole screen.
    Every listing below skips what it cannot read rather than 500-ing."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        log.warning("skipping unreadable note %s", path)
        return None


def _unquote(value: str) -> str:
    """Strip the surrounding quotes a YAML scalar may carry.

    route._scalar quotes anything that would otherwise be read as structure, and
    people hand-quote values in Obsidian too, so the reader has to give back the
    string rather than the quoting."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\") if value[0] == '"' else inner
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Flat top-level keys only (list values are indented and skipped), matching
    the format route.build_frontmatter writes."""
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    fm: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            fm[k.strip()] = _unquote(v.strip())
    return fm, parts[2]


def _restamp(text: str, new_type: str, new_status: str) -> str:
    """Rewrite only the column-0 type:/status: lines inside the frontmatter block."""
    head, sep, body = text.partition("\n---\n")
    lines = []
    for line in head.splitlines():
        if line.startswith("type:"):
            lines.append(f"type: {new_type}")
        elif line.startswith("status:"):
            lines.append(f"status: {new_status}")
        else:
            lines.append(line)
    return "\n".join(lines) + sep + body


# ---- git --------------------------------------------------------------------

def git_commit_vault(vault: Path, message: str) -> bool:
    """Commit the vault after an API write. A git hiccup never fails the request
    (the watcher's never-abort precedent) — the write itself succeeded.
    Returns True when a commit was actually made (the backup endpoint reports
    this truthfully)."""
    try:
        inside = subprocess.run(
            ["git", "-C", str(vault), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True)
        if inside.returncode != 0:
            log.info("vault is not a git repo — skipping commit (%s)", message)
            return False
        subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(vault), "commit", "-m", message, "--allow-empty"],
                       check=True, capture_output=True)
        return True
    except Exception:
        log.exception("vault git commit failed (%s)", message)
        return False


# ---- review queue -----------------------------------------------------------

def _classify_map(db_path: Path) -> dict[str, tuple[float, str | None]]:
    """note filename → (classifier confidence, evidence), joined from events.db.

    Route events log 'wrote <name>.md' (basenames, ', '-joined); the nearest
    preceding classify event on the same source file carries
    'type=X confidence=0.62 by=llm evidence="..."' (evidence optional).
    Later route events win.
    """
    if not db_path.exists():
        return {}
    out: dict[str, tuple[float, str | None]] = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT r.message,"
                " (SELECT c.message FROM events c"
                "  WHERE c.file = r.file AND c.stage = 'classify' AND c.id < r.id"
                "  ORDER BY c.id DESC LIMIT 1)"
                " FROM events r WHERE r.stage = 'route' AND r.status = 'ok' ORDER BY r.id"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        log.exception("confidence join failed")
        return {}
    for route_msg, classify_msg in rows:
        if not route_msg or not classify_msg:
            continue
        m = _CONFIDENCE_RE.search(classify_msg)
        if not m:
            continue
        conf = float(m.group(1))
        ev = _EVIDENCE_RE.search(classify_msg)
        evidence = ev.group(1) if ev else None
        for name in route_msg.removeprefix("wrote ").split(", "):
            out[name.strip()] = (conf, evidence)
    return out


def _related_map(db_path: Path) -> dict[str, str | None]:
    """note filename → related note's title, joined from events.db, same
    join shape as _classify_map but against stage='related'. A row exists
    for every processed file (watcher logs 'related=none' explicitly), so a
    missing dict entry means 'never processed', not 'no related note'.

    Unlike _classify_map's classify event (logged BEFORE route), the related
    event is logged AFTER route — so the subquery here looks FORWARD
    (`rel.id > r.id`, nearest first via `ORDER BY rel.id ASC`), not backward.
    Getting this direction backwards would silently return the wrong related
    event (or none) instead of erroring."""
    if not db_path.exists():
        return {}
    out: dict[str, str | None] = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT r.message,"
                " (SELECT rel.message FROM events rel"
                "  WHERE rel.file = r.file AND rel.stage = 'related' AND rel.id > r.id"
                "  ORDER BY rel.id ASC LIMIT 1)"
                " FROM events r WHERE r.stage = 'route' AND r.status = 'ok' ORDER BY r.id"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        log.exception("related join failed")
        return {}
    for route_msg, related_msg in rows:
        if not route_msg:
            continue
        title = None
        if related_msg:
            m = _RELATED_TITLE_RE.search(related_msg)
            title = m.group(1) if m else None
        for name in route_msg.removeprefix("wrote ").split(", "):
            out[name.strip()] = title
    return out


def _confidence_map(db_path: Path) -> dict[str, float]:
    """Backward-compatible wrapper over _classify_map — confidence only."""
    return {name: conf for name, (conf, _evidence) in _classify_map(db_path).items()}


def _suggested_attendees_map(db_path: Path) -> dict[str, dict[str, str]]:
    """note filename -> {speaker label: person id} — the same join
    _confidence_map does, over the 'attendees' stage instead of 'classify'.
    Never written to frontmatter (SCHEMA-REFERENCE.md §7); this is the only
    way a suggestion ever reaches the API, exactly like classify confidence."""
    if not db_path.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT r.message,"
                " (SELECT a.message FROM events a"
                "  WHERE a.file = r.file AND a.stage = 'attendees' AND a.id < r.id"
                "  ORDER BY a.id DESC LIMIT 1)"
                " FROM events r WHERE r.stage = 'route' AND r.status = 'ok' ORDER BY r.id"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        log.exception("attendees join failed")
        return {}
    for route_msg, attendees_msg in rows:
        if not route_msg or not attendees_msg:
            continue
        try:
            suggested = json.loads(attendees_msg).get("suggested") or {}
        except json.JSONDecodeError:
            continue
        for name in route_msg.removeprefix("wrote ").split(", "):
            out[name.strip()] = suggested
    return out


def list_review(vault: Path, db_path: Path) -> list[dict]:
    inbox_dir = vault / route.INBOX_FOLDER
    if not inbox_dir.is_dir():
        return []
    classifications = _classify_map(db_path)
    related_titles = _related_map(db_path)
    suggestions = _suggested_attendees_map(db_path)
    people_by_id = {p.id: p.name for p in relationships.load_people(vault)}
    items = []
    for path in sorted(inbox_dir.glob("*.md")):
        text = read_note(path)
        if text is None:
            continue
        fm, body = parse_frontmatter(text)
        if fm.get("status") != "needs-review":
            continue
        note_type = fm.get("type", "musing")
        # always present ([] when there is nothing to suggest) so the client
        # never has to special-case a missing key
        attendees = [
            {"id": pid, "label": label, "name": people_by_id.get(pid, label)}
            for label, pid in suggestions.get(path.name, {}).items()
        ] if note_type == "conversation" else []
        confidence, evidence = classifications.get(path.name, (0.5, None))
        items.append({
            "id": fm.get("id", ""),
            "file": f"{route.INBOX_FOLDER}/{path.name}",
            "title": _DATE_PREFIX_RE.sub("", path.stem),
            "excerpt": body.strip()[:EXCERPT_CHARS],
            "suggested_type": note_type,
            "confidence": confidence,
            "created": fm.get("created", ""),
            "suggested_attendees": attendees,
            "evidence": evidence,
            "related_title": related_titles.get(path.name),
        })
    return items


def count_review(vault: Path) -> int:
    """The Today badge must equal the triage queue length — same scan."""
    inbox_dir = vault / route.INBOX_FOLDER
    if not inbox_dir.is_dir():
        return 0
    n = 0
    for path in inbox_dir.glob("*.md"):
        text = read_note(path)
        if text is None:
            continue
        fm, _ = parse_frontmatter(text)
        if fm.get("status") == "needs-review":
            n += 1
    return n


def _move_note(vault: Path, source: Path, text: str, new_type: str) -> Path:
    """Restamp type/status and atomically relocate a note into its type's
    folder — the identical mkstemp→replace→unlink dance `approve()` and
    `drain_review()` both need. Raises OSError if the source copy can't be
    removed after a successful write (never leave a note duplicated under
    one immutable id — SCHEMA §1 — every link pointing at it would break)."""
    new_status = route.STATUS_INITIAL.get(new_type, "active")
    dest_dir = vault / route.TYPE_FOLDER[new_type]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source.name
    i = 1
    while dest.exists():
        i += 1
        dest = dest_dir / f"{source.stem}-{i}{source.suffix}"

    # Write to a temp file in the destination folder and rename it into place,
    # then remove the source copy. If the unlink fails the note would exist
    # twice under one immutable id (SCHEMA §1), which breaks every link
    # pointing at it — so that failure is surfaced, not swallowed.
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".move-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_restamp(text, new_type, new_status))
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    try:
        source.unlink()
    except OSError:
        dest.unlink(missing_ok=True)   # roll back rather than duplicate the id
        log.exception("could not remove the source copy of %s — move rolled back", source)
        raise
    return dest


def approve(vault: Path, note_id: str, new_type: str,
           attendees: list[str] | None = None, *,
           events: EventLog | None = None) -> str:
    """Restamp type/status and move the note to its folder. Returns the
    vault-relative destination. Raises LookupError if the id isn't in review.

    `attendees` — confirmed 07-People ids, ignored unless new_type is
    "conversation" — is the ONLY way `attendees:` is ever filled in: the
    pipeline always writes it empty (SCHEMA-REFERENCE.md §7). Confirming here
    is what appends the dated interaction-log line to each person, via the
    same relationships.log_contact the People screen uses (CLAUDE.md §3 — no
    AI bulk-write reaches a person note unreviewed). An id that doesn't
    resolve to a real person is skipped rather than failing the whole
    approve — a stale suggestion should not block filing the note.

    `events` — optional; when given, logs an "approve" event recording what
    the classifier suggested vs. what the human chose (accuracy tracking,
    B3). Omitted by callers that don't care about the event log."""
    inbox_dir = vault / route.INBOX_FOLDER
    target: Path | None = None
    found_fm: dict = {}
    text = ""
    if inbox_dir.is_dir():
        for path in inbox_dir.glob("*.md"):
            candidate = read_note(path)
            if candidate is None:
                continue
            text = candidate
            fm, _ = parse_frontmatter(text)
            if fm.get("id") == note_id and fm.get("status") == "needs-review":
                target = path
                found_fm = fm
                break
    if target is None:
        raise LookupError(note_id)
    suggested = found_fm.get("type") or "none"

    dest = _move_note(vault, target, text, new_type)

    # Confirming attendees happens only AFTER the note is safely filed — had
    # the move above failed, no person note should have been touched at all.
    confirmed_names: list[str] = []
    if attendees and new_type == "conversation":
        confirmed_ids: list[str] = []
        title = _DATE_PREFIX_RE.sub("", dest.stem)
        for person_id in attendees:
            person = relationships.find_person(vault, person_id)
            if person is None:
                continue          # a stale/unknown suggestion — skip it
            note_line = f"Conversation: {title} ([[{note_id}]])"
            person.path.write_text(
                relationships.log_contact(person, note_line, date.today()), encoding="utf-8")
            confirmed_names.append(person.name)
            confirmed_ids.append(person_id)
        if confirmed_ids:
            fm_block, sep, body = dest.read_text(encoding="utf-8").partition("\n---\n")
            dest.write_text(
                route.stamp_list_field(fm_block, "attendees", confirmed_ids) + sep + body,
                encoding="utf-8")

    if events is not None:
        events.log(str(target), "approve", "ok",
                   message=f"id={note_id} suggested={suggested} chosen={new_type}")

    commit_msg = f"api: filed {note_id} as {new_type}"
    if confirmed_names:
        commit_msg += f" · confirmed attendees: {', '.join(confirmed_names)}"
    git_commit_vault(vault, commit_msg)
    return str(dest.relative_to(vault))


# ---- anti-guilt drain (Pass A, B5) ------------------------------------------

def drain_review(vault: Path, db_path: Path, *, older_than_days: int = 14,
                 floor: float = 0.5, now: date | None = None) -> dict:
    """Anti-guilt drain (Pass A, B5): triage items older than `older_than_days`
    are resolved without waiting on a human — filed at the classifier's best
    guess when it was confident enough, parked out of the queue otherwise.
    Every note this touches keeps its full content; nothing is deleted, and
    the whole run is ONE git commit so it's undone with one `git revert`.
    `origin: ai` + `drained: true` mark exactly which notes this filed, so
    that provenance (CLAUDE.md §1/§2) is never ambiguous later.

    Floor check: a filename with NO classify event at all (e.g. a tag-routed
    capture that skipped the LLM entirely) is treated as PARKED, never filed
    at a default confidence — only a filename that DOES have a classify
    event, at/above `floor`, is eligible to be filed.

    Conversations are ALWAYS parked, on top of that floor check, never filed
    — checked verbatim against pipeline/watcher.py rather than assumed: its
    `is_conversation` branch routes the type deterministically and logs
    `stage='attendees'` for the suggestion, but it ALSO still logs an
    unconditional `stage='classify'` event afterwards (confidence 1.0,
    `by=plaud`) purely so GET /api/review can display a confidence number —
    so a conversation is NOT actually "missing" from `_classify_map` the way
    a tag-routed capture is, and the floor check alone would wrongly treat
    its 1.0 confidence as "confident enough" and file it. Hence the explicit
    `note_type != "conversation"` guard below: a conversation's TYPE is never
    in doubt, only its attendees are, and auto-filing it would either skip
    attendee confirmation entirely or invent one — both wrong under CLAUDE.md
    §3 (no AI bulk-write reaches a person note unreviewed). Parking a stale
    unconfirmed conversation, so a human can still confirm attendees by hand,
    is the correct, constitution-consistent behavior."""
    now = now or date.today()
    inbox_dir = vault / route.INBOX_FOLDER
    if not inbox_dir.is_dir():
        return {"filed": 0, "parked": 0}
    classified = _classify_map(db_path)  # {filename: (confidence, evidence)}
    filed = parked = 0
    for path in sorted(inbox_dir.glob("*.md")):
        text = read_note(path)
        if text is None:
            continue
        fm, _ = parse_frontmatter(text)
        if fm.get("status") != "needs-review":
            continue
        created_str = fm.get("created", "")
        try:
            created = date.fromisoformat(created_str)
        except ValueError:
            continue  # unparseable date — never touched by an automated sweep
        if (now - created).days < older_than_days:
            continue
        note_type = fm.get("type", "musing")
        confident_enough = path.name in classified and classified[path.name][0] >= floor
        # a conversation's type is never in doubt, only its attendees are —
        # never auto-file one, whatever its (always 1.0, by=plaud) confidence
        # reads as; see the docstring above for why this can't be left to the
        # floor check alone
        if confident_enough and note_type in route.TYPE_FOLDER and note_type != "conversation":
            dest = _move_note(vault, path, text, note_type)
            fm_block, sep, body = dest.read_text(encoding="utf-8").partition("\n---\n")
            fm_block = route.stamp_field(fm_block, "origin", "ai")
            fm_block = route.stamp_field(fm_block, "drained", "true")
            dest.write_text(fm_block + sep + body, encoding="utf-8")
            filed += 1
        else:
            parked_dir = inbox_dir / "parked"
            parked_dir.mkdir(exist_ok=True)
            dest = parked_dir / path.name
            i = 1
            while dest.exists():
                i += 1
                dest = parked_dir / f"{path.stem}-{i}{path.suffix}"
            path.rename(dest)
            parked += 1
    if filed or parked:
        git_commit_vault(
            vault,
            f"triage drain: {filed} filed at best guess, {parked} parked — "
            "revert with: git revert HEAD")
    return {"filed": filed, "parked": parked}


# ---- capture ----------------------------------------------------------------

def _slug(text: str) -> str:
    words = text.split()[:6]
    if len(words) == 1 and re.match(r"^https?://", words[0], re.IGNORECASE):
        # a bare URL has no word breaks to slug on — use its host instead,
        # e.g. https://youtube.com/watch?v=abc -> "youtube-com"
        host = urllib.parse.urlparse(words[0]).netloc.removeprefix("www.")
        words = [host] if host else words
    joined = " ".join(words)
    slug = re.sub(r"[^\w\s-]", "", joined.replace("#", "")).strip()
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug[:60] or "note"


def _atomic_write_bytes(path: Path, data: bytes, tmp_dir: Path) -> None:
    """Write-then-rename so a concurrently polling watcher never sees a half
    file — shared by every capture kind (text, image, image sidecar)."""
    fd, tmp = tempfile.mkstemp(dir=tmp_dir, prefix=".capture-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def capture(inbox: Path, text: str, tag: str | None) -> str:
    """Write a text capture the intake stage will parse (source=manual, tag
    free-routes). Returns the id the pipeline will mint for this note —
    filename time is minute-precision, so seconds are always 00."""
    inbox.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d-%H%M")
    name = _slug(text)
    suffix = f" #{tag}" if tag else ""
    path = inbox / f"{stamp} {name}{suffix}.md"
    i = 1
    while path.exists():
        i += 1
        # collision suffix goes on the NAME portion — after the #tag it would
        # break the free tag-route (intake._NAME_RE binds the tag last)
        path = inbox / f"{stamp} {name}-{i}{suffix}.md"

    _atomic_write_bytes(path, (text.rstrip() + "\n").encode("utf-8"), inbox)
    return now.strftime("%Y%m%d%H%M") + "00"


def valid_tag(tag: str | None) -> bool:
    return tag is None or tag in classify.TAG_TO_TYPE


# ---- audio capture (Pass V) --------------------------------------------------
# audio the browser's MediaRecorder can produce, mapped to the extension the
# intake stage recognises. Safari records mp4/m4a, everything else webm.
AUDIO_MIME_EXT = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".mp4",
    "audio/m4a": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}
MAX_AUDIO_BYTES = 100 * 1024 * 1024


def audio_extension(content_type: str | None) -> str | None:
    """Extension for a recording's Content-Type, or None if it isn't audio we
    can hand to the pipeline. Parameters (`;codecs=opus`) are ignored."""
    base = (content_type or "").split(";")[0].strip().lower()
    return AUDIO_MIME_EXT.get(base)


def audio_capture_path(inbox: Path, ext: str, name: str | None, tag: str | None,
                       now: datetime | None = None) -> tuple[Path, str]:
    """Reserve the inbox filename for a recording and return (path, note id).

    Same stamping as capture(): "YYYY-MM-DD-HHmm <name> #tag.<ext>", collision
    suffix on the NAME (after the #tag it would break the free tag-route)."""
    inbox.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now()
    stamp = now.strftime("%Y-%m-%d-%H%M")
    slug = _slug(name) if name and name.strip() else "voice-note"
    suffix = f" #{tag}" if tag else ""
    path = inbox / f"{stamp} {slug}{suffix}{ext}"
    i = 1
    while path.exists():
        i += 1
        path = inbox / f"{stamp} {slug}-{i}{suffix}{ext}"
    return path, now.strftime("%Y%m%d%H%M") + "00"


# A browser MediaRecorder's .webm/.mp4 is a streamed container with no overall
# duration in its header — ffprobe's format=duration comes back N/A, which
# means every mic capture would silently skip duration_min (D7/D4). A quick
# stream-copy remux (no re-encode, no quality loss) writes that header field.
REMUX_EXT = {".webm", ".mp4"}


def remux_for_duration(path: Path) -> None:
    """Best-effort in place; any failure (no ffmpeg, odd container) leaves the
    original untouched — this is a metadata nicety, never load-bearing."""
    if path.suffix.lower() not in REMUX_EXT:
        return
    tmp = path.with_name(f".remux-{path.name}")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(path), "-c", "copy", str(tmp)],
            check=True, capture_output=True, timeout=60)
        os.replace(tmp, path)
    except (OSError, subprocess.SubprocessError):
        Path(tmp).unlink(missing_ok=True)


# ---- image capture (Pass V2) -------------------------------------------------
# A photo shared from the "→ Brain Cloud" Shortcut or the cockpit's own photo
# button. HEIC never reaches this server — resizing AND format conversion
# happen on the device (the Shortcut's Resize/Convert steps, the PWA's canvas
# downscale), so no new dependency is needed here (CLAUDE.md §7: no Pillow,
# no server-side image decode).
IMAGE_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_IMAGE_BYTES = 15 * 1024 * 1024
INSIGHT_SIDECAR_SUFFIX = ".insight"


def image_extension(content_type: str | None) -> str | None:
    """Extension for a photo's Content-Type, or None if it isn't an image
    this server accepts. HEIC/HEIF are deliberately absent — the caller's
    envelope names the on-device conversion step instead of trying to decode
    it here."""
    base = (content_type or "").split(";")[0].strip().lower()
    return IMAGE_MIME_EXT.get(base)


def image_capture_path(inbox: Path, ext: str, name: str | None, tag: str | None,
                       now: datetime | None = None) -> tuple[Path, str]:
    """Reserve the inbox filename for a photo — same stamping and collision
    rule as audio_capture_path."""
    inbox.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now()
    stamp = now.strftime("%Y-%m-%d-%H%M")
    slug = _slug(name) if name and name.strip() else "photo"
    suffix = f" #{tag}" if tag else ""
    path = inbox / f"{stamp} {slug}{suffix}{ext}"
    i = 1
    while path.exists():
        i += 1
        path = inbox / f"{stamp} {slug}-{i}{suffix}{ext}"
    return path, now.strftime("%Y%m%d%H%M") + "00"


def image_insight_sidecar(image_path: Path) -> Path:
    """The dotfile carrying a photo's quick thought. Dotfiles are invisible to
    intake.poll (pipeline/intake.py), and this is written to disk BEFORE the
    image itself, so the watcher can never see the image without its sidecar
    already there — no race, no ordering dependency between the two writes."""
    return image_path.with_name(f".{image_path.stem}{INSIGHT_SIDECAR_SUFFIX}")


# ---- resurface (Pass R, B6) --------------------------------------------------
# The candidate scan, cooldown/weight algorithm, and events.db bookkeeping all
# live in pipeline/resurface.py — pure, self-contained, no api/ imports (this
# package's binding design ruling). The two functions below are the thin
# api/-facing surface: the vault write (todo-file append) `act` needs, and a
# delegation wrapper so main.py's `notes.*` import surface doesn't change shape.

def resurface(vault: Path, db_path: Path, k: int = 1) -> list[dict]:
    """The notes.py-facing entry point GET /api/resurfaced calls — thin
    delegation to pipeline.resurface.pick, kept here so main.py's import
    surface for `notes.*` doesn't change shape."""
    return resurface_mod.pick(vault, db_path, k)


def resurface_respond(vault: Path, db_path: Path, note_id: str, action: str,
                      title: str) -> str | None:
    """Handle a Connect/Act/Archive tap from the Today screen. Returns the
    todo block id when `action == "act"` wrote one, else None. `connect` and
    `archive` only touch events.db (no vault write, no commit needed) —
    only `act` writes to the vault."""
    if action != "act":
        resurface_mod.record_response(db_path, note_id, action)
        return None

    todos_dir = vault / "06-Todos"
    todos_dir.mkdir(parents=True, exist_ok=True)
    today_file = todos_dir / f"{date.today().isoformat()}.md"
    i = 1
    existing = today_file.read_text(encoding="utf-8") if today_file.exists() else ""
    while f"^{note_id}-r{i}" in existing:
        i += 1
    block_id = f"{note_id}-r{i}"
    # a title with an embedded newline could otherwise inject extra todo
    # lines into the file — one line in, one line out
    safe_title = title.splitlines()[0] if title.splitlines() else title
    line = f"- [ ] Follow up: {safe_title} (from [[{note_id}]]) ^{block_id}"

    if not today_file.exists() or today_file.stat().st_size == 0:
        today_file.write_text(f"# Todos — {date.today().isoformat()}\n\n{line}\n", encoding="utf-8")
    else:
        with today_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    resurface_mod.record_response(db_path, note_id, action)
    git_commit_vault(vault, f"api: resurfaced {note_id} → todo")
    return f"^{block_id}"


# ---- resources (Pass 6) -----------------------------------------------------
# The Resource OS reads/writes 04-Resources notes. Insight lives in a body
# '## Insight' section (not the schema's frontmatter field) so it can carry the
# human-origin guarantee and hold a couple of sentences — see the pass plan.

RESOURCES_FOLDER = route.TYPE_FOLDER["resource"]  # "04-Resources"

# Resource status lifecycle, verbatim from SCHEMA-REFERENCE.md §6. The single
# source the /status advance validates against and the UI reads back.
RESOURCE_LIFECYCLE = ["inbox", "to-consume", "consumed", "referenced", "archived"]

# older_than scope → age in days (None = no age bound, the whole sample set).
SAMPLE_SCOPES: dict[str, int | None] = {"1d": 1, "1w": 7, "1m": 30, "all": None}

# Per-type extra frontmatter fields, verbatim from SCHEMA-REFERENCE.md §7 "Type
# extras": book: author; movie: where_to_watch, runtime; tutorial: steps,
# tools_mentioned, transcript; recipe: ingredients, steps; place: map_url,
# best_time. Read generically off every note — a field simply comes back None
# when that note's resource_type doesn't carry it.
RESOURCE_EXTRA_FIELDS = [
    "author", "where_to_watch", "runtime", "ingredients", "steps",
    "tools_mentioned", "transcript", "map_url", "best_time",
]

_INSIGHT_HEADING = "## insight"


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def _note_title(path: Path, fm: dict[str, str]) -> str:
    """Frontmatter title wins; fall back to the human filename sans date prefix."""
    return fm.get("title") or _DATE_PREFIX_RE.sub("", path.stem)


def _sections(body: str) -> list[dict[str, str]]:
    """Body → ordered [{heading, text}] split on H2s. Text before the first H2
    is returned under heading '' (only when it's non-empty)."""
    sections: list[dict[str, str]] = []
    heading = ""
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if heading or text:
            sections.append({"heading": heading, "text": text})

    for line in body.splitlines():
        if line.strip().startswith("## "):
            flush()
            heading = line.strip()[3:].strip()
            buf = []
        else:
            buf.append(line)
    flush()
    return sections


def set_insight_section(body: str, text: str) -> str:
    """Return the body with its '## Insight' section appended or replaced; empty
    text removes it. Result is stripped (no leading/trailing blank lines) — the
    writer re-adds the single blank line after the frontmatter."""
    kept: list[str] = []
    skipping = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.lower() == _INSIGHT_HEADING:
            skipping = True
            continue
        if skipping and stripped.startswith("## "):
            skipping = False
        if not skipping:
            kept.append(line)
    base = "\n".join(kept).strip()
    text = text.strip()
    if not text:
        return base
    block = f"## Insight\n{text}"
    return f"{base}\n\n{block}" if base else block


def _split_note(text: str) -> tuple[str, str] | None:
    """(frontmatter_block, body) or None when there's no frontmatter. The block
    is the raw text between the '---' fences (no fences, keeps inner newlines)."""
    if not text.startswith("---\n"):
        return None
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return None
    return parts[1], parts[2]


def _compose_note(fm_block: str, body: str) -> str:
    return "---\n" + fm_block.rstrip("\n") + "\n---\n\n" + body.strip() + "\n"


def _ensure_origin_human(fm_block: str) -> str:
    """The insight is the human's words — origin stays 'human', never flips to
    'ai' (SCHEMA §1 firewall + §7 'never overwritten by AI')."""
    lines = fm_block.splitlines()
    if any(line.startswith("origin:") for line in lines):
        lines = ["origin: human" if line.startswith("origin:") else line for line in lines]
    else:
        lines.append("origin: human")
    return "\n".join(lines)


def _stamp_field(fm_block: str, key: str, value: str) -> str:
    """Set a column-0 scalar frontmatter field, appending it if absent.
    One implementation, in the pipeline's frontmatter authority."""
    return route.stamp_field(fm_block, key, value)


def _resource_summary(vault: Path, path: Path, fm: dict[str, str], body: str) -> dict:
    insight = _insight_text(body)
    return {
        "id": fm.get("id", ""),
        "title": _note_title(path, fm),
        "category": fm.get("resource_type", ""),
        "status": fm.get("status", "inbox"),
        "cover": fm.get("cover") or None,
        "url": fm.get("source_url") or None,
        "created": fm.get("created", ""),
        "sample": fm.get("sample", "").lower() == "true",
        "file": str(path.relative_to(vault)),
        "has_insight": bool(insight),
        "insight": insight or None,
        # exposed so `q` can match the description too (Pass S4) — the
        # gallery already showed this in the detail drawer, just not here
        "description": fm.get("description") or None,
    }


def _resource_notes(vault: Path):
    """Yield (path, fm, body) for every resource-type note in 04-Resources."""
    folder = vault / RESOURCES_FOLDER
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*.md")):
        text = read_note(path)
        if text is None:
            continue
        fm, body = parse_frontmatter(text)
        if fm.get("type") == "resource":
            yield path, fm, body


def find_resource(vault: Path, note_id: str) -> Path | None:
    for path, fm, _ in _resource_notes(vault):
        if fm.get("id") == note_id:
            return path
    return None


def list_resources(vault: Path, *, category: str | None = None, status: str | None = None,
                   q: str | None = None, has_insight: bool | None = None,
                   sort: str = "created") -> list[dict]:
    items: list[dict] = []
    for path, fm, body in _resource_notes(vault):
        item = _resource_summary(vault, path, fm, body)
        if category and item["category"].lower() != category.lower():
            continue
        if status and item["status"] != status:
            continue
        if q:
            # search matches how the owner actually thinks about a resource:
            # not just its title, but the description, the type-extra fields
            # (a recipe's ingredients, a movie's where_to_watch, ...), the
            # insight, and the full note body — the body already contains the
            # insight section, so it isn't searched twice. More thorough than
            # _matches_query's title/description/insight-only check (Pass Q's
            # own whole-vault GET /api/search covers the general case; this
            # is specifically the Resources screen's own inline filter).
            extras = " ".join(fm.get(k, "") for k in RESOURCE_EXTRA_FIELDS)
            haystack = f"{item['title']} {fm.get('description', '')} {extras} {body}".lower()
            if q.lower() not in haystack:
                continue
        if has_insight is not None and item["has_insight"] != has_insight:
            continue
        items.append(item)
    if sort == "title":
        items.sort(key=lambda i: i["title"].lower())
    elif sort == "oldest":
        items.sort(key=lambda i: i["created"])
    else:  # "created" — newest first (default)
        items.sort(key=lambda i: i["created"], reverse=True)
    return items


def resource_detail(vault: Path, note_id: str) -> dict | None:
    path = find_resource(vault, note_id)
    if path is None:
        return None
    fm, body = parse_frontmatter(read_note(path) or "")
    detail = _resource_summary(vault, path, fm, body)
    detail["description"] = fm.get("description") or None
    detail["rating"] = fm.get("rating") or None
    for key in RESOURCE_EXTRA_FIELDS:
        detail[key] = fm.get(key) or None
    detail["sections"] = _sections(body)
    return detail


def set_resource_status(vault: Path, note_id: str, new_status: str) -> dict:
    """Restamp the status line (keeps type); stamp a 'consumed' date when the
    note reaches 'consumed'. Commits. Raises LookupError if the id isn't a
    resource. Caller validates new_status against RESOURCE_LIFECYCLE first."""
    path = find_resource(vault, note_id)
    if path is None:
        raise LookupError(note_id)
    text = path.read_text(encoding="utf-8")
    fm, _ = parse_frontmatter(text)
    new_text = _restamp(text, fm.get("type", "resource"), new_status)
    if new_status == "consumed":
        split = _split_note(new_text)
        if split is not None:
            fm_block, body = split
            new_text = _compose_note(_stamp_field(fm_block, "consumed", date.today().isoformat()), body)
    path.write_text(new_text, encoding="utf-8")
    git_commit_vault(vault, f"api: resource {note_id} → {new_status}")
    fm2, body2 = parse_frontmatter(path.read_text(encoding="utf-8"))
    return _resource_summary(vault, path, fm2, body2)


def set_resource_insight(vault: Path, note_id: str, text: str) -> dict:
    """Append/replace the '## Insight' section with the human's words; keep
    origin human; commit. Raises LookupError if the id isn't a resource."""
    path = find_resource(vault, note_id)
    if path is None:
        raise LookupError(note_id)
    split = _split_note(path.read_text(encoding="utf-8"))
    if split is None:
        raise LookupError(note_id)
    fm_block, body = split
    path.write_text(_compose_note(_ensure_origin_human(fm_block), set_insight_section(body, text)), encoding="utf-8")
    git_commit_vault(vault, f"api: insight on {note_id}")
    fm2, body2 = parse_frontmatter(path.read_text(encoding="utf-8"))
    return _resource_summary(vault, path, fm2, body2)


# ---- sample-data purge (safety-critical) ------------------------------------
# The ONLY thing the purge may target is a note whose frontmatter has exactly
# sample: true. A note without that flag can never be deleted here, whatever its
# age. older_than filters WITHIN the sample set by created date.

def sample_matching(vault: Path, scope: str) -> list[Path]:
    """Resource notes with sample:true whose created date is old enough for the
    scope. scope 'all' → every sample note (no age bound)."""
    days = SAMPLE_SCOPES[scope]
    cutoff = None if days is None else date.today() - timedelta(days=days)
    out: list[Path] = []
    for path, fm, _ in _resource_notes(vault):
        if fm.get("sample", "").lower() != "true":
            continue
        if cutoff is None:
            out.append(path)
            continue
        created = _parse_date(fm.get("created", ""))
        if created is not None and created <= cutoff:
            out.append(path)
    return out


def sample_titles(paths: list[Path]) -> list[str]:
    titles: list[str] = []
    for path in paths:
        fm, _ = parse_frontmatter(read_note(path) or "")
        titles.append(_note_title(path, fm))
    return titles


# ---- whole-vault search (Pass Q) --------------------------------------------
# A filesystem scan, not an index — no note content ever touches SQLite
# (CLAUDE.md §1). At personal-vault scale (a few thousand notes) this is well
# under 100ms; there is nothing here worth caching.

# raw/ (source recordings — the pipeline never reads it, SCHEMA-REFERENCE.md
# §1) and _System/ (logs, not knowledge) are excluded — the same boundary the
# pipeline itself respects.
SEARCH_EXCLUDED_FOLDERS = {"raw", "_System"}
SEARCH_MIN_QUERY_LEN = 2
SEARCH_DEFAULT_LIMIT = 50
SEARCH_MAX_LIMIT = 100
SEARCH_EXCERPT_RADIUS = 80


def _search_excerpt(text: str, q_lower: str) -> str:
    """The matched line, trimmed to ~160 chars centered on the match — never
    the whole body. Falls back to the start of the text if, somehow, the
    match isn't found on any single line (shouldn't happen: body_match is
    only set when `q_lower in body.lower()`, but a line-by-line re-scan is
    cheap insurance against a body containing a comparison client wouldn't
    naturally split on lines)."""
    for line in text.splitlines():
        idx = line.lower().find(q_lower)
        if idx == -1:
            continue
        start = max(0, idx - SEARCH_EXCERPT_RADIUS)
        end = min(len(line), idx + len(q_lower) + SEARCH_EXCERPT_RADIUS)
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(line) else ""
        return f"{prefix}{line[start:end].strip()}{suffix}"
    return text.strip()[: SEARCH_EXCERPT_RADIUS * 2]


def search_vault(vault: Path, q: str, limit: int = SEARCH_DEFAULT_LIMIT) -> list[dict]:
    """Every note whose title, frontmatter values, or body contains `q`
    (case-insensitive substring), ranked title-match > frontmatter-match >
    body-match, then by file path for a stable order. `limit` is capped by
    the caller at SEARCH_MAX_LIMIT; the `q` length floor is enforced by the
    caller too (SEARCH_MIN_QUERY_LEN) — this function trusts its input.
    """
    q_lower = q.lower()
    hits: list[tuple[int, str, dict]] = []
    for path in sorted(vault.rglob("*.md")):
        rel_parts = path.relative_to(vault).parts
        if not rel_parts or rel_parts[0] in SEARCH_EXCLUDED_FOLDERS:
            continue
        if any(part.startswith(".") for part in rel_parts):
            continue
        text = read_note(path)
        if text is None:
            continue
        fm, body = parse_frontmatter(text)
        if not fm:
            continue   # not a note the pipeline wrote — nothing reliable to search
        title = _note_title(path, fm)

        if q_lower in title.lower():
            matched_in, rank, excerpt = "title", 0, title
        else:
            fm_hit = next((f"{k}: {v}" for k, v in fm.items()
                          if k != "id" and q_lower in str(v).lower()), None)
            if fm_hit is not None:
                matched_in, rank, excerpt = "frontmatter", 1, fm_hit
            elif q_lower in body.lower():
                matched_in, rank, excerpt = "body", 2, _search_excerpt(body, q_lower)
            else:
                continue

        hits.append((rank, str(path), {
            "id": fm.get("id", ""),
            "type": fm.get("type", ""),
            "title": title,
            "file": "/".join(rel_parts),
            "folder": rel_parts[0],
            "excerpt": excerpt,
            "matched_in": matched_in,
        }))
    hits.sort(key=lambda h: (h[0], h[1]))
    return [h[2] for h in hits[:limit]]
