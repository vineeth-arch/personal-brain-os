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
import logging
import os
import re
import shutil
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import llm, route

log = logging.getLogger("pipeline")

HTTP_TIMEOUT = 10
APIFY_TIMEOUT = 60

RESOURCE_TYPES = ["tool", "tutorial", "book", "movie", "recipe", "place", "article"]

_URL_RE = re.compile(r"https?://[^\s<>\"')]+")
_YT_ID_RE = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/|embed/|live/))([\w-]{11})")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\'\r\n]+)["\']',
    re.IGNORECASE)
_TIMEDTEXT_RE = re.compile(r"<text[^>]*>(.*?)</text>", re.IGNORECASE | re.DOTALL)

# Tracking params a share sheet tacks onto an otherwise-identical URL. Anything
# NOT in this list survives normalization — a YouTube ?v= or an Amazon /dp/
# still has to identify the resource, so only params known to carry no
# resource identity get dropped.
_TRACKING_PARAMS = {"igsh", "igshid", "si", "feature", "fbclid", "gclid",
                    "mibextid", "ref", "ref_src", "s"}


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
    # Instagram carousel slides — [{image_url, caption}], capped at 20. Empty
    # for a single post/reel and for every other platform.
    slides: list = field(default_factory=list)


def extract_url(text: str) -> str | None:
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(".,);]") if m else None


def is_link_text(text: str) -> bool:
    return extract_url(text) is not None


def strip_urls(text: str) -> str:
    """The user's words, minus any URL — the link already lives in
    `source_url`, so leaving it in `## Insight` too just crowds out the one
    line of thought the user actually typed. Collapses the blank space a
    removed URL leaves behind; whitespace-only input returns ''. Handles
    every URL in the text (not just the one that made this a link capture,
    D14) — a re-share sometimes carries a second link in the thought itself."""
    without = _URL_RE.sub("", text or "")
    lines = [ln.strip() for ln in without.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def normalize_url(url: str) -> str:
    """A URL stripped of per-share noise, so the SAME reel/video shared twice
    (each carrying its own `?igsh=` or `?si=` tracking id) is recognized as
    one link. Lowercase host, no fragment, no trailing slash, no known
    tracking params — but a resource-identifying param like YouTube's `?v=`
    or an Amazon `/dp/...` path is never touched."""
    parsed = urllib.parse.urlsplit(url.strip())
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if k not in _TRACKING_PARAMS and not k.lower().startswith("utm_")]
    path = parsed.path.rstrip("/") or parsed.path
    return urllib.parse.urlunsplit((
        parsed.scheme.lower(), parsed.netloc.lower(), path,
        urllib.parse.urlencode(query), ""))


# ---- HTTP seam --------------------------------------------------------------

