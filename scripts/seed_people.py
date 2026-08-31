#!/usr/bin/env python3
"""Seed sample person notes into <vault>/07-People/ so the Relationship OS has
something to show before your real people are in the vault.

Pass MW. Every note is schema-correct (SCHEMA-REFERENCE.md §7) and carries the
same seed markers Pass 6 uses for resources:

    sample: true      the ONLY thing a purge may target
    origin: human     these stand in for notes you would have written

The spread is deliberate: someone badly overdue, someone mid-warm-up, someone
you owe a step, and someone perfectly in touch — so "Needs you", "Warm-up due"
and the quiet empty state can all be seen working.

Usage:
    python scripts/seed_people.py                  # vault from repo config.json
    python scripts/seed_people.py --vault /path    # explicit vault (testing)
    python scripts/seed_people.py --purge          # remove sample: true people

Idempotent: a second run writes nothing. Commits the vault before and after.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# (name, relationship, company, stage, cadence, days since last contact, next action)
PEOPLE = [
    ("Priya Raman", "client", "Alserkal Avenue", "conversing", 3, 24,
     "Send the studio deck today"),
    ("Omar Haddad", "prospect", "Tashkeel", "researched", 5, 12, ""),
    ("Lena Fischer", "collaborator", "Studio Fischer", "engaging", 4, 9,
     "Follow up on the residency by 2026-09-30"),
    ("Rahul Menon", "friend", "", "warm", 21, 30, ""),
    ("Aisha Noor", "prospect", "Dubai Design District", "identified", 7, None, ""),
    ("Tomás Ferreira", "client", "Casa Ferreira", "ready", 14, 2, ""),
]

CONTEXT = {
    "Priya Raman": "Met at a studio visit in Alserkal. Runs the artist programme; "
                   "decides on studio partnerships herself.",
    "Omar Haddad": "Introduced over email by Lena. Looking for a brand identity "
                   "for Tashkeel's 2027 season.",
    "Lena Fischer": "Ceramicist. We keep circling a joint residency application.",
    "Rahul Menon": "Oldest friend from Bangalore. No agenda — just don't let a "
                   "year go by again.",
    "Aisha Noor": "Programme lead at d3. Found via the d3 newsletter; no contact yet.",
    "Tomás Ferreira": "Signed for the Casa Ferreira rebrand. Kickoff done, "
                      "deliverables running.",
}

NEEDS = {
    "Priya Raman": "A studio partner who can hold a full season, not a one-off.",
    "Omar Haddad": "An identity system his small team can run without a designer.",
    "Lena Fischer": "A collaborator for the residency application deadline.",
    "Rahul Menon": "Nothing. This one is not a pipeline.",
    "Aisha Noor": "Unknown — that's what the first conversation is for.",
    "Tomás Ferreira": "Weekly clarity on what lands when.",
}


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def _note(name: str, relationship: str, company: str, stage: str, cadence: int,
          days_ago: int | None, next_action: str, today: date, index: int) -> str:
    note_id = f"2026070109{index:02d}00"
    last_contact = "" if days_ago is None else (today - timedelta(days=days_ago)).isoformat()
    channels = ("{whatsapp: +9715000000%02d, email: %s@example.com, linkedin: }"
                % (index, _slug(name).split("-")[0]))
    log = ("" if days_ago is None
           else f"- {last_contact} — spoke about {NEEDS[name].split('.')[0].lower()}")
    return (
        "---\n"
        f"id: {note_id}\n"
        "type: person\n"
        "created: 2026-07-01\n"
        "source: manual\n"
        "origin: human\n"
        f"relationship: {relationship}\n"
        f"company: {company}\n"
        f"channels: {channels}\n"
        "dex_id:\n"
        "dex_deeplink:\n"
        f"cadence_days: {cadence}\n"
        f"last_contact: {last_contact}\n"
        f"warmth_stage: {stage}\n"
        "status: active\n"
        "sample: true\n"
        "categories: []\n"
        "subjects: []\n"
        "tags: []\n"
        "---\n\n"
        f"# {name}\n\n"
        f"## Context\n\n{CONTEXT[name]}\n\n"
        f"## Needs\n\n{NEEDS[name]}\n\n"
        f"## Interaction log\n\n{log}\n\n"
        f"## Next action\n\n{next_action}\n"
    )


def _commit(vault: Path, message: str) -> None:
    try:
        subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(vault), "commit", "-m", message, "--allow-empty"],
                       check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"(vault isn't a git repo — skipped commit: {message})")


def seed(vault: Path, today: date | None = None) -> list[Path]:
    today = today or date.today()
    folder = vault / "07-People"
    folder.mkdir(parents=True, exist_ok=True)
    _commit(vault, "pre-seed: sample people")     # reviewable + revertible (CLAUDE.md §3)

    written = []
    for i, (name, relationship, company, stage, cadence, days_ago, action) in enumerate(PEOPLE, 1):
        path = folder / f"2026-07-01-{_slug(name)}.md"
        if path.exists():
            continue
        path.write_text(_note(name, relationship, company, stage, cadence, days_ago,
                              action, today, i), encoding="utf-8")
        written.append(path)
    if written:
        _commit(vault, f"seed: {len(written)} sample people")
    return written


def purge(vault: Path) -> list[Path]:
    folder = vault / "07-People"
    removed = []
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        if "\nsample: true\n" in path.read_text(encoding="utf-8"):
            path.unlink()
            removed.append(path)
    if removed:
        _commit(vault, f"seed: removed {len(removed)} sample people")
    return removed


def _vault_from_config(config_path: Path) -> Path:
    return Path(json.loads(config_path.read_text(encoding="utf-8"))["vault_path"]).expanduser()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault")
    parser.add_argument("--config", default=str(REPO_ROOT / "config.json"))
    parser.add_argument("--purge", action="store_true")
    args = parser.parse_args()

    vault = Path(args.vault).expanduser() if args.vault else _vault_from_config(Path(args.config))
    if not vault.is_dir():
        print(f"Vault not found: {vault}")
        return 2

    if args.purge:
        removed = purge(vault)
        print(f"Removed {len(removed)} sample people." if removed else "No sample people to remove.")
        return 0

    written = seed(vault)
    print(f"Wrote {len(written)} sample people into {vault / '07-People'}."
          if written else "Sample people already present — nothing written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
