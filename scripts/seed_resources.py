#!/usr/bin/env python3
"""Seed ~36 schema-correct sample resource notes into <vault>/04-Resources/.

Pass 6. These are believable placeholder resources for a designer-founder so the
Resource OS dashboard has real variance to work with (categories, statuses,
insight vs none, a spread of ages). Every note carries three seed markers on top
of the SCHEMA-REFERENCE.md §2/§7 frontmatter:

    sample: true            the ONLY thing the purge endpoint may target
    source: seed            an intentional seed-only marker (not a §4 source value)
    cover: https://picsum.photos/seed/<slug>/400/560   placeholder cover

Insight, when present, is a body '## Insight' section (not the schema frontmatter
field) — see the Pass 6 plan. The deep-link-out lives in the schema's
`source_url` field; the API exposes it to the UI as `url`.

Usage:
    python scripts/seed_resources.py                 # vault from repo config.json
    python scripts/seed_resources.py --vault /path   # explicit vault (testing)
    python scripts/seed_resources.py --config /p.json # explicit config

Idempotent: a second run writes nothing (entries whose title already exists as a
sample note are skipped). Commits the vault "pre-seed" before writing and
"seed: 36 sample resources" after.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Run as a plain script (`python scripts/seed_resources.py`) — make the repo
# root importable so we can reuse the pipeline config loader and the vault git
# helper instead of re-implementing them.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from api.notes import git_commit_vault  # noqa: E402
from pipeline import config as config_mod  # noqa: E402
from pipeline.route import TYPE_FOLDER  # noqa: E402

RESOURCES_FOLDER = TYPE_FOLDER["resource"]  # "04-Resources"

# ---- the seed set -----------------------------------------------------------
# (title, resource_type, status, days_ago, description, url, extra, insight)
# resource_type ∈ SCHEMA §7 vocab (tool tutorial book movie recipe place
# article); the brief's "Video" flavor maps to `tutorial` (design talks / YT).
# days_ago is staggered so some are <1d, <1w, <1m, and older (up to 90d).
# extra: type-specific frontmatter (author / where_to_watch / etc.).
_SEED: list[dict] = [
    # --- Books (7) ---
    dict(title="Refactoring UI", rt="book", status="referenced", days=64,
         desc="Practical visual design tactics for developers.",
         url="https://www.refactoringui.com/", extra={"author": "Adam Wathan & Steve Schoger"},
         insight="The single biggest lever is spacing — generous, consistent whitespace reads as premium before a single colour choice matters."),
    dict(title="The Brand Gap", rt="book", status="consumed", days=51,
         desc="Neumeier's bridge between business strategy and design.",
         url="https://www.marty-neumeier.com/the-brand-gap", extra={"author": "Marty Neumeier"},
         insight="A brand isn't the logo or the palette — it's the gut feeling people carry, so I should design for the feeling and let the marks follow."),
    dict(title="Grid Systems in Graphic Design", rt="book", status="to-consume", days=12,
         desc="Müller-Brockmann's canonical Swiss grid manual.",
         url="https://www.niggli.ch/en/grid-systems-in-graphic-design.html",
         extra={"author": "Josef Müller-Brockmann"}, insight=None),
    dict(title="Hooked", rt="book", status="consumed", days=80,
         desc="How habit-forming products are built.",
         url="https://www.nirandfar.com/hooked/", extra={"author": "Nir Eyal"},
         insight="Trigger → action → variable reward → investment. The investment step is the one most products skip, and it's what earns the next trigger."),
    dict(title="Thinking with Type", rt="book", status="to-consume", days=6,
         desc="Lupton's field guide to typography.",
         url="https://thinkingwithtype.com/", extra={"author": "Ellen Lupton"}, insight=None),
    dict(title="Change by Design", rt="book", status="inbox", days=2,
         desc="Tim Brown on design thinking at IDEO.",
         url="https://www.ideo.com/", extra={"author": "Tim Brown"}, insight=None),
    dict(title="Designing Brand Identity", rt="book", status="referenced", days=73,
         desc="Wheeler's end-to-end branding process reference.",
         url="https://www.wiley.com/en-us/Designing+Brand+Identity", extra={"author": "Alina Wheeler"},
         insight="Their five-phase process (research → clarify → design → touchpoints → manage) is the checklist I keep reaching for on client kickoffs."),

    # --- Movies (5) ---
    dict(title="Blade Runner 2049", rt="movie", status="consumed", days=40,
         desc="Villeneuve's visual-design masterclass.",
         url="https://www.imdb.com/title/tt1856101/",
         extra={"where_to_watch": "Netflix", "runtime": "164 min"},
         insight="Every frame is one dominant hue with a single warm light source — a live demo of the one-accent rule I keep preaching."),
    dict(title="Helvetica", rt="movie", status="referenced", days=88,
         desc="Hustwit's documentary on the typeface and modernism.",
         url="https://www.imdb.com/title/tt0847817/",
         extra={"where_to_watch": "YouTube", "runtime": "80 min"}, insight=None),
    dict(title="Abstract: The Art of Design", rt="movie", status="to-consume", days=9,
         desc="Netflix series profiling working designers.",
         url="https://www.imdb.com/title/tt6337006/",
         extra={"where_to_watch": "Netflix", "runtime": "8 × 45 min"}, insight=None),
    dict(title="Jiro Dreams of Sushi", rt="movie", status="consumed", days=55,
         desc="A study in obsessive craft and refinement.",
         url="https://www.imdb.com/title/tt1772925/",
         extra={"where_to_watch": "Prime Video", "runtime": "81 min"},
         insight="Mastery is subtraction — Jiro's whole menu is a handful of things done relentlessly, which is exactly how I should scope a product."),
    dict(title="The Social Network", rt="movie", status="inbox", days=1,
         desc="Fincher/Sorkin on the founding of Facebook.",
         url="https://www.imdb.com/title/tt1285016/",
         extra={"where_to_watch": "Prime Video", "runtime": "120 min"}, insight=None),

    # --- Tools (6) ---
    dict(title="Figma", rt="tool", status="referenced", days=70,
         desc="Collaborative interface design tool.",
         url="https://www.figma.com/", extra={}, insight=None),
    dict(title="Linear", rt="tool", status="consumed", days=33,
         desc="Keyboard-first issue tracking for product teams.",
         url="https://linear.app/", extra={},
         insight="Its opinionated defaults are the feature — I move faster because there are fewer knobs, and I want that discipline in my own tools."),
    dict(title="Framer", rt="tool", status="to-consume", days=11,
         desc="Design-to-site with real interactions.",
         url="https://www.framer.com/", extra={}, insight=None),
    dict(title="Raycast", rt="tool", status="consumed", days=46,
         desc="Extensible macOS launcher and command bar.",
         url="https://www.raycast.com/", extra={},
         insight="Replacing five menu-bar apps with one launcher cut my context-switching noticeably — worth building capture flows around."),
    dict(title="Vercel", rt="tool", status="inbox", days=3,
         desc="Frontend deploy and hosting platform.",
         url="https://vercel.com/", extra={}, insight=None),
    dict(title="Obsidian", rt="tool", status="referenced", days=90,
         desc="Local-first markdown knowledge base.",
         url="https://obsidian.md/", extra={},
         insight="Plain files I own beat any database — this whole brain-OS bet rides on the vault staying just markdown on disk."),

    # --- Articles (5) ---
    dict(title="Designing for the Subconscious", rt="article", status="to-consume", days=7,
         desc="How pre-attentive cues shape first impressions.",
         url="https://alistapart.com/article/designing-for-the-subconscious/", extra={}, insight=None),
    dict(title="The Case Against Design Thinking", rt="article", status="consumed", days=61,
         desc="A critique of design thinking as a process fad.",
         url="https://www.fastcompany.com/the-case-against-design-thinking", extra={},
         insight="The critique lands: process theatre is not craft. Ship real artefacts, don't run more sticky-note workshops."),
    dict(title="Airbnb's Design Language System", rt="article", status="referenced", days=77,
         desc="How Airbnb unified its product surfaces.",
         url="https://airbnb.design/building-a-visual-language/", extra={}, insight=None),
    dict(title="How Stripe Designs Beautiful Websites", rt="article", status="consumed", days=29,
         desc="A teardown of Stripe's marketing craft.",
         url="https://medium.com/@steveschoger/how-stripe-designs", extra={},
         insight="The gradient-under-glass depth trick is just layered low-opacity fills — cheap to copy, expensive-looking."),
    dict(title="The Math Behind Good Spacing", rt="article", status="inbox", days=4,
         desc="Modular scales and spacing systems, explained.",
         url="https://www.designsystems.com/space-grids-and-layouts/", extra={}, insight=None),

    # --- Recipes (5) ---
    dict(title="Bombay Masala Chai", rt="recipe", status="referenced", days=58,
         desc="Stovetop cutting chai with fresh ginger and cardamom.",
         url="https://www.cookwithmanali.com/masala-chai/",
         extra={"ingredients": "tea, milk, ginger, cardamom, sugar", "steps": "boil, spice, simmer, strain"},
         insight="Bruising the ginger and cardamom before boiling, not after, is the whole difference — five extra seconds, twice the aroma."),
    dict(title="Butter Chicken from Scratch", rt="recipe", status="to-consume", days=14,
         desc="Restaurant-style murgh makhani at home.",
         url="https://www.recipetineats.com/butter-chicken/",
         extra={"ingredients": "chicken, tomato, cream, butter, garam masala",
                "steps": "marinate, grill, simmer sauce, combine"}, insight=None),
    dict(title="Shakshuka", rt="recipe", status="consumed", days=36,
         desc="Eggs poached in spiced tomato and pepper.",
         url="https://cooking.nytimes.com/recipes/shakshuka",
         extra={"ingredients": "eggs, tomato, peppers, cumin, feta",
                "steps": "soften, simmer, nest eggs, cover"},
         insight="A one-pan dish that plates like a restaurant course — my new default for when someone drops by unannounced."),
    dict(title="Hyderabadi Biryani", rt="recipe", status="inbox", days=5,
         desc="Layered dum biryani with saffron and fried onions.",
         url="https://www.cookwithmanali.com/hyderabadi-biryani/",
         extra={"ingredients": "basmati, mutton, yoghurt, saffron, onions",
                "steps": "marinate, par-cook rice, layer, dum"}, insight=None),
    dict(title="Karak Chai, Dubai Style", rt="recipe", status="to-consume", days=18,
         desc="Strong, sweet, evaporated-milk cafeteria chai.",
         url="https://www.arabianbusiness.com/karak-chai-recipe",
         extra={"ingredients": "tea, evaporated milk, cardamom, sugar",
                "steps": "boil hard, sweeten, froth, pour"}, insight=None),

    # --- Places (5) ---
    dict(title="Kala Ghoda Arts District, Mumbai", rt="place", status="referenced", days=84,
         desc="Gallery and café quarter in South Mumbai.",
         url="https://maps.google.com/?q=Kala+Ghoda+Mumbai",
         extra={"map_url": "https://maps.google.com/?q=Kala+Ghoda+Mumbai", "best_time": "Weekend mornings"},
         insight="The annual Kala Ghoda festival is the best local pulse-check on where Indian visual culture is heading — worth timing a trip around."),
    dict(title="Alserkal Avenue, Dubai", rt="place", status="to-consume", days=16,
         desc="Industrial-district arts and design complex.",
         url="https://maps.google.com/?q=Alserkal+Avenue+Dubai",
         extra={"map_url": "https://maps.google.com/?q=Alserkal+Avenue+Dubai", "best_time": "Thursday evenings"},
         insight=None),
    dict(title="Prithvi Theatre Café, Mumbai", rt="place", status="consumed", days=42,
         desc="Irani-café courtyard beside the theatre in Juhu.",
         url="https://maps.google.com/?q=Prithvi+Theatre+Mumbai",
         extra={"map_url": "https://maps.google.com/?q=Prithvi+Theatre+Mumbai", "best_time": "Pre-show, 6pm"},
         insight=None),
    dict(title="The Coffee Museum, Dubai", rt="place", status="inbox", days=8,
         desc="Tiny museum-café in the Al Fahidi quarter.",
         url="https://maps.google.com/?q=Coffee+Museum+Dubai",
         extra={"map_url": "https://maps.google.com/?q=Coffee+Museum+Dubai", "best_time": "Weekday afternoons"},
         insight=None),
    dict(title="Leopold Café, Mumbai", rt="place", status="archived", days=86,
         desc="Colaba institution, open since 1871.",
         url="https://maps.google.com/?q=Leopold+Cafe+Mumbai",
         extra={"map_url": "https://maps.google.com/?q=Leopold+Cafe+Mumbai", "best_time": "Late lunch"},
         insight=None),

    # --- Tutorials / talks (the "Video" flavor) (3) ---
    dict(title="The Design of Everyday Things — Norman Talk", rt="tutorial", status="consumed", days=47,
         desc="Don Norman on affordances and signifiers.",
         url="https://www.youtube.com/watch?v=NK1Zb_5VxuM",
         extra={"tools_mentioned": "affordances, signifiers, feedback"},
         insight="Signifiers, not affordances, are what users actually perceive — so my job is making the right action look obvious, not just possible."),
    dict(title="Stop Designing Products — Julie Zhuo", rt="tutorial", status="to-consume", days=13,
         desc="On designing for outcomes over artefacts.",
         url="https://www.youtube.com/watch?v=Zhuo-designing",
         extra={"tools_mentioned": "outcomes, north-star metrics"}, insight=None),
    dict(title="Advanced Figma Auto Layout", rt="tutorial", status="inbox", days=0,
         desc="Deep dive into responsive auto-layout patterns.",
         url="https://www.youtube.com/watch?v=figma-autolayout",
         extra={"tools_mentioned": "auto layout, constraints, variants"}, insight=None),
]


def _slug(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title.lower()).strip()
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug[:60] or "resource"


def _existing_sample_titles(folder: Path) -> set[str]:
    """Titles of resource notes already carrying sample: true — the idempotency
    key (robust to date drift across runs)."""
    titles: set[str] = set()
    if not folder.is_dir():
        return titles
    for path in folder.glob("*.md"):
        text = path.read_text()
        if not text.startswith("---\n"):
            continue
        block = text.split("---\n", 2)
        if len(block) < 3:
            continue
        fm = {}
        for line in block[1].splitlines():
            if ":" in line and not line.startswith((" ", "\t")):
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip()
        if fm.get("type") == "resource" and fm.get("sample", "").lower() == "true":
            titles.add(fm.get("title", ""))
    return titles


def _build_note(entry: dict, when: datetime) -> str:
    note_id = when.strftime("%Y%m%d%H%M%S")
    created = when.strftime("%Y-%m-%d")
    slug = _slug(entry["title"])
    lines = [
        "---",
        f"id: {note_id}",
        "type: resource",
        f"created: {created}",
        "source: seed",
        "origin: human",
        f"status: {entry['status']}",
        f"resource_type: {entry['rt']}",
        f"title: {entry['title']}",
        f"description: {entry['desc']}",
        f"cover: https://picsum.photos/seed/{slug}/400/560",
        f"source_url: {entry['url']}",
        f"captured: {created}",
    ]
    if entry["status"] == "consumed":
        lines.append(f"consumed: {created}")
    for key, value in entry.get("extra", {}).items():
        lines.append(f"{key}: {value}")
    lines += ["sample: true", "categories: []", "subjects: []", "tags: []", "---", ""]

    body = [entry["desc"]]
    if entry.get("insight"):
        body += ["", "## Insight", entry["insight"]]
    return "\n".join(lines) + "\n".join(body) + "\n"


def seed(vault_path: Path, *, commit: bool = True, today: date | None = None) -> list[dict]:
    """Write the seed set into <vault>/04-Resources. Returns a summary row per
    note actually written (skipped duplicates are omitted). Idempotent."""
    vault_path = Path(vault_path)
    folder = vault_path / RESOURCES_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    today = today or date.today()
    existing = _existing_sample_titles(folder)

    to_write = [e for e in _SEED if e["title"] not in existing]
    if not to_write:
        print(f"All {len(_SEED)} sample resources already present in "
              f"{RESOURCES_FOLDER} — nothing to write.")
        return []

    if commit:
        git_commit_vault(vault_path, "pre-seed")

    written: list[dict] = []
    for i, entry in enumerate(to_write):
        # midday minus the entry's age, plus a per-index second so ids are
        # unique even when two notes share a created date.
        when = datetime.combine(today, datetime.min.time()).replace(hour=12) \
            - timedelta(days=entry["days"]) + timedelta(seconds=i)
        path = folder / f"{when.strftime('%Y-%m-%d')}-{_slug(entry['title'])}.md"
        n = 1
        while path.exists():
            n += 1
            path = folder / f"{when.strftime('%Y-%m-%d')}-{_slug(entry['title'])}-{n}.md"
        path.write_text(_build_note(entry, when))
        written.append({
            "title": entry["title"], "type": entry["rt"], "status": entry["status"],
            "insight": bool(entry.get("insight")), "file": path.name,
        })

    if commit:
        git_commit_vault(vault_path, f"seed: {len(_SEED)} sample resources")

    return written


def _print_summary(written: list[dict]) -> None:
    if not written:
        return
    print(f"\nSeeded {len(written)} resource notes:\n")
    print(f"  {'TITLE':<40}{'TYPE':<10}{'STATUS':<12}{'INSIGHT'}")
    print(f"  {'-' * 38:<40}{'-' * 8:<10}{'-' * 10:<12}{'-' * 7}")
    for row in written:
        mark = "●" if row["insight"] else "·"
        print(f"  {row['title'][:38]:<40}{row['type']:<10}{row['status']:<12}{mark}")
    n_insight = sum(1 for r in written if r["insight"])
    print(f"\n  {len(written)} notes · {n_insight} with an insight.")


def _resolve_vault(args) -> Path:
    if args.vault:
        return Path(args.vault).expanduser()
    config_path = Path(args.config).expanduser() if args.config else _REPO_ROOT / "config.json"
    if not config_path.exists():
        raise SystemExit(
            f"No vault given and {config_path} doesn't exist.\n"
            "Pass --vault /path/to/vault, or create config.json (copy "
            "config.example.json) with a vault_path.")
    return config_mod.load(config_path).vault_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed sample resource notes into the vault.")
    parser.add_argument("--vault", help="Vault path (overrides config.json)")
    parser.add_argument("--config", help="Path to config.json (default: repo root)")
    parser.add_argument("--no-commit", action="store_true", help="Skip the git commits")
    args = parser.parse_args(argv)

    vault_path = _resolve_vault(args)
    if not vault_path.is_dir():
        raise SystemExit(f"Vault path {vault_path} isn't a directory.")

    written = seed(vault_path, commit=not args.no_commit)
    _print_summary(written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