def _default_fetch(url: str, data: bytes | None = None, timeout: int = HTTP_TIMEOUT,
                   headers: dict | None = None) -> bytes:
    """Injectable in tests: fetch(url, data=, timeout=, headers=).
    POST when data is given, else GET."""
    req = urllib.request.Request(
        url, data=data,
        headers={"User-Agent": "Mozilla/5.0 (Brain Cockpit)",
                 **({"Content-Type": "application/json"} if data else {}),
                 **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---- per-platform enrichers -------------------------------------------------

def _unescape(text: str) -> str:
    import html
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _parse_timedtext(xml: str) -> str:
    parts = [_unescape(m.group(1)) for m in _TIMEDTEXT_RE.finditer(xml)]
    return " ".join(p for p in parts if p)[:4000]


# D12: video.google.com/timedtext (the old source of this transcript) is dead
# — it now answers empty for every video, so transcripts were effectively
# always missing. The innertube endpoint below is what the YouTube apps
# themselves call; the public ANDROID client context needs no key and no
# cookies. Best-effort throughout: any failure here — a changed response
# shape, no captions on the video, a network error — degrades to no
# transcript, exactly as the old code did. It never blocks the note.
_INNERTUBE_URL = "https://www.youtube.com/youtubei/v1/player"
_INNERTUBE_CONTEXT = {
    "client": {"clientName": "ANDROID", "clientVersion": "19.09.37", "androidSdkVersion": 30}
}
_PREFERRED_CAPTION_LANGS = ("en", "hi")


def _parse_captions_payload(raw: bytes) -> str:
    """A caption track body, in whichever shape the baseUrl answered with:
    json3 (`&fmt=json3` — an `events[].segs[].utf8` structure) or, when that
    param is ignored, the same XML shape the old timedtext endpoint used."""
    text = raw.decode("utf-8", "ignore")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _parse_timedtext(text)
    parts = []
    for event in data.get("events") or []:
        for seg in event.get("segs") or []:
            if seg.get("utf8"):
                parts.append(seg["utf8"])
    return _unescape("".join(parts))[:4000]


def _fetch_youtube_transcript(vid: str, fetch) -> str:
    if not vid:
        return ""
    try:
        body = json.dumps({"videoId": vid, "context": _INNERTUBE_CONTEXT}).encode()
        data = json.loads(fetch(_INNERTUBE_URL, data=body, timeout=HTTP_TIMEOUT))
        tracks = (((data.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {})
                  .get("captionTracks") or [])
        if not tracks:
            return ""
        track = next((t for t in tracks if t.get("languageCode") in _PREFERRED_CAPTION_LANGS),
                     tracks[0])
        base_url = track.get("baseUrl")
        if not base_url:
            return ""
        raw = fetch(base_url + "&fmt=json3")
        return _parse_captions_payload(raw)
    except Exception:
        return ""   # transcript is optional and often unavailable — never fail on it


def _enrich_youtube(url: str, vid: str, fetch) -> Enrichment:
    try:
        oembed = "https://www.youtube.com/oembed?" + urllib.parse.urlencode(
            {"url": url, "format": "json"})
        data = json.loads(fetch(oembed))
    except Exception:
        return Enrichment("youtube", False, url,
                          detail="YouTube didn't return oEmbed data — the video may be private or removed. The note is saved.")
    return Enrichment("youtube", True, url,
                      title=str(data.get("title", "")),
                      author=str(data.get("author_name", "")),
                      cover=str(data.get("thumbnail_url", "")),
                      transcript=_fetch_youtube_transcript(vid, fetch))


MAX_SLIDES = 20


def _slides_from_item(item: dict) -> list:
    """A carousel's slides, or [] for a single post/reel. Apify's Instagram
    actors don't all agree on the field name for carousel children —
    `childPosts` (the common shape) and `images` (seen on some actor
    versions) are both handled defensively; anything else is treated as "no
    carousel data" rather than raised."""
    slides = []
    child_posts = item.get("childPosts")
    if isinstance(child_posts, list):
        for cp in child_posts[:MAX_SLIDES]:
            if not isinstance(cp, dict):
                continue
            image_url = str(cp.get("displayUrl") or cp.get("thumbnailUrl") or cp.get("url") or "")
            if image_url:
                slides.append({"image_url": image_url, "caption": str(cp.get("caption") or "")})
        if slides:
            return slides
    images = item.get("images")
    if isinstance(images, list):
        for img in images[:MAX_SLIDES]:
            if isinstance(img, str) and img:
                slides.append({"image_url": img, "caption": ""})
            elif isinstance(img, dict):
                image_url = str(img.get("url") or img.get("displayUrl") or "")
                if image_url:
                    slides.append({"image_url": image_url, "caption": str(img.get("caption") or "")})
    return slides[:MAX_SLIDES]


def _slides_text(slides: list) -> str:
    """The '## Slides' body text: a numbered list, image URL + caption when
    one exists. '' for no slides (single post/reel) — the section is omitted
    entirely rather than written empty."""
    lines = []
    for n, slide in enumerate(slides, 1):
        caption = f" — {slide['caption']}" if slide.get("caption") else ""
        lines.append(f"{n}. {slide['image_url']}{caption}")
    return "\n".join(lines)


def _enrich_instagram(url: str, config, fetch) -> Enrichment:
    token = os.environ.get("APIFY_TOKEN")
    actor = (getattr(config, "raw", {}).get("apify") or {}).get("actor_id")
    if not token or not actor:
        return Enrichment("instagram", False, url,
                          detail="Apify isn't configured (APIFY_TOKEN in the environment + apify.actor_id in config.json), so Instagram can't be enriched. The note is saved.")
    try:
        # the token rides in the Authorization header, never the query string —
        # a URL carrying a secret ends up in proxy logs and error reports
        api = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
        body = json.dumps({"directUrls": [url], "resultsLimit": 1}).encode()
        items = json.loads(fetch(api, data=body, timeout=APIFY_TIMEOUT,
                                 headers={"Authorization": f"Bearer {token}"}))
        item = items[0] if isinstance(items, list) and items else {}
        caption = str(item.get("caption") or "")
        cover = str(item.get("displayUrl") or item.get("thumbnailUrl") or "")
        if not (caption or cover):
            raise ValueError("empty payload")
        return Enrichment("instagram", True, url,
                          title=caption[:80] or "instagram-post", caption=caption,
                          cover=cover, author=str(item.get("ownerUsername", "")),
                          slides=_slides_from_item(item))
    except Exception:
        # The broad catch is deliberate — this scraper is ToS-grey and breaks
        # routinely — but it once swallowed a TypeError from a changed call
        # signature and reported a code bug as a normal outage. Log the real
        # reason; keep telling the user the honest, useful version.
        log.info("instagram enrichment failed", exc_info=True)
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


# ---- dedup: the same link shared twice is one note, not two -----------------

def find_by_source_url(vault_path: Path, url: str) -> Path | None:
    """An existing resource note whose source_url normalizes to the same
    place as `url` — checked before writing a new note, so re-sharing a reel
    (each share carrying its own ?igsh=/?si= tracking id) appends a second
    thought instead of duplicating the note."""
    if not url:
        return None
    target = normalize_url(url)
    folder = Path(vault_path) / route.TYPE_FOLDER["resource"]
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = _parse_note(text)
        if fm.get("type") == "resource" and fm.get("source_url") == target:
            return path
    return None


def append_insight(path: Path, new_text: str) -> None:
    """Add one more thought to an already-saved resource note. NEVER
    overwrites what's there — SCHEMA §7 marks insight 'never overwritten by
    AI', and appending a second share's thought is exactly the case that
    guarantee exists for. A blank/URL-only share adds nothing and is a
    silent no-op (the duplicate was still recognized; there's just no new
    thought to record)."""
    addition = strip_urls(new_text)
    if not addition:
        return
    text = path.read_text(encoding="utf-8")
    fm, body = _parse_note(text)
    existing = insight_text(body)
    combined = f"{existing}\n\n{addition}" if existing else addition
    body = _replace_section(body, "Insight", combined)
    fm_block = text.split("---\n", 2)[1]
    path.write_text("---\n" + fm_block.rstrip("\n") + "\n---\n\n" + body.strip() + "\n",
                    encoding="utf-8")


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
    if enr.slides:
        slide_text = " ".join(s.get("caption", "") for s in enr.slides if s.get("caption"))
        if slide_text:
            ctx.append(f"Slides: {slide_text[:1500]}")
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
        f"title: {route._scalar(title)}",
        f"cover: {route._scalar(enr.cover)}",
        f"source_url: {route._scalar(normalize_url(enr.url) if enr.url else enr.url)}",
        f"description: {route._scalar(structured.get('description', ''))}",
        "status: inbox",
        f"platform: {route._scalar(enr.platform)}",
        f"enriched: {'true' if enr.enriched else 'false'}",
        f"enrich_attempts: {attempts}",
        f"enrich_last: {now_iso}",
        "categories: []",
        "subjects: []",
        "tags: []",
        "---",
    ]
    body = ["\n".join(fm), ""]
    insight = strip_urls(user_text)
    if insight:
        # the user's own words, verbatim minus the link itself (which already
        # lives in source_url) — origin human, never overwritten by AI
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
    slides_text = _slides_text(enr.slides)
    if slides_text:
        body += ["## Slides", "", slides_text, ""]
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
                                        note_id, created, now_iso, attempts), encoding="utf-8")
    return path


