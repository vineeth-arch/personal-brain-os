"""Vault note operations: review queue, approve/retype, capture, resurface.

Reads and writes follow SCHEMA-REFERENCE.md exactly — flat frontmatter as
written by pipeline/route.py, immutable id, locked vocabularies imported from
the pipeline (never re-declared here). Every vault write is followed by a git
commit with a descriptive message (CLAUDE.md §3 — revertible AI-era changes).
"""
from __future__ import annotations

import logging
import os
import random
import re
import sqlite3
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from pipeline import classify, notefm, people, relationships as rel, route
# The frontmatter primitives live in the pipeline package (dex.py and search.py
# need them too, and imports only run api → pipeline). Re-exported here under
# their original names so this module's call sites read unchanged.
from pipeline.notefm import parse_frontmatter  # noqa: F401  (re-export)

log = logging.getLogger("api")

_split_note = notefm.split_note
_compose_note = notefm.compose_note
_stamp_field = notefm.stamp_field

_CONFIDENCE_RE = re.compile(r"confidence=([0-9.]+)")
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")

EXCERPT_CHARS = 300


# ---- frontmatter ------------------------------------------------------------
# parse_frontmatter / _split_note / _compose_note / _stamp_field are the
# pipeline.notefm primitives, bound at the top of this module.

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

def _confidence_map(db_path: Path) -> dict[str, float]:
    """note filename → classifier confidence, joined from events.db.

    Route events log 'wrote <name>.md' (basenames, ', '-joined); the nearest
    preceding classify event on the same source file carries
    'type=X confidence=0.62 by=llm'. Later route events win.
    """
    if not db_path.exists():
        return {}
    out: dict[str, float] = {}
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
        for name in route_msg.removeprefix("wrote ").split(", "):
            out[name.strip()] = conf
    return out


def list_review(vault: Path, db_path: Path) -> list[dict]:
    inbox_dir = vault / route.INBOX_FOLDER
    if not inbox_dir.is_dir():
        return []
    confidences = _confidence_map(db_path)
    items = []
    for path in sorted(inbox_dir.glob("*.md")):
        fm, body = parse_frontmatter(path.read_text())
        if fm.get("status") != "needs-review":
            continue
        items.append({
            "id": fm.get("id", ""),
            "file": f"{route.INBOX_FOLDER}/{path.name}",
            "title": _DATE_PREFIX_RE.sub("", path.stem),
            "excerpt": body.strip()[:EXCERPT_CHARS],
            "suggested_type": fm.get("type", "musing"),
            "confidence": confidences.get(path.name, 0.5),
            "created": fm.get("created", ""),
        })
    return items


def count_review(vault: Path) -> int:
    """The Today badge must equal the triage queue length — same scan."""
    inbox_dir = vault / route.INBOX_FOLDER
    if not inbox_dir.is_dir():
        return 0
    n = 0
    for path in inbox_dir.glob("*.md"):
        fm, _ = parse_frontmatter(path.read_text())
        if fm.get("status") == "needs-review":
            n += 1
    return n


def approve(vault: Path, note_id: str, new_type: str) -> str:
    """Restamp type/status and move the note to its folder. Returns the
    vault-relative destination. Raises LookupError if the id isn't in review."""
    inbox_dir = vault / route.INBOX_FOLDER
    target: Path | None = None
    text = ""
    if inbox_dir.is_dir():
        for path in inbox_dir.glob("*.md"):
            text = path.read_text()
            fm, _ = parse_frontmatter(text)
            if fm.get("id") == note_id and fm.get("status") == "needs-review":
                target = path
                break
    if target is None:
        raise LookupError(note_id)

    new_status = route.STATUS_INITIAL.get(new_type, "active")
    dest_dir = vault / route.TYPE_FOLDER[new_type]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / target.name
    i = 1
    while dest.exists():
        i += 1
        dest = dest_dir / f"{target.stem}-{i}{target.suffix}"

    dest.write_text(_restamp(text, new_type, new_status))
    target.unlink()
    git_commit_vault(vault, f"api: filed {note_id} as {new_type}")
    return str(dest.relative_to(vault))


# ---- capture ----------------------------------------------------------------

