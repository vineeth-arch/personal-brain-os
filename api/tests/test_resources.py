"""Pass 6 — Resource OS endpoints, purge safety, and seed idempotency.

Reuses the hermetic harness from test_api.py (ephemeral uvicorn + urllib, the
git-initialised tmp vault fixture). The purge tests are the point: a planted
real note must survive every scope, including 'all'.
"""
from __future__ import annotations

import subprocess
from datetime import date, timedelta
from pathlib import Path

# Reuse the harness + the tmp-vault fixture verbatim (env is a pytest fixture;
# importing it here lets pytest discover it in this module too).
from api.tests.test_api import Server, env  # noqa: F401


def _write_resource(folder: Path, note_id: str, *, rt: str, status: str, title: str,
                    sample: bool, created: str, insight: str | None = None,
                    url: str = "https://example.com/x", description: str = "A thing worth keeping.",
                    extra: dict[str, str] | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"id: {note_id}",
        "type: resource",
        f"created: {created}",
        "source: seed" if sample else "source: manual",
        "origin: human",
        f"status: {status}",
        f"resource_type: {rt}",
        f"title: {title}",
        f"description: {description}",
        f"cover: https://picsum.photos/seed/{note_id}/400/560",
        f"source_url: {url}",
    ]
    for key, value in (extra or {}).items():
        lines.append(f"{key}: {value}")
    if sample:
        lines.append("sample: true")
    lines += ["categories: []", "subjects: []", "tags: []", "---", ""]
    body = "About the thing."
    if insight:
        body += f"\n\n## Insight\n{insight}"
    (folder / f"{created}-{note_id}.md").write_text("\n".join(lines) + body + "\n")


def _git_log(vault: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(vault), "log", "--format=%s"],
                         capture_output=True, text=True)
    return out.stdout.strip().splitlines()


# ---- parsing / listing ------------------------------------------------------

def test_resources_list_parses_fields_and_insight(env):
    tmp, vault, *_ = env
    res = vault / "04-Resources"
    _write_resource(res, "20260601120000", rt="book", status="referenced",
                    title="Refactoring UI", sample=True, created="2026-06-01",
                    insight="Spacing is the biggest lever.")
    _write_resource(res, "20260101120000", rt="tool", status="inbox",
                    title="Linear", sample=True, created="2026-01-01")
    _write_resource(res, "20260701120000", rt="movie", status="consumed",
                    title="Blade Runner 2049", sample=True, created="2026-07-01")

    with Server(tmp) as s:
        code, body = s.req("GET", "/api/resources")
        assert code == 200
        items = body["items"]
        assert len(items) == 3
        # default sort: created desc
        assert [i["created"] for i in items] == ["2026-07-01", "2026-06-01", "2026-01-01"]
        book = next(i for i in items if i["title"] == "Refactoring UI")
        assert book["category"] == "book"
        assert book["url"] == "https://example.com/x"
        assert book["cover"].startswith("https://picsum.photos/")
        assert book["sample"] is True
        assert book["has_insight"] is True
        assert book["insight"] == "Spacing is the biggest lever."
        linear = next(i for i in items if i["title"] == "Linear")
        assert linear["has_insight"] is False and linear["insight"] is None


def test_resources_filters(env):
    tmp, vault, *_ = env
    res = vault / "04-Resources"
    _write_resource(res, "20260601120000", rt="book", status="referenced",
                    title="Refactoring UI", sample=True, created="2026-06-01",
                    insight="Spacing.")
    _write_resource(res, "20260101120000", rt="tool", status="inbox",
                    title="Linear", sample=True, created="2026-01-01")
    _write_resource(res, "20260701120000", rt="book", status="inbox",
                    title="The Brand Gap", sample=True, created="2026-07-01")

    with Server(tmp) as s:
        # category (case-insensitive)
        _, body = s.req("GET", "/api/resources?category=Book")
        assert {i["title"] for i in body["items"]} == {"Refactoring UI", "The Brand Gap"}
        # status
        _, body = s.req("GET", "/api/resources?status=inbox")
        assert {i["title"] for i in body["items"]} == {"Linear", "The Brand Gap"}
        # search
        _, body = s.req("GET", "/api/resources?q=brand")
        assert [i["title"] for i in body["items"]] == ["The Brand Gap"]
        # has_insight
        _, body = s.req("GET", "/api/resources?has_insight=true")
        assert [i["title"] for i in body["items"]] == ["Refactoring UI"]
        # sort title
        _, body = s.req("GET", "/api/resources?sort=title")
        assert [i["title"] for i in body["items"]] == ["Linear", "Refactoring UI", "The Brand Gap"]
        # bad sort → 400 envelope
        code, body = s.req("GET", "/api/resources?sort=nope")
        assert code == 400 and set(body["error"]) == {"what", "cause", "todo"}