# ---- image capture (Pass V2/V3) ---------------------------------------------
# A photo is media, not something to classify — no LLM classify call, no
# review gate. It's either a resource note (default, D-PHOTO) or, when the
# user attached a capture tag, that type's note instead. Vision description
# is best-effort decoration on top, same as link enrichment (Pass L).

IMAGE_INSIGHT_SUFFIX = ".insight"  # must match api/notes.INSIGHT_SIDECAR_SUFFIX


def take_image_insight(image_path: Path) -> str:
    """Read + delete the '.<stem>.insight' dotfile the capture endpoint
    writes BEFORE the image itself lands (api/notes.image_insight_sidecar),
    so by the time the watcher gets to the image any sidecar is already
    here — no race, no ordering dependency. '' when the photo was shared
    with no quick thought."""
    sidecar = image_path.with_name(f".{image_path.stem}{IMAGE_INSIGHT_SUFFIX}")
    if not sidecar.is_file():
        return ""
    try:
        return sidecar.read_text(encoding="utf-8").strip()
    finally:
        sidecar.unlink(missing_ok=True)


def move_image_to_vault(item, vault_path: Path) -> Path:
    """Move a captured photo out of the inbox into the vault's own
    attachments/ store — its permanent home, alongside raw/ and wiki/ outside
    the numbered folders (SCHEMA §1). Unlike audio/text, an image IS vault
    content once captured (it's embedded straight into its note), so this
    replaces the generic Stage 6 archive step rather than running beside it."""
    folder = Path(vault_path) / "attachments"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = item.captured.strftime("%Y%m%d%H%M%S")
    slug = route._kebab(item.name) if item.name else "photo"
    dest = folder / f"{stamp}-{slug}{item.path.suffix.lower()}"
    i = 1
    while dest.exists():
        i += 1
        dest = folder / f"{stamp}-{slug}-{i}{item.path.suffix.lower()}"
    shutil.move(str(item.path), str(dest))
    return dest