def _slug(text: str) -> str:
    words = " ".join(text.split()[:6])
    slug = re.sub(r"[^\w\s-]", "", words.replace("#", "")).strip()
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug[:60] or "note"


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

    # atomic-ish write so a concurrently polling watcher never sees a half file
    fd, tmp = tempfile.mkstemp(dir=inbox, prefix=".capture-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text.rstrip() + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return now.strftime("%Y%m%d%H%M") + "00"


def valid_tag(tag: str | None) -> bool:
    return tag is None or tag in classify.TAG_TO_TYPE


# ---- resurface ----------------------------------------------------------------

def resurface(vault: Path) -> dict | None:
    """One deterministic pick per day from the knowledge folders (musing →
    02-Musings, learning → 03-Learnings, insight → wiki/, per
    route.TYPE_FOLDER). The merged candidate list is sorted folder-then-date
    (not globally date-interleaved) — stable and deterministic, which is all
    the daily pick needs."""
    folders = [vault / route.TYPE_FOLDER[t] for t in ("musing", "learning", "insight")]
    candidates = sorted(
        p
        for folder in folders
        if folder.is_dir()
        for p in folder.glob("*.md")
        if p.read_text().startswith("---\n")
    )
    if not candidates:
        return None
    pick = random.Random(date.today().toordinal()).choice(candidates)
    fm, body = parse_frontmatter(pick.read_text())
    paragraph = next((p.strip() for p in body.split("\n\n") if p.strip()), "")
    return {
        "id": fm.get("id", ""),
        "title": _DATE_PREFIX_RE.sub("", pick.stem),
        "file": str(pick.relative_to(vault)),
        "excerpt": paragraph[:EXCERPT_CHARS],
        "type": fm.get("type", "musing"),
        "created": fm.get("created", ""),
    }


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

_INSIGHT_HEADING = "## insight"


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def _note_title(path: Path, fm: dict[str, str]) -> str:
    """Frontmatter title wins; fall back to the human filename sans date prefix."""
    return fm.get("title") or _DATE_PREFIX_RE.sub("", path.stem)


def _insight_text(body: str) -> str:
    """Text under a '## Insight' H2, up to the next H2 or EOF. '' when absent/blank."""
    out: list[str] = []
    capturing = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.lower() == _INSIGHT_HEADING:
            capturing = True
            continue
        if capturing and stripped.startswith("## "):
            break
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


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


def _ensure_origin_human(fm_block: str) -> str:
    """The insight is the human's words — origin stays 'human', never flips to
    'ai' (SCHEMA §1 firewall + §7 'never overwritten by AI')."""
    lines = fm_block.splitlines()
    if any(line.startswith("origin:") for line in lines):
        lines = ["origin: human" if line.startswith("origin:") else line for line in lines]
    else:
        lines.append("origin: human")
    return "\n".join(lines)


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
    }


def _resource_notes(vault: Path):
    """Yield (path, fm, body) for every resource-type note in 04-Resources."""
    folder = vault / RESOURCES_FOLDER
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*.md")):
        fm, body = parse_frontmatter(path.read_text())
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
        if q and q.lower() not in item["title"].lower():
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
    fm, body = parse_frontmatter(path.read_text())
    detail = _resource_summary(vault, path, fm, body)
    detail["description"] = fm.get("description") or None
    detail["author"] = fm.get("author") or None
    detail["rating"] = fm.get("rating") or None
    detail["sections"] = _sections(body)
    return detail


def set_resource_status(vault: Path, note_id: str, new_status: str) -> dict:
    """Restamp the status line (keeps type); stamp a 'consumed' date when the
    note reaches 'consumed'. Commits. Raises LookupError if the id isn't a
    resource. Caller validates new_status against RESOURCE_LIFECYCLE first."""
    path = find_resource(vault, note_id)
    if path is None:
        raise LookupError(note_id)
    text = path.read_text()
    fm, _ = parse_frontmatter(text)
    new_text = _restamp(text, fm.get("type", "resource"), new_status)
    if new_status == "consumed":
        split = _split_note(new_text)
        if split is not None:
            fm_block, body = split
            new_text = _compose_note(_stamp_field(fm_block, "consumed", date.today().isoformat()), body)
    path.write_text(new_text)
    git_commit_vault(vault, f"api: resource {note_id} → {new_status}")
    fm2, body2 = parse_frontmatter(path.read_text())
    return _resource_summary(vault, path, fm2, body2)


def set_resource_insight(vault: Path, note_id: str, text: str) -> dict:
    """Append/replace the '## Insight' section with the human's words; keep
    origin human; commit. Raises LookupError if the id isn't a resource."""
    path = find_resource(vault, note_id)
    if path is None:
        raise LookupError(note_id)
    split = _split_note(path.read_text())
    if split is None:
        raise LookupError(note_id)
    fm_block, body = split
    path.write_text(_compose_note(_ensure_origin_human(fm_block), set_insight_section(body, text)))
    git_commit_vault(vault, f"api: insight on {note_id}")
    fm2, body2 = parse_frontmatter(path.read_text())
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
        fm, _ = parse_frontmatter(path.read_text())
        titles.append(_note_title(path, fm))
    return titles


# ---- people (Pass 7) ---------------------------------------------------------
# 07-People notes. Reads join the stored frontmatter with the computed lifecycle
# (pipeline.relationships); writes touch named fields and append to the
# interaction log, never the owner's prose.

PEOPLE_FILTERS = ["all", *rel.PERSON_STATUSES, rel.UNSET]


