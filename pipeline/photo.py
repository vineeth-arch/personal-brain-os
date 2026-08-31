"""Stage — photo capture orchestration (Pass 13). An image is routed exactly
like a voice memo: vision.extract() stands in for transcription, then the
EXISTING classify.classify() (tag-or-LLM) and route.route() handle typing and
filing unchanged. Only the resource case gets its own richer note-builder
(mirroring enrich.py's link path) because resource is the only schema type
with `cover`/`description` fields.

THE USER'S OWN WORDS SAVED INSTANTLY AND UNCONDITIONALLY; extraction and
description-writing are best-effort decoration that may fail without losing
anything — the note is always written, exactly like Pass L's enrichment
principle."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from . import classify, enrich, errors, llm, route, sidecar, vision

_MIME_FOR_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".heic": "image/heic",
}
_EXT_FOR_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def mime_for(ext: str) -> str:
    return _MIME_FOR_EXT.get(ext.lower(), "image/jpeg")


def ext_for_mime(mime: str) -> str:
    return _EXT_FOR_MIME.get(mime, ".jpg")


def read_sidecar(image_path: Path) -> dict:
    """The owner's typed thought + any on-device OCR text, written by
    POST /api/capture/image. Missing (a Syncthing-arrived image never went
    through that endpoint) or malformed sidecars degrade to empty fields —
    never a pipeline failure over a hostile or absent sidecar."""
    path = sidecar.sidecar_path(image_path)
    if not path.exists():
        return {"text": "", "ocr": "", "source": "photo"}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("sidecar is not a JSON object")
    except (OSError, ValueError):
        return {"text": "", "ocr": "", "source": "photo"}
    return {
        "text": str(data.get("text", ""))[:20_000],
        "ocr": str(data.get("ocr", ""))[:20_000],
        "source": str(data.get("source", "photo")),
    }


def load_image_for_processing(item) -> tuple[bytes, str]:
    """(bytes, mime) ready for vision + the attachments copy. HEIC only ever
    arrives via Syncthing — the capture Shortcut always converts to JPEG on
    the phone before uploading — so this is a fallback path, not the common
    case. A HEIC conversion failure quarantines the file (it won't fix
    itself on retry, so it's a permanent StageError)."""
    ext = item.path.suffix.lower()
    if ext != ".heic":
        return item.path.read_bytes(), mime_for(ext)
    if not shutil.which("ffmpeg"):
        raise errors.StageError(
            "This HEIC photo can't be converted.",
            "ffmpeg isn't available on this server, so HEIC (the iPhone's native photo "
            "format) can't be converted to something the pipeline can read.",
            "Convert the photo to JPEG on the phone and re-share it, or install ffmpeg "
            "on the server.")
    with tempfile.TemporaryDirectory() as td:
        dst = Path(td) / "converted.jpg"
        try:
            subprocess.run(["ffmpeg", "-y", "-i", str(item.path), str(dst)],
                          capture_output=True, timeout=30, check=True)
            if not dst.exists() or dst.stat().st_size == 0:
                raise RuntimeError("ffmpeg produced no output")
            return dst.read_bytes(), "image/jpeg"
        except Exception as e:
            raise errors.StageError(
                "This HEIC photo can't be converted.",
                f"ffmpeg couldn't convert it ({e}) — this server's ffmpeg build may be "
                "missing HEIC support (libheif).",
                "Convert the photo to JPEG on the phone — the share Shortcut does this "
                "automatically — or install an ffmpeg build with HEIC support on the server.",
            ) from e


def classify_image(item, extracted_text: str, config, llm_fn=None) -> classify.Classification:
    """classify.classify() with one guard on top: no tag AND no extracted
    text must never trigger an LLM call on nothing — straight to
    needs-review, per the 'never a silent guess' rule."""
    tag = (item.tag or "").lower() if item.tag else None
    if tag not in classify.TAG_TO_TYPE and not extracted_text.strip():
        return classify.Classification(
            type="musing", title=item.name, confidence=0.0,
            needs_review=True, routed_by="llm", provider="none")
    return classify.classify(item, extracted_text, config, llm_fn)


def copy_to_attachments(item, image_bytes: bytes, ext: str, vault_path: Path) -> str:
    """Copy (never move) the processed bytes into vault/attachments/,
    returning the vault-relative path for the note's `cover` field / embed.
    A copy, not a move, because the ORIGINAL file in the inbox still needs to
    reach archive_path via the normal stage-6 archive step afterward.

    The filename is built from item.captured + a kebab slug of item.name —
    NOT from the raw inbox filename — because the raw name carries a
    ` #tag` suffix (e.g. "sunset #resource.jpg") that intake.py deliberately
    leaves in place for its own parsing. A `#` in an unquoted YAML scalar
    starts a COMMENT: writing it into `cover:`/an embed link would silently
    truncate the value in Obsidian's own frontmatter parser. route._kebab()
    already strips everything but word characters, so the result is always
    YAML- and filesystem-safe."""
    dest_dir = Path(vault_path) / "attachments"
    dest_dir.mkdir(parents=True, exist_ok=True)
    created = item.captured.strftime("%Y-%m-%d")
    base = f"{created}-{route._kebab(item.name)}"
    dest = dest_dir / f"{base}{ext}"
    i = 1
    while dest.exists():
        i += 1
        dest = dest_dir / f"{base}-{i}{ext}"
    dest.write_bytes(image_bytes)
    return f"attachments/{dest.name}"


# ---- resource structuring (mirrors enrich.py's link-structuring shape) ------

def _structure_prompt(user_text: str, extracted_text: str) -> str:
    return (
        "Structure this captured PHOTO into a resource note. Return ONLY JSON with keys: "
        "resource_type, title, description.\n"
        f"resource_type must be one of: {', '.join(enrich.RESOURCE_TYPES)}.\n"
        "description = one or two plain lines summarizing what this is useful for. "
        "title = a short human title.\n\n"
        f"The owner's own words about this photo:\n{user_text or '(none given)'}\n\n"
        f"Text visible in the image:\n{extracted_text[:3000] or '(none found)'}"
    )


def _validate_structure(data: object) -> str | None:
    if not isinstance(data, dict):
        return "not a JSON object"
    if str(data.get("resource_type", "")).lower() not in enrich.RESOURCE_TYPES:
        return "resource_type not in the locked list"
    if not str(data.get("title") or "").strip():
        return "empty title"
    return None


def structure(user_text: str, extracted_text: str, config, router=None) -> dict:
    """All-fail degrades to a plain resource note — never a review gate, a
    photo tagged/classified #resource IS a resource regardless."""
    router = router or llm.complete_json
    data, _provider, _attempts = router(_structure_prompt(user_text, extracted_text), config,
                                        _validate_structure)
    if data is None:
        return {"resource_type": "article", "title": "captured-photo", "description": ""}
    return data


# ---- note building ------------------------------------------------------------

def build_image_resource_note(item, structured: dict, extracted_text: str, user_text: str,
                              note_id: str, created: str, cover_rel: str) -> str:
    rtype = str(structured.get("resource_type", "article")).lower()
    if rtype not in enrich.RESOURCE_TYPES:
        rtype = "article"
    title = str(structured.get("title") or "captured-photo").strip()
    fm = [
        "---",
        f"id: {note_id}",
        "type: resource",
        f"resource_type: {rtype}",
        f"created: {created}",
        f"source: {item.source}",
        "origin: human",
        "meta_origin: ai",
        f"title: {title}",
        f"cover: {cover_rel}",
        f"description: {str(structured.get('description', '')).strip()}",
        "status: inbox",
        "platform: photo",
        "categories: []",
        "subjects: []",
        "tags: []",
        "---",
    ]
    body = ["\n".join(fm), "", f"![[{cover_rel}]]", ""]
    insight = user_text.strip()
    if insight:
        # the owner's own words, verbatim — origin human, never overwritten by AI
        body += ["## Insight", "", insight, ""]
    if extracted_text.strip():
        body += ["## Extracted text", "", extracted_text.strip(), ""]
    return "\n".join(body).rstrip() + "\n"


def route_image_resource(item, structured: dict, extracted_text: str, user_text: str,
                         vault_path: Path, cover_rel: str) -> Path:
    note_id = item.captured.strftime("%Y%m%d%H%M%S")
    created = item.captured.strftime("%Y-%m-%d")
    title = str(structured.get("title") or "captured-photo")
    dest_dir = Path(vault_path) / route.TYPE_FOLDER["resource"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = f"{created}-{route._kebab(title)}"
    path = dest_dir / f"{base}.md"
    i = 1
    while path.exists():
        i += 1
        path = dest_dir / f"{base}-{i}.md"
    path.write_text(build_image_resource_note(item, structured, extracted_text, user_text,
                                              note_id, created, cover_rel))
    return path


def generic_image_body(cover_rel: str, user_text: str, extracted_text: str) -> str:
    """The 'transcript' for non-resource types — an embed of the photo plus
    whatever text exists, fed into the UNCHANGED route.route() so every
    other note type (todo/musing/journal/decision/project/person/learning)
    gets normal frontmatter and filing with no image-specific code there."""
    body = [f"![[{cover_rel}]]", ""]
    if user_text.strip():
        body += [user_text.strip(), ""]
    if extracted_text.strip():
        body += ["## Extracted text", "", extracted_text.strip(), ""]
    return "\n".join(body).rstrip() + "\n"