def build_image_note(item, vision: dict | None, insight: str, attachment_rel: str,
                     note_id: str, created: str, now_iso: str, attempts: int) -> str:
    """No capture tag: a resource note like any other share, platform: photo
    instead of youtube/instagram/web (D-PHOTO default)."""
    description = str((vision or {}).get("description") or "").strip()
    rtype = str((vision or {}).get("resource_type") or "").lower()
    if rtype not in RESOURCE_TYPES:
        rtype = "article"
    title = (insight.splitlines()[0].strip() if insight else "") or description or "photo"
    extracted = str((vision or {}).get("extracted_text") or "").strip()
    fm = [
        "---",
        f"id: {note_id}",
        "type: resource",
        f"resource_type: {rtype}",
        f"created: {created}",
        f"source: {item.source}",
        "origin: human",
        "meta_origin: ai",
        f"title: {route._scalar(title)}",
        f"cover: {route._scalar(attachment_rel)}",
        "source_url: ",
        f"description: {route._scalar(description)}",
        "status: inbox",
        "platform: photo",
        f"enriched: {'true' if vision else 'false'}",
        f"enrich_attempts: {attempts}",
        f"enrich_last: {now_iso}",
        "categories: []",
        "subjects: []",
        "tags: []",
        "---",
    ]
    body = ["\n".join(fm), ""]
    if insight:
        # the user's own quick thought, verbatim — origin human, same
        # guarantee as a link's ## Insight
        body += ["## Insight", "", insight, ""]
    body += [f"![[{attachment_rel}]]", ""]
    if extracted:
        body += ["## Extracted text", "", extracted, ""]
    if not vision:
        body += ["## Enrichment", "",
                 "> The photo couldn't be described automatically (no vision "
                 "provider configured, or the attempt failed). The note is "
                 "saved; the photo is still here.", ""]
    return "\n".join(body).rstrip() + "\n"