def test_missing_folder_is_empty_not_error(env):
    tmp, *_ = env  # env does not create 04-Resources
    with Server(tmp) as s:
        assert s.req("GET", "/api/resources") == (200, {"items": []})


def test_resource_detail_and_404(env):
    tmp, vault, *_ = env
    res = vault / "04-Resources"
    _write_resource(res, "20260601120000", rt="book", status="referenced",
                    title="Refactoring UI", sample=True, created="2026-06-01",
                    insight="Spacing is the biggest lever.")
    with Server(tmp) as s:
        code, body = s.req("GET", "/api/resources/20260601120000")
        assert code == 200
        assert body["title"] == "Refactoring UI"
        assert body["description"] == "A thing worth keeping."
        headings = {sec["heading"] for sec in body["sections"]}
        assert "Insight" in headings
        code, body = s.req("GET", "/api/resources/99999999999999")
        assert code == 404 and set(body["error"]) == {"what", "cause", "todo"}


def test_resource_detail_exposes_type_extras_per_category(env):
    """SCHEMA-REFERENCE.md §7 'Type extras' — each resource_type carries a
    different extra-field set; the detail endpoint must surface whichever
    fields THAT note's frontmatter actually has, and null the rest."""
    tmp, vault, *_ = env
    res = vault / "04-Resources"
    _write_resource(res, "20260601120000", rt="book", status="inbox",
                    title="Designing Brand Identity", sample=True, created="2026-06-01",
                    extra={"author": "Alina Wheeler"})
    _write_resource(res, "20260602120000", rt="movie", status="inbox",
                    title="Blade Runner 2049", sample=True, created="2026-06-02",
                    extra={"where_to_watch": "Netflix", "runtime": "164 min"})
    _write_resource(res, "20260603120000", rt="recipe", status="inbox",
                    title="Shakshuka", sample=True, created="2026-06-03",
                    extra={"ingredients": "eggs, tomato, peppers", "steps": "soften, simmer, nest eggs"})
    _write_resource(res, "20260604120000", rt="place", status="inbox",
                    title="Alserkal Avenue", sample=True, created="2026-06-04",
                    extra={"map_url": "https://maps.google.com/?q=Alserkal", "best_time": "Thursday evenings"})
    _write_resource(res, "20260605120000", rt="tutorial", status="inbox",
                    title="Advanced Figma Auto Layout", sample=True, created="2026-06-05",
                    extra={"tools_mentioned": "auto layout, constraints", "transcript": "Welcome to the talk..."})

    with Server(tmp) as s:
        _, book = s.req("GET", "/api/resources/20260601120000")
        assert book["author"] == "Alina Wheeler"
        assert book["where_to_watch"] is None and book["ingredients"] is None

        _, movie = s.req("GET", "/api/resources/20260602120000")
        assert movie["where_to_watch"] == "Netflix" and movie["runtime"] == "164 min"
        assert movie["author"] is None

        _, recipe = s.req("GET", "/api/resources/20260603120000")
        assert recipe["ingredients"] == "eggs, tomato, peppers"
        assert recipe["steps"] == "soften, simmer, nest eggs"

        _, place = s.req("GET", "/api/resources/20260604120000")
        assert place["map_url"] == "https://maps.google.com/?q=Alserkal"
        assert place["best_time"] == "Thursday evenings"

        _, tutorial = s.req("GET", "/api/resources/20260605120000")
        assert tutorial["tools_mentioned"] == "auto layout, constraints"
        assert tutorial["transcript"] == "Welcome to the talk..."

        # the list endpoint stays lean — extras are detail-only
        _, listing = s.req("GET", "/api/resources")
        assert "author" not in listing["items"][0]


# ---- writes: status advance + insight --------------------------------------

def test_status_advance_restamps_and_commits(env):
    tmp, vault, *_ = env
    res = vault / "04-Resources"
    _write_resource(res, "20260101120000", rt="tool", status="inbox",
                    title="Linear", sample=True, created="2026-01-01")
    with Server(tmp) as s:
        code, body = s.req("POST", "/api/resources/20260101120000/status",
                           {"status": "consumed"})
        assert code == 200 and body["status"] == "consumed"
        # frontmatter actually restamped on disk
        note = next((res).glob("*.md"))
        text = note.read_text()
        assert "status: consumed" in text and "consumed: " in text
        assert "type: resource" in text  # type untouched
        assert _git_log(vault)[0] == "api: resource 20260101120000 → consumed"
        # invalid status → 400
        code, body = s.req("POST", "/api/resources/20260101120000/status",
                           {"status": "done"})
        assert code == 400 and set(body["error"]) == {"what", "cause", "todo"}


