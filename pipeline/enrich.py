"""Stage L — link capture + enrichment.

THE USER'S NOTE IS SAVED INSTANTLY AND UNCONDITIONALLY; enrichment is
best-effort decoration that may fail without losing anything. A failed
enrichment writes the note with `enriched: false` frontmatter and one quiet
`enrich` event row — never a quarantine, never an alarm push.

All network I/O goes through injectable seams (`fetch`, `router`) so every
test is hermetic. Structuring reuses the Pass B model router.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import llm, route

HTTP_TIMEOUT = 10
APIFY_TIMEOUT = 60

RESOURCE_TYPES = ["tool", "tutorial", "book", "movie", "recipe", "place", "article"]

_URL_RE = re.compile(r"https?://[^\s<>\"')]+")
_YT_ID_RE = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/|embed/|live/))([\w-]{11})")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE)
_TIMEDTEXT_RE = re.compile(r"<text[^>]*>(.*?)</text>", re.IGNORECASE | re.DOTALL)


@dataclass
class Enrichment:
    platform: str            # youtube | instagram | web
    enriched: bool
    url: str
    title: str = ""
    author: str = ""         # channel / owner / site
    cover: str = ""          # image URL
    caption: str = ""        # IG caption / web description
    transcript: str = ""     # YouTube transcript when available
    detail: str = ""         # plain-English reason when enriched is false


def extract_url(text: str) -> str | None:
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(".,);]") if m else None


def is_link_text(text: str) -> bool:
    return extract_url(text) is not None


# ---- HTTP seam --------------------------------------------------------------

def _default_fetch(url: str, data: bytes | None = None, timeout: int = HTTP_TIMEOUT) -> bytes:
    """Injectable in tests. POST when data is given, else GET."""
    req = urllib.request.Request(
        url, data=data,
        headers={"User-Agent": "Mozilla/5.0 (Brain Cockpit)",
                 **({"Content-Type": "application/json"} if data else {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---- per-platform enrichers -------------------------------------------------

def _unescape(text: str) -> str:
    import html
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _parse_timedtext(xml: str) -> str:
    parts = [_unescape(m.group(1)) for m in _TIMEDTEXT_RE.finditer(xml)]
    return " ".join(p for p in parts if p)[:4000]


def _enrich_youtube(url: str, vid: str, fetch) -> Enrichment:
    try:
        oembed = "https://www.youtube.com/oembed?" + urllib.parse.urlencode(
            {"url": url, "format": "json"})
        data = json.loads(fetch(oembed))
    except Exception:
        return Enrichment("youtube", False, url,
                          detail="YouTube didn't return oEmbed data — the video may be private or removed. The note is saved.")
    transcript = ""
    if vid:
        try:  # transcript is optional and often empty — never fail on it
            raw = fetch(f"https://video.google.com/timedtext?lang=en&v={vid}")
            transcript = _parse_timedtext(raw.decode("utf-8", "ignore"))
        except Exception:
            transcript = ""
    return Enrichment("youtube", True, url,
                      title=str(data.get("title", "")),
                      author=str(data.get("author_name", "")),
                      cover=str(data.get("thumbnail_url", "")),
                      transcript=transcript)


def _enrich_instagram(url: str, config, fetch) -> Enrichment:
    token = os.environ.get("APIFY_TOKEN")
    actor = (getattr(config, "raw", {}).get("apify") or {}).get("actor_id")
    if not token or not actor:
        return Enrichment("instagram", False, url,
                          detail="Apify isn't configured (APIFY_TOKEN in the environment + apify.actor_id in config.json), so Instagram can't be enriched. The note is saved.")
    try:
        api = (f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
               f"?token={token}")
        body = json.dumps({"directUrls": [url], "resultsLimit": 1}).encode()
        items = json.loads(fetch(api, data=body, timeout=APIFY_TIMEOUT))
        item = items[0] if isinstance(items, list) and items else {}
        caption = str(item.get("caption") or "")
        cover = str(item.get("displayUrl") or item.get("thumbnailUrl") or "")
        if not (caption or cover):
            raise ValueError("empty payload")
        return Enrichment("instagram", True, url,
                          title=caption[:80] or "instagram-post", caption=caption,
                          cover=cover, author=str(item.get("ownerUsername", "")))
    except Exception:
        return Enrichment("instagram", False, url,
                          detail="Instagram enrichment failed — this is expected periodically (the scraper is ToS-grey and breaks). The note is saved; it retries later.")


def _enrich_web(url: str, fetch) -> Enrichment:
    try:
        html = fetch(url).decode("utf-8", "ignore")
    except Exception:
        return Enrichment("web", False, url,
                          detail="The page couldn't be fetched (offline, blocked, or a dead link). The note is saved.")
    m = _TITLE_RE.search(html)
    title = _unescape(m.group(1))[:200] if m else ""
    mi = _OG_IMAGE_RE.search(html)
    cover = mi.group(1) if mi else ""
    if not title:
        return Enrichment("web", False, url, cover=cover,
                          detail="The page had no readable title. The note is saved.")
    return Enrichment("web", True, url, title=title, cover=cover)


def enrich_url(url: str, config, fetch=None) -> Enrichment:
    fetch = fetch or _default_fetch
    host = urllib.parse.urlparse(url).netloc.lower()
    yt = _YT_ID_RE.search(url)
    if yt or "youtube.com" in host or "youtu.be" in host:
        return _enrich_youtube(url, yt.group(1) if yt else "", fetch)
    if "instagram.com" in host:
        return _enrich_instagram(url, config, fetch)
    return _enrich_web(url, fetch)


# ---- structuring via the Pass B router --------------------------------------

def _structure_prompt(user_text: str, enr: Enrichment) -> str:
    ctx = [f"Platform: {enr.platform}", f"URL: {enr.url}"]
    if enr.title:
        ctx.append(f"Title: {enr.title}")
    if enr.author:
        ctx.append(f"By: {enr.author}")
    if enr.caption:
        ctx.append(f"Caption: {enr.caption[:1500]}")
    if enr.transcript:
        ctx.append(f"Transcript: {enr.transcript[:2500]}")
    return (
        "Structure this saved link into a resource note. Return ONLY JSON with keys: "
        "resource_type, title, description, is_recipe, ingredients, steps.\n"
        f"resource_type must be one of: {', '.join(RESOURCE_TYPES)}.\n"
        "description = one line. is_recipe = true ONLY when the content is clearly a "
        "recipe with identifiable ingredients AND steps; then ingredients and steps are "
        "arrays of strings, else []. title = a short human title.\n\n"
        f"The user's own words:\n{user_text}\n\n"
        f"Link metadata:\n" + "\n".join(ctx)
    )


def validate_structure(data: object) -> str | None:
    if not isinstance(data, dict):
        return "not a JSON object"
    if str(data.get("resource_type", "")).lower() not in RESOURCE_TYPES:
        return "resource_type not in the locked list"
    if not str(data.get("title") or "").strip():
        return "empty title"
    return None


def structure(user_text: str, enr: Enrichment, config, router=None) -> dict:
    """Router-structured resource metadata. All-fail degrades to a plain
    resource note — a link IS a resource, so this never needs a review gate."""
    router = router or llm.complete_json
    data, _provider, _attempts = router(_structure_prompt(user_text, enr), config,
                                        validate_structure)
    if data is None:
        default_type = "tutorial" if enr.platform == "youtube" else "article"
        return {"resource_type": default_type, "title": enr.title or "untitled",
                "description": "", "is_recipe": False, "ingredients": [], "steps": []}
    return data


# ---- note building / routing ------------------------------------------------

def _yaml_list(values) -> str:
    values = [str(v).strip() for v in (values or []) if str(v).strip()]
    if not values:
        return "[]"
    return "\n" + "\n".join(f"  - {v}" for v in values)


def build_resource_note(item, enr: Enrichment, structured: dict, user_text: str,
                        note_id: str, created: str, now_iso: str, attempts: int) -> str:
    rtype = str(structured.get("resource_type", "article")).lower()
    if rtype not in RESOURCE_TYPES:
        rtype = "article"
    is_recipe = bool(structured.get("is_recipe")) and rtype == "recipe"
    title = str(structured.get("title") or enr.title or "untitled").strip()
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
        f"cover: {enr.cover}",
        f"source_url: {enr.url}",
        f"description: {str(structured.get('description', '')).strip()}",
        "status: inbox",
        f"platform: {enr.platform}",
        f"enriched: {'true' if enr.enriched else 'false'}",
        f"enrich_attempts: {attempts}",
        f"enrich_last: {now_iso}",
        "categories: []",
        "subjects: []",
        "tags: []",
        "---",
    ]
    body = ["\n".join(fm), ""]
    insight = user_text.strip()
    if insight:
        # the user's own words, verbatim — origin human (never overwritten by AI)
        body += ["## Insight", "", insight, ""]
    if is_recipe:
        ing = structured.get("ingredients") or []
        steps = structured.get("steps") or []
        if ing:
            body += ["## Ingredients", ""] + [f"- {i}" for i in ing] + [""]
        if steps:
            body += ["## Steps", ""] + [f"{n}. {s}" for n, s in enumerate(steps, 1)] + [""]
    if enr.transcript:
        body += ["## Transcript", "", enr.transcript, ""]
    elif enr.caption:
        body += ["## Caption", "", enr.caption, ""]
    if not enr.enriched and enr.detail:
        body += ["## Enrichment", "", f"> {enr.detail}", ""]
    return "\n".join(body).rstrip() + "\n"


def route_link(item, user_text: str, enr: Enrichment, structured: dict,
               vault_path: Path, attempts: int = 1) -> Path:
    note_id = item.captured.strftime("%Y%m%d%H%M%S")
    created = item.captured.strftime("%Y-%m-%d")
    now_iso = datetime.now().isoformat(timespec="seconds")
    title = str(structured.get("title") or enr.title or "untitled")
    dest_dir = Path(vault_path) / route.TYPE_FOLDER["resource"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = f"{created}-{route._kebab(title)}"
    path = dest_dir / f"{base}.md"
    i = 1
    while path.exists():
        i += 1
        path = dest_dir / f"{base}-{i}.md"
    path.write_text(build_resource_note(item, enr, structured, user_text,
                                        note_id, created, now_iso, attempts))
    return path


# ---- frontmatter round-trip for retry ---------------------------------------

def _parse_note(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    fm = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, parts[2]


def _insight_from_body(body: str) -> str:
    m = re.search(r"^## Insight\s*\n(.*?)(?:\n## |\Z)", body, re.DOTALL | re.MULTILINE)
    return m.group(1).strip() if m else ""


@dataclass
class _RetryItem:
    captured: datetime
    source: str


def reenrich_note(path: Path, config, fetch=None, router=None) -> bool:
    """Re-attempt enrichment for one resource note. Rewrites it in place,
    bumping enrich_attempts and setting enriched:true on success. Returns the
    new enriched state. Never raises on enrichment failure."""
    fm, body = _parse_note(path.read_text())
    url = fm.get("source_url", "")
    if not url:
        return False
    attempts = int(fm.get("enrich_attempts", "1") or "1") + 1
    user_text = _insight_from_body(body)
    enr = enrich_url(url, config, fetch=fetch)
    structured = structure(user_text, enr, config, router=router)
    item = _RetryItem(
        captured=datetime.strptime(fm.get("created", "2000-01-01"), "%Y-%m-%d")
        if fm.get("created") else datetime.now(),
        source=fm.get("source", "manual"))
    note_id = fm.get("id", item.captured.strftime("%Y%m%d%H%M%S"))
    created = fm.get("created", item.captured.strftime("%Y-%m-%d"))
    now_iso = datetime.now().isoformat(timespec="seconds")
    path.write_text(build_resource_note(item, enr, structured, user_text,
                                        note_id, created, now_iso, attempts))
    return enr.enriched


def retry_pending(config, events, now: datetime | None = None, fetch=None, router=None) -> None:
    """--loop tick: one auto re-attempt for enriched:false notes older than 24h.
    Never raises — enrichment is decoration."""
    try:
        _retry_pending(config, events, now or datetime.now(), fetch, router)
    except Exception:
        import logging
        logging.getLogger("pipeline").exception("enrich retry failed")


def _retry_pending(config, events, now: datetime, fetch, router) -> None:
    folder = Path(config.vault_path) / route.TYPE_FOLDER["resource"]
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*.md")):
        fm, _ = _parse_note(path.read_text())
        if fm.get("type") != "resource" or fm.get("enriched") != "false":
            continue
        if int(fm.get("enrich_attempts", "1") or "1") >= 2:
            continue  # exactly one auto re-attempt
        try:
            last = datetime.fromisoformat(fm.get("enrich_last", ""))
        except ValueError:
            continue
        if (now - last).total_seconds() < 24 * 3600:
            continue
        enriched = reenrich_note(path, config, fetch=fetch, router=router)
        events.log(str(path), "enrich", "ok",
                   message=f"platform={fm.get('platform', '')} enriched={str(enriched).lower()} retry=auto")