def route_image(item, vision: dict | None, insight: str, attachment_rel: str,
                vault_path: Path, attempts: int = 1) -> Path:
    note_id = item.captured.strftime("%Y%m%d%H%M%S")
    created = item.captured.strftime("%Y-%m-%d")
    now_iso = datetime.now().isoformat(timespec="seconds")
    title = (insight.splitlines()[0].strip() if insight else "") or \
            str((vision or {}).get("description") or "").strip() or "photo"
    dest_dir = Path(vault_path) / route.TYPE_FOLDER["resource"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = f"{created}-{route._kebab(title)}"
    path = dest_dir / f"{base}.md"
    i = 1
    while path.exists():
        i += 1
        path = dest_dir / f"{base}-{i}.md"
    path.write_text(build_image_note(item, vision, insight, attachment_rel,
                                     note_id, created, now_iso, attempts), encoding="utf-8")
    return path


def build_tagged_image_note(item, cls, vision: dict | None, insight: str,
                            attachment_rel: str, note_id: str, created: str) -> str:
    """A photo captured WITH a capture tag: filed as that type's note instead
    of a resource (D-PHOTO 'Both') — universal frontmatter only (SCHEMA §2),
    same as every other tag-routed capture. The vision description is
    AI-written, so it lives under its own heading rather than folding into
    the human's own words — mirroring how enrichment-owned sections
    (## Transcript, ## Caption) stay apart from ## Insight elsewhere here."""
    fm = [
        "---",
        f"id: {note_id}",
        f"type: {cls.type}",
        f"created: {created}",
        "source: share",
        "origin: human",
        "meta_origin: human",
        f"status: {route.STATUS_INITIAL.get(cls.type, 'active')}",
        f"categories: {route._yaml_links([])}",
        f"subjects: {route._yaml_links([])}",
        f"tags: {route._yaml_list(cls.tags)}",
        "---",
    ]
    body = ["\n".join(fm), ""]
    if insight:
        body += [insight, ""]
    body += [f"![[{attachment_rel}]]", ""]
    description = (vision or {}).get("description")
    if description:
        body += ["## AI description", "", description, ""]
    extracted = (vision or {}).get("extracted_text")
    if extracted:
        body += ["## Extracted text", "", extracted, ""]
    return "\n".join(body).rstrip() + "\n"


def route_tagged_image(item, cls, vision: dict | None, insight: str,
                       attachment_rel: str, vault_path: Path) -> Path:
    note_id = item.captured.strftime("%Y%m%d%H%M%S")
    created = item.captured.strftime("%Y-%m-%d")
    dest_dir = Path(vault_path) / route.TYPE_FOLDER[cls.type]
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = f"{created}-{route._kebab(cls.title)}"
    path = dest_dir / f"{base}.md"
    i = 1
    while path.exists():
        i += 1
        path = dest_dir / f"{base}-{i}.md"
    path.write_text(build_tagged_image_note(item, cls, vision, insight, attachment_rel,
                                            note_id, created), encoding="utf-8")
    return path


def reenrich_image_note(path: Path, config, caller=None) -> bool:
    """Re-attempt vision for one photo resource note (platform: photo),
    merging the result in exactly like reenrich_note does for links — only
    the vision-owned fields/sections are touched; status, rating,
    categories/subjects/tags, and ## Insight are the user's and stay put.
    False (no change) when the note has no cover to describe."""
    from . import vision as vision_mod  # local import: vision imports nothing from enrich

    text = path.read_text(encoding="utf-8")
    fm, body = _parse_note(text)
    cover = fm.get("cover", "")
    if not cover:
        return False
    vault_path = path.parents[1]   # <vault>/04-Resources/<note>.md
    attempts = int(fm.get("enrich_attempts", "1") or "1") + 1
    result = vision_mod.describe(vault_path / cover, config, caller=caller)

    fm_block = text.split("---\n", 2)[1]
    updates = {
        "description": (result or {}).get("description", "") or fm.get("description", ""),
        "resource_type": (result or {}).get("resource_type", "") or fm.get("resource_type", "article"),
        "enriched": "true" if result else "false",
        "enrich_attempts": str(attempts),
        "enrich_last": datetime.now().isoformat(timespec="seconds"),
    }
    for key in ("description", "resource_type", "enriched", "enrich_attempts", "enrich_last"):
        fm_block = route.stamp_field(fm_block, key, route._scalar(updates[key]))

    body = _replace_section(body, "Extracted text", (result or {}).get("extracted_text", ""))
    body = _replace_section(
        body, "Enrichment",
        "" if result else "> The photo couldn't be described automatically (no vision "
                          "provider configured, or the attempt failed). The note is "
                          "saved; the photo is still here.")

    path.write_text("---\n" + fm_block.rstrip("\n") + "\n---\n\n" + body.strip() + "\n",
                    encoding="utf-8")
    return bool(result)


# ---- frontmatter round-trip for retry ---------------------------------------

def _unquote(value: str) -> str:
    """Give back the string, not the quoting (see api/notes._unquote)."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


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
            fm[k.strip()] = _unquote(v.strip())
    return fm, parts[2]


# D17: the canonical '## Insight' reader — api/notes.py imports this rather
# than keeping its own copy (two parsers with different case-sensitivity was
# an easy way for them to quietly drift apart).
def insight_text(body: str) -> str:
    """Text under a '## Insight' H2, up to the next H2 or EOF. '' when absent/blank."""
    out: list[str] = []
    capturing = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.lower() == "## insight":
            capturing = True
            continue
        if capturing and stripped.startswith("## "):
            break
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


# The only frontmatter fields enrichment owns. Everything else on a resource
# note — status, rating, categories, subjects, tags, consumed, sample, and any
# field a human added — belongs to the user and is never touched here.
ENRICHED_FIELDS = ("title", "cover", "description", "platform",
                   "enriched", "enrich_attempts", "enrich_last")

# Likewise the only body sections enrichment owns.
ENRICHED_SECTIONS = ("Transcript", "Caption", "Slides", "Enrichment")


def _replace_section(body: str, heading: str, text: str) -> str:
    """Set (or drop, when text is empty) one '## heading' section, leaving every
    other section — the user's ## Insight, their own notes — exactly as it is."""
    kept, skipping = [], False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped == f"## {heading}":
            skipping = True
            continue
        if skipping and stripped.startswith("## "):
            skipping = False
        if not skipping:
            kept.append(line)
    base = "\n".join(kept).strip()
    if not text.strip():
        return base
    block = f"## {heading}\n\n{text.strip()}"
    return f"{base}\n\n{block}" if base else block


def reenrich_note(path: Path, config, fetch=None, router=None) -> bool:
    """Re-attempt enrichment for one resource note, MERGING the result in.

    It used to rebuild the note from scratch, which reset status to `inbox`,
    blanked categories/subjects/tags, and dropped every body section except
    ## Insight. retry_pending calls this automatically from the watcher loop, so
    a resource the user had read, rated and tagged was quietly returned to the
    inbox 24h later. The vault is the source of truth (CLAUDE.md §1): enrichment
    may only write the fields and sections it owns.

    Returns the new enriched state. Never raises on enrichment failure.
    """
    from . import route  # local import: route imports nothing from enrich

    text = path.read_text(encoding="utf-8")
    fm, body = _parse_note(text)
    url = fm.get("source_url", "")
    if not url:
        return False
    attempts = int(fm.get("enrich_attempts", "1") or "1") + 1
    user_text = insight_text(body)
    enr = enrich_url(url, config, fetch=fetch)
    structured = structure(user_text, enr, config, router=router)

    fm_block = text.split("---\n", 2)[1]
    updates = {
        "title": str(structured.get("title") or enr.title or fm.get("title") or "untitled"),
        "cover": enr.cover or fm.get("cover", ""),
        "description": str(structured.get("description", "")).strip() or fm.get("description", ""),
        "platform": enr.platform,
        "enriched": "true" if enr.enriched else "false",
        "enrich_attempts": str(attempts),
        "enrich_last": datetime.now().isoformat(timespec="seconds"),
    }
    for key in ENRICHED_FIELDS:
        fm_block = route.stamp_field(fm_block, key, route._scalar(updates[key]))

    body = _replace_section(body, "Transcript", enr.transcript)
    body = _replace_section(body, "Caption", "" if enr.transcript else enr.caption)
    body = _replace_section(body, "Slides", _slides_text(enr.slides))
    body = _replace_section(
        body, "Enrichment", f"> {enr.detail}" if (not enr.enriched and enr.detail) else "")

    path.write_text("---\n" + fm_block.rstrip("\n") + "\n---\n\n" + body.strip() + "\n",
                    encoding="utf-8")
    return enr.enriched


def retry_pending(config, events, now: datetime | None = None, fetch=None, router=None,
                  vision_caller=None) -> None:
    """--loop tick: one auto re-attempt for enriched:false notes older than 24h.
    Never raises — enrichment (link or vision) is decoration."""
    try:
        _retry_pending(config, events, now or datetime.now(), fetch, router, vision_caller)
    except Exception:
        import logging
        logging.getLogger("pipeline").exception("enrich retry failed")


# Apify is the one integration this app can't verify before the fact — a
# note can fail its guaranteed re-attempt for no better reason than "the
# owner hadn't configured Apify yet". A hard stop at attempt 2 would then
# strand that note unenriched forever, even after they set APIFY_TOKEN. So an
# Instagram note gets extra tries, but only while Apify is now actually
# configured (never an unbounded retry loop), and only up to this cap.
MAX_ENRICH_ATTEMPTS = 4


def _apify_configured(config) -> bool:
    return bool(os.environ.get("APIFY_TOKEN")) and bool(
        (getattr(config, "raw", {}).get("apify") or {}).get("actor_id"))


def _retry_pending(config, events, now: datetime, fetch, router, vision_caller=None) -> None:
    folder = Path(config.vault_path) / route.TYPE_FOLDER["resource"]
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*.md")):
        fm, _ = _parse_note(path.read_text(encoding="utf-8"))
        if fm.get("type") != "resource" or fm.get("enriched") != "false":
            continue
        attempts = int(fm.get("enrich_attempts", "1") or "1")
        if attempts >= MAX_ENRICH_ATTEMPTS:
            continue
        if attempts >= 2 and not (fm.get("platform") == "instagram" and _apify_configured(config)):
            continue  # past the guaranteed one re-attempt, and no reason to think this one differs
        try:
            last = datetime.fromisoformat(fm.get("enrich_last", ""))
        except ValueError:
            continue
        if (now - last).total_seconds() < 24 * 3600:
            continue
        if fm.get("platform") == "photo":
            enriched = reenrich_image_note(path, config, caller=vision_caller)
        else:
            enriched = reenrich_note(path, config, fetch=fetch, router=router)
        events.log(str(path), "enrich", "ok",
                   message=f"platform={fm.get('platform', '')} enriched={str(enriched).lower()} retry=auto")