def test_insight_write_creates_section_origin_human_and_commits(env):
    tmp, vault, *_ = env
    res = vault / "04-Resources"
    _write_resource(res, "20260101120000", rt="tool", status="consumed",
                    title="Linear", sample=True, created="2026-01-01")
    with Server(tmp) as s:
        code, body = s.req("POST", "/api/resources/20260101120000/insight",
                           {"text": "Opinionated defaults make me faster."})
        assert code == 200 and body["has_insight"] is True
        note = next((res).glob("*.md"))
        text = note.read_text()
        assert "## Insight" in text
        assert "Opinionated defaults make me faster." in text
        assert "origin: human" in text  # firewall: never flipped to ai
        assert _git_log(vault)[0] == "api: insight on 20260101120000"
        # replace + then clear
        s.req("POST", "/api/resources/20260101120000/insight", {"text": "Second take."})
        _, body = s.req("GET", "/api/resources/20260101120000")
        assert body["insight"] == "Second take."
        code, body = s.req("POST", "/api/resources/20260101120000/insight", {"text": ""})
        assert code == 200 and body["has_insight"] is False


# ---- purge safety (the point) ----------------------------------------------

def _plant_mixed(res: Path) -> None:
    old = (date.today() - timedelta(days=60)).isoformat()
    recent = date.today().isoformat()
    # two OLD samples, one RECENT sample, one REAL note (no sample flag)
    _write_resource(res, "20250101120000", rt="book", status="inbox",
                    title="Old Sample A", sample=True, created=old)
    _write_resource(res, "20250101120001", rt="tool", status="inbox",
                    title="Old Sample B", sample=True, created=old)
    _write_resource(res, "20260704120000", rt="movie", status="inbox",
                    title="Recent Sample", sample=True, created=recent)
    _write_resource(res, "20260704120001", rt="article", status="inbox",
                    title="My Real Note", sample=False, created=recent)


def test_sample_count_and_scope(env):
    tmp, vault, *_ = env
    _plant_mixed(vault / "04-Resources")
    with Server(tmp) as s:
        assert s.req("GET", "/api/resources/sample/count?older_than=all")[1]["count"] == 3
        assert s.req("GET", "/api/resources/sample/count?older_than=1m")[1]["count"] == 2
        # bad scope → 400
        code, body = s.req("GET", "/api/resources/sample/count?older_than=1y")
        assert code == 400 and set(body["error"]) == {"what", "cause", "todo"}


def test_purge_all_removes_only_samples_real_note_survives(env):
    tmp, vault, *_ = env
    res = vault / "04-Resources"
    _plant_mixed(res)
    with Server(tmp) as s:
        code, body = s.req("DELETE", "/api/resources/sample?older_than=all")
        assert code == 200
        assert body["removed"] == 3
        assert "My Real Note" not in body["titles"]
        assert set(body["titles"]) == {"Old Sample A", "Old Sample B", "Recent Sample"}
        assert "real notes were never touched" in body["message"]
    # only the real note remains on disk
    remaining = [p.name for p in res.glob("*.md")]
    assert len(remaining) == 1
    assert "My Real Note" in (res / remaining[0]).read_text()
    # the purge was git-committed, pre-purge first (one revert away from undone)
    log = _git_log(vault)
    assert "pre-purge: 3 sample notes, scope=all" in log
    assert log[0].startswith("api: purged 3 sample notes")


def test_purge_month_scope_spares_recent_and_real(env):
    tmp, vault, *_ = env
    res = vault / "04-Resources"
    _plant_mixed(res)
    with Server(tmp) as s:
        code, body = s.req("DELETE", "/api/resources/sample?older_than=1m")
        assert code == 200 and body["removed"] == 2
        assert set(body["titles"]) == {"Old Sample A", "Old Sample B"}
    titles_left = {(res / p.name).read_text() for p in res.glob("*.md")}
    joined = "\n".join(titles_left)
    assert "My Real Note" in joined  # real note untouched
    assert "Recent Sample" in joined  # recent sample untouched by month scope


def test_real_note_only_purge_is_a_noop(env):
    # A vault with only a real note: the purge finds nothing to remove and the
    # note is never touched, whatever the scope.
    tmp, vault, *_ = env
    res = vault / "04-Resources"
    _write_resource(res, "20260704120001", rt="article", status="inbox",
                    title="My Real Note", sample=False, created=date.today().isoformat())
    with Server(tmp) as s:
        code, body = s.req("DELETE", "/api/resources/sample?older_than=all")
        assert code == 200 and body["removed"] == 0
    assert any("My Real Note" in (res / p.name).read_text() for p in res.glob("*.md"))


# ---- seed idempotency -------------------------------------------------------

def test_seed_is_idempotent(env):
    tmp, vault, *_ = env
    import scripts.seed_resources as seed_mod

    first = seed_mod.seed(vault, today=date(2026, 7, 4))
    assert len(first) == 36
    on_disk = list((vault / "04-Resources").glob("*.md"))
    assert len(on_disk) == 36
    second = seed_mod.seed(vault, today=date(2026, 7, 4))
    assert second == []  # nothing re-written
    assert len(list((vault / "04-Resources").glob("*.md"))) == 36