def _person_summary(vault: Path, path: Path, fm: dict[str, str], today: date) -> dict:
    computed = rel.classify_person(fm, today)
    return {
        "id": fm.get("id", ""),
        "name": people.person_name(path, fm),
        "file": str(path.relative_to(vault)),
        "relationship": fm.get("relationship", ""),
        "company": fm.get("company", ""),
        "warmth_stage": fm.get("warmth_stage", ""),
        "cadence_days": rel.parse_cadence(fm.get("cadence_days")),
        "last_contact": fm.get("last_contact", "") or None,
        "days_since_contact": rel.days_since_contact(fm, today),
        "days_overdue": rel.days_overdue(fm, today),
        "status": computed,
        # `unset` isn't a lifecycle state — it means this note predates the
        # cadence fields, so the UI asks for them instead of showing a number.
        "unset": computed == rel.UNSET,
        "dex_deeplink": fm.get("dex_deeplink", "") or None,
    }


def list_people(vault: Path, *, filter: str = "all", today: date | None = None) -> list[dict]:
    today = today or date.today()
    items = [_person_summary(vault, path, fm, today)
             for path, fm, _ in people.person_notes(vault)]
    if filter != "all":
        items = [i for i in items if i["status"] == filter]
    # most overdue first: the people about to slip are the reason to open this
    # screen at all. Unset notes sort last — they're a setup chore, not a nudge.
    items.sort(key=lambda i: (i["unset"], -(i["days_overdue"] or 0), i["name"].lower()))
    return items


def person_detail(vault: Path, note_id: str, today: date | None = None) -> dict | None:
    today = today or date.today()
    for path, fm, body in people.person_notes(vault):
        if fm.get("id") != note_id:
            continue
        summary = _person_summary(vault, path, fm, today)
        summary.update({
            "sections": people.sections(body),
            "interactions": people.interaction_log(body),
            "channels": fm.get("channels", ""),
            "dex_id": fm.get("dex_id", "") or None,
            "created": fm.get("created", ""),
            "origin": fm.get("origin", ""),
        })
        return summary
    return None


def log_contact(vault: Path, note_id: str, today: date | None = None) -> dict:
    """Stamp last_contact to today and add one dated line to the interaction
    log. Raises LookupError when the id is unknown."""
    today = today or date.today()
    path = people.find_person(vault, note_id)
    if path is None:
        raise LookupError(note_id)
    text = path.read_text()
    split = _split_note(text)
    if split is None:
        raise LookupError(note_id)
    fm_block, body = split
    fm, _ = parse_frontmatter(text)

    stamp = today.isoformat()
    fm_block = _stamp_field(fm_block, "last_contact", stamp)
    fm["last_contact"] = stamp
    fm_block = people.stamp_status(fm_block, fm, today)
    body = people.append_interaction(body, f"- {stamp} — contact logged")
    path.write_text(_compose_note(fm_block, body))

    git_commit_vault(vault, f"api: logged contact {note_id}")
    return _person_summary(vault, path, parse_frontmatter(path.read_text())[0], today)


def update_person(vault: Path, note_id: str, changes: dict, today: date | None = None) -> dict:
    """Set cadence_days / warmth_stage. Raises LookupError for an unknown id,
    ValueError (with the allowed set) for a bad value.

    `status` is deliberately not settable: it's computed from last_contact +
    cadence_days, so accepting a hand-set value would mean writing something
    the next read contradicts. Parking a relationship = a longer cadence."""
    today = today or date.today()
    path = people.find_person(vault, note_id)
    if path is None:
        raise LookupError(note_id)

    cadence = changes.get("cadence_days")
    if cadence is not None and rel.parse_cadence(cadence) is None:
        raise ValueError("cadence_days must be a whole number of days above zero")
    warmth = changes.get("warmth_stage")
    if warmth is not None and warmth not in rel.WARMTH_STAGES:
        raise ValueError("warmth_stage must be one of: " + ", ".join(rel.WARMTH_STAGES))

    text = path.read_text()
    split = _split_note(text)
    if split is None:
        raise LookupError(note_id)
    fm_block, body = split

    if cadence is not None:
        fm_block = _stamp_field(fm_block, "cadence_days", str(rel.parse_cadence(cadence)))
    if warmth is not None:
        fm_block = _stamp_field(fm_block, "warmth_stage", warmth)
    # These edits are the owner's, typed in the cockpit — the note is theirs.
    fm_block = _ensure_origin_human(fm_block)
    # A cadence edit can change the lifecycle, so re-stamp status from the new
    # values: the file always agrees with what the screen shows.
    merged = dict(parse_frontmatter(_compose_note(fm_block, body))[0])
    fm_block = people.stamp_status(fm_block, merged, today)
    path.write_text(_compose_note(fm_block, body))

    git_commit_vault(vault, f"api: person {note_id} updated")
    fm, _ = parse_frontmatter(path.read_text())
    return _person_summary(vault, path, fm, today)
