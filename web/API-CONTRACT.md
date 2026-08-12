# API Contract — what the cockpit frontend expects from Pass 2

The `api/` FastAPI app did not exist when Pass 3 (this frontend) was built, so
this document **is** the contract: the frontend is typed against it
(`src/api/types.ts`), and `mock-api.py` is its executable reference. Pass 2
implements these routes; if a route must change, change this file, the types,
and the mock in the same commit.

## Conventions

- **Base URL** — configurable by the user on the connect screen; default
  `http://127.0.0.1:8000`. All routes live under `/api/`.
- **Auth** — `Authorization: Bearer <token>` where the token is
  `config.json → api.auth_token`. Every route requires it except
  `GET /api/health`. A wrong/missing token returns `401` (the frontend then
  clears its stored token and shows the connect screen).
- **Errors** — every non-2xx response carries the three-part plain-English
  envelope (CLAUDE.md §5), with the field names from
  `pipeline/errors.py::StageError`:

  ```json
  { "error": { "what": "Could not classify the note.",
               "cause": "ANTHROPIC_API_KEY is not set, so the Haiku classifier can't run.",
               "todo": "export ANTHROPIC_API_KEY=... or add a #tag to route it for free." } }
  ```

  The UI renders these verbatim under the labels "What happened / Likely
  cause / What to do". Stack traces and exception types never appear in a
  response body — logs only.
- **Timestamps** — ISO 8601 seconds precision, as the pipeline already writes
  (`datetime.now().isoformat(timespec="seconds")`). Dates are `YYYY-MM-DD`.
- **Vocabularies** — two distinct lists (never conflate; see
  `pipeline/classify.py`):
  - note **types** (11): `musing learning todo journal project person resource
    decision principle insight reflection`
  - capture **tags** (8): `todo idea journal learning person resource decision
    project`

## Routes

### `GET /api/health` (no auth)

`200 {"ok": true}` — exists so the connect screen can tell "server down"
apart from "bad token".

### `GET /api/status`

```json
{
  "vault": "MyVault",
  "engine": "whispercpp",
  "heartbeat": "2026-07-03T05:40:12",
  "last_run": "2026-07-03T05:40:12",
  "counts": { "pending": 2, "processed_today": 5, "needs_review": 3, "failed": 1 }
}
```

- `vault` = `basename(vault_path)` (used for `obsidian://` deep links).
- `engine` = `config.transcription.engine`.
- `heartbeat` = contents of the heartbeat file, `null` if it doesn't exist.
  The frontend computes OK / ATTENTION / PROBLEM client-side (PROBLEM when
  `failed > 0` or heartbeat is missing/older than 20 minutes).
- counts follow `events.write_status` semantics: `needs_review` and `failed`
  are **distinct files** (latest state per file), not raw event rows.

### `GET /api/review`

```json
{ "items": [ {
  "id": "20260703054000",
  "file": "00-Inbox/2026-07-03-note.md",
  "title": "note-title",
  "excerpt": "first ~300 chars of the note body",
  "suggested_type": "learning",
  "confidence": 0.7,
  "created": "2026-07-03"
} ] }
```

Notes whose classification fell below the confidence threshold. `id` is the
immutable frontmatter id; `confidence` is 0..1.

### `POST /api/review/{id}/approve`

Request `{"type": "learning"}` — one of the 11 note types. Covers both UI
affordances: [Approve] echoes `suggested_type` back; a chip tap sends the
chosen type. The API rewrites the note's `type`/`status` frontmatter and moves
the file per `route.TYPE_FOLDER`.

`200 {"ok": true, "moved_to": "03-Learnings/2026-07-03-note-title.md"}`

### `POST /api/capture`

Request `{"text": "call the plumber", "tag": "todo"}` — `tag` optional
(`null`/omitted), one of the 8 capture tags, no `#`. The API writes a text
capture into `inbox_path` with the tag baked into the filename so
`classify` free-routes it.

`201 {"id": "20260703061500", "status": "captured"}`

### `GET /api/failed`

```json
{ "items": [ {
  "id": 42,
  "file": "memo.m4a",
  "timestamp": "2026-07-03T05:12:00",
  "error": { "what": "…", "cause": "…", "todo": "…" }
} ] }
```

Latest failure per quarantined file; `id` is the event row id; `error` is the
parsed `plain_english_error` (split on the "What happened: / Likely cause: /
What to do:" prefixes; if unparseable, put the whole string in `what`).

### `POST /api/failed/{id}/retry`

Moves the quarantined file from `failed_path` back into `inbox_path` so the
watcher picks it up again. `200 {"ok": true}`

### `GET /api/events?status=&limit=&before_id=`

- `status` optional: `ok | failed | needs_review`
- `limit` default 100; `before_id` for pagination.

```json
{ "events": [ {
  "id": 57, "timestamp": "2026-07-03T05:40:11", "file": "memo.m4a",
  "stage": "classify", "status": "needs_review", "duration_ms": 812,
  "message": "type=learning confidence=0.62 by=llm", "plain_english_error": ""
} ] }
```

Reverse-chronological. Field names are exactly the SQLite `events` columns.

### `POST /api/run`

Triggers one pipeline pass. `202 {"started": true}`; `409` + error envelope
if a run is already in flight.

### `GET /api/streak`

```json
{ "current": 12,
  "days": [ { "date": "2026-06-04", "captured": true } ] }
```

`days` = exactly 30 entries, oldest → newest, ending today. A day counts as
captured if any file completed the archive stage ok that day (same source as
`processed_today`).

### `GET /api/resurfaced`

```json
{ "note": {
  "id": "20260101090000", "title": "note-title",
  "file": "02-Musings/2026-01-01-note-title.md",
  "excerpt": "…", "type": "musing", "created": "2026-01-01"
} }
```

One deterministic pick per day (e.g. date-seeded); `note` may be `null` when
the vault is empty. `file` is vault-relative — the frontend builds
`obsidian://open?vault=<vault>&file=<file minus .md>` itself.

## Integrations (Pass 4)

The INTEGRATIONS screen ("one pane of truth"). All health checks run
**server-side and are cached 60s**; the client renders whatever the endpoint
returns and never runs a check itself.

### `GET /api/integrations?fresh=1`

`?fresh=1` bypasses the 60s cache and re-runs every check (the client's
"Recheck"). Response:

```json
{
  "engine": "whispercpp",
  "generated_at": "2026-07-03T05:41:00",
  "fresh": true,
  "cards": [ {
    "id": "transcription-whispercpp",
    "group": "health",
    "name": "Transcription — whisper.cpp",
    "description": "Turns your voice memos into text, all on this machine.",
    "icon": "waveform",
    "status": "ok",
    "badge": "Ready · active",
    "detail": "Local transcription is ready and is the engine in use.",
    "meta": { "model": "ggml-base.en.bin", "engine_active": true }
  } ]
}
```

Per card: `id`, `group` (`"health"` | `"link"`), `name`, `description`, `icon`
(a short key the frontend maps to an inline SVG; unknown keys render a
lettermark), `status` (`"ok"` | `"warn"` | `"problem"` | `"unknown"`), `badge`
(short label, or `null` for link cards), optional `detail`, optional `error`
(the `{what,cause,todo}` envelope — present on `warn`/`problem` health cards),
optional `url` (link cards only), optional `meta` (presentational
`{[k]: string|number|boolean}` — model name, ages, flags).

**Health cards (7)** — server checks, all cached 60s:
- `transcription-whispercpp` — binary + model paths exist & executable
  (`transcribe.py`); `meta.model` = model filename; `meta.engine_active`;
  `meta.is_backup` (true when the active engine is `openai`, i.e. this one is
  standing by as the fallback — badge `Ready · backup`).
- `transcription-openai` — `OPENAI_API_KEY` present (boolean) + cached test-call
  result; `meta.engine_active`. (The engine toggle lives on this card.) When it
  is the active engine with no key but whisper.cpp is configured, the card is
  `warn` with badge `No key · using whisper.cpp` — a covered gap, not an outage.
- `claude` — `ANTHROPIC_API_KEY` present + cached test call.
- `ntfy` — configured topic; `unknown` until a test push is sent.
- `vault-sync` — `inbox_path` & `vault_path` reachable + `meta.minutes_since_activity`.
- `git` — vault repo clean/dirty + commit age; **`warn` if uncommitted > 24h**.
- `watcher` — heartbeat file age; `problem` if missing or > 20 min.

**Link cards** — no health check, no badge, just `url` from the config
`links` section (below): `obsidian` (built server-side as
`obsidian://open?vault=<basename(vault_path)>`), then the 7 known keys
(`dex`, `gmail`, `gcal`, `caldiy`, `n8n`, `zima`, `supabase`), **plus any
other key present in `links`** — unknown keys render with `icon` set to the
key itself, which the frontend draws as a lettermark tile. Keys with an empty
URL are skipped.

### `POST /api/integrations/engine`

Body `{"engine": "whispercpp" | "openai"}`. Writes `transcription.engine` in
`config.json` (a config **write**). `200 {"ok": true, "engine": "openai"}`;
unknown engine → `400` + envelope. The client shows the cloud caution
("Cloud transcription sends your audio to OpenAI…") and requires one confirm
before switching to `openai`.

**Engine fallback.** `openai` is the shipped default and whisper.cpp is its
automatic backup: when the cloud engine can't answer (no key, a rejected
request, or the network still down after the transient-retry budget), the
watcher gives the local engine one attempt before anything is quarantined. The
`transcribe` event message records who actually served —
`engine=openai | engine=whispercpp | engine=whispercpp-fallback | engine=none`
(`none` = a typed capture, which never reaches an engine). The fallback is
one-directional: a vault on `whispercpp` never falls back to the cloud, so
audio is never uploaded by a decision the app made on the owner's behalf.
Consequently `engine: "openai"` **without** a key is accepted when whisper.cpp
is configured, and refused (400 + envelope) only when neither engine could
transcribe.

### `POST /api/integrations/ntfy/test`

No body. Attempts one real push — a user-initiated self-notification (allowed
under CLAUDE.md §4). `200 {"ok": true}` means the ntfy server accepted the
message (a truthful send receipt — still not proof the phone displayed it);
the send failing (network down/blocked, wrong url) → `502` + envelope, and the
ntfy card reports the failed test until a later one succeeds; unconfigured →
`400` + envelope.

### config.json `links` section

The link-card URLs (everything except `obsidian`, which is derived from
`vault_path`):

```json
"links": {
  "dex": "https://getdex.com/",
  "gmail": "https://mail.google.com/",
  "gcal": "https://calendar.google.com/",
  "caldiy": "https://cal.diy/",
  "n8n": "http://zimaos.local:5678/",
  "zima": "http://zimaos.local/",
  "supabase": "https://app.supabase.com/project/_"
}
```

## Config (Pass 2; editable from Settings since Pass 4)

Safe settings only — **key VALUES are never returned or accepted**; the provider
API keys live in the server's environment (CLAUDE.md §7). The Settings screen
edits this subset live (engine with a cloud-caution confirm, confidence
threshold, ntfy url/topic); everything else stays documentation.

### `GET /api/config`

```json
{ "engine": "whispercpp", "confidence_threshold": 0.7,
  "ntfy_url": "https://ntfy.sh", "ntfy_topic": "brain-cockpit",
  "providers": ["gemini-flash", "groq-llama-3.3-70b", "openrouter-free", "claude-haiku"],
  "keys": { "anthropic": true, "openai": false } }
```

`keys` are presence booleans. `providers` is the classification fallback chain
in order (read-only here — reordering lives in config.json).
`api.auth_token` is never included.

### `PUT /api/config`

Body may set any of `engine`, `confidence_threshold` (0..1), `ntfy_topic`,
`ntfy_url` (omitted fields unchanged). Writes `config.json` atomically,
preserving unknown keys (`links`, paths, `api`). Rejects `engine: "openai"`
when `OPENAI_API_KEY` is missing (400 + envelope). Returns the same shape as
`GET`. `POST /api/integrations/engine` shares this validated writer.

## Hardening (Pass 5)

### `GET /api/selfcheck`

The same structural check the server runs before it agrees to boot (config
readable, folders writable, events.db opens), re-run live, plus informational
booleans (auth token set, whisper configured, ntfy configured, a model key
present) for the Build screen.

```json
{ "ok": true, "problems": [],
  "checks": [ { "id": "path-vault", "label": "vault folder writable",
                "ok": true, "detail": "/path/to/vault" } ] }
```

`problems` is a list of `{what,cause,todo}` envelopes — non-empty means the
server would refuse to restart in this state (at boot, these print as a
numbered list and the process exits).

### `POST /api/backup`

Git-commits the vault (`api: manual backup`) and copies `events.db` to
`backups/events-<stamp>.db` (a live-safe sqlite backup, not a raw file copy).

`200 {"ok": true, "at": "<iso>", "vault_committed": true, "events_db_copied": true}`

`vault_committed` is false when the vault isn't a git repository;
`events_db_copied` is false when there's no events.db yet.

### `GET /api/backup`

`{"last_backup": "<iso>|null", "last_vault_commit": "<iso>|null"}` — newest
`backups/` copy and the vault's HEAD commit time. A dedicated route (not part
of `/api/status`) so the 20-second status poll never shells out to git.

## Todos (Pass T)

Todos are Obsidian Tasks-compatible checkbox lines in `06-Todos/<date>.md`:
`- [ ] task (from [[<note-id>]]) 📅 2026-07-05 ⏰ 14:00 🔁 every week ^<block-id>`
— markers only when known; a ⏰ time means a reminder fires at that time (once,
via the watcher's --loop tick). Completing flips `- [ ]` to `- [x]` in place;
lines are never deleted. All date ranges are **Asia/Kolkata**.

**Recurrence (🔁).** Rules: `every day` · `every week` · `every month` ·
`every N days` (also `every N weeks/months`). Completing a 🔁 line **spawns its
next occurrence** — a new unchecked line in `06-Todos/<next-due>.md` with the
date advanced, a fresh block id `<base>-r<N>`, and the ⏰ time, rule and
provenance carried over; the completed line stays as the record. Month steps
clamp to the end of the target month (Jan 31 → Feb 28/29). Reopening deletes
nothing, and a duplicate guard means re-completing won't write the same
occurrence twice. A rule the app can't parse ("every other Tuesday") leaves the
todo perfectly valid — it just doesn't repeat. The syntax matches the Obsidian
Tasks plugin, so ticking the box in Obsidian yields the same result there.

### `GET /api/todos?range=today|tomorrow|week|overdue`

```json
{ "items": [ { "id": "20260703140000-1", "task": "call the dentist",
  "due": "2026-07-05", "time": "14:00", "done": false, "overdue": false,
  "recurrence": null, "file": "06-Todos/2026-07-03.md" } ] }
```

`recurrence` is the raw 🔁 rule text (`"every week"`) or `null`.

Only lines with a due date and a block id appear (undated todos live in the
daily note). `week` = the day after tomorrow through +7 days. Unknown range →
400 envelope.

### `POST /api/todos/{block_id}/toggle`

Flips the checkbox in place and git-commits the vault
(`api: todo <id> marked done|open`, with `(+ next occurrence <date>)` appended
when one was spawned — one commit covers both lines).

`200 {"ok": true, "done": true, "spawned": {"id": "…-r1", "due": "2026-07-12"}}`
— `spawned` is `null` for a non-recurring todo. Unknown id → 404 envelope.

## Relationship OS (Pass 7)

`07-People` notes, per SCHEMA-REFERENCE.md §7. The lifecycle is **computed**, not
read from the note: `cold` once more days have passed than `cadence_days`,
`dormant` past 3× that, `active` inside it, and `unset` when the note has no
cadence or no `last_contact` (a note written before these fields — it renders
with a set-up prompt, never a guessed threshold). Every write also stamps
`status:` in the file so Obsidian Bases sees the same answer.

### `GET /api/people?filter=all|active|cold|dormant|unset`

```json
{ "items": [ {
  "id": "20260101090002", "name": "Grace Hopper",
  "file": "07-People/2026-01-01-grace-hopper.md",
  "relationship": "collaborator", "company": "Example Co",
  "warmth_stage": "conversing", "cadence_days": 7,
  "last_contact": "2026-07-31", "days_since_contact": 12, "days_overdue": 5,
  "status": "cold", "unset": false, "dex_deeplink": "https://getdex.com/c/d1"
} ] }
```

Sorted most-overdue first, `unset` notes last. Unknown filter → 400 envelope. A
missing `07-People` folder is `{"items": []}`, not an error.

### `GET /api/people/{id}`

The list shape plus `sections` (body H2 → text: Context / Needs / Next action),
`interactions` (the dated `## Interaction log` lines, oldest first), `channels`,
`dex_id`, `created`, `origin`. Unknown id → 404 envelope.

### `POST /api/people/{id}/log-contact`

Stamps `last_contact` to today, appends one dated line to `## Interaction log`,
git-commits (`api: logged contact <id>`). Returns the person's new list shape.

### `PATCH /api/people/{id}`

Body may set `cadence_days` (whole number > 0) and/or `warmth_stage` (one of
`identified researched engaging conversing warm ready`). Git-commits
(`api: person <id> updated`) and returns the new list shape. `status` is **not**
settable — it's computed, so a hand-set value would be contradicted by the next
read; park a relationship by widening its cadence instead. Empty body or a value
outside the schema → 400 envelope; unknown id → 404.

### `POST /api/people/sync`

Pulls contacts from Dex into `07-People`. **One-way** — the app never writes to
Dex (drop-Dex test: the markdown archive stands alone).

`200 {"ok": true, "created": 2, "updated": 1, "unchanged": 9, "skipped": 0, "message": "…"}`

New notes carry `origin: ai`, `source: dex`. Existing notes get only `dex_id`,
`dex_deeplink`, `last_contact` (which never moves backwards) and one idempotent
dated line in the interaction log — the owner's prose is never edited. The vault
is committed *before* the batch (`pre-dex-sync`) and after (`dex: sync …`), so an
import is one revert away. `DEX_API_KEY` is env-only; missing key → 400 envelope,
Dex unreachable/rate-limited → 502 envelope. `config.json → dex` holds
`sync_daily`, `base_url`, `deeplink_base`; with `sync_daily: true` the watcher's
`--loop` tick also runs it once a day after 03:00.

## Decision journal + calibration (Pass 8)

`09-Decisions` notes, per SCHEMA-REFERENCE.md §7. **A probability is captured
only when it was spoken at recording time** ("I'd say 70%") — the pipeline's
`probability` stage stores it, and never infers one. There is deliberately no
way to add a probability afterwards: a hindsight number would be recorded as
the owner's own forecast and would corrupt the only measurement this feature
exists to produce. A decision without one resolves normally with `brier: null`
and is excluded from the chart.

### `GET /api/decisions`

```json
{ "items": [ {
    "id": "20260101100001", "title": "launch", "claim": "The launch slips past October",
    "file": "09-Decisions/2026-01-01-launch.md", "created": "2026-01-01",
    "resolves": "2026-09-01", "resolved": null, "status": "open",
    "probability": 70, "outcome": null, "brier": null, "process_grade": null } ],
  "calibration": {
    "buckets": [ { "bucket": 7, "label": "70–80%", "count": 3, "hits": 2,
                   "actual": 0.6667, "midpoint": 75 } ],
    "resolved_count": 4, "scored_count": 3, "open_count": 2,
    "mean_brier": 0.16, "mean_process_grade": 3.5 } }
```

Open decisions first (soonest `resolves` first), then resolved (newest first).
Ten fixed buckets, 0–10% … 90–100%; `actual` is `null` for an empty bucket —
unknown, not zero. Only decisions with **both** a stated probability and a
recorded outcome reach the curve or `mean_brier`.

### `POST /api/decisions/{id}/resolve`

Request `{"outcome": true, "process_grade": 4}` — `process_grade` is 1–5, the
owner's self-rating of the **process, not the outcome**. Stamps `status:
resolved`, `outcome`, `resolved` (today), `process_grade`, and `brier` =
`(probability/100 − outcome)²` when a probability was stated, or an **empty**
`brier` when it wasn't (a `0` would read as a perfect forecast). Git-commits
(`api: resolved <id>`) and returns the decision's new shape.

Grade outside 1–5 → 400 envelope; already resolved → **409** envelope (the first
answer stands); unknown id → 404.

## /query (Pass 9)

Full-text search over the vault, then an answer built only from what was
retrieved.

**The index is a cache, not a store.** It lives in its own `search.db` (never
`events.db`), every row is a copy of a vault file, and it can be rebuilt from
the vault at any time — delete it and you lose a cache, not a sentence, which
is the test CLAUDE.md §1 sets. Indexed: every folder in `route.TYPE_FOLDER`
plus `wiki/`. **Not** indexed: `raw/` (user-managed), `_System/` (status
artifacts), and `00-Inbox` (those notes haven't passed the review gate, so an
answer must not lean on them). Content-bearing frontmatter (`claim`, `name`,
`company`, `outside_view`, `statement`, `description`) is indexed alongside the
body — a decision's claim is its substance. The watcher's `--loop` tick
reindexes incrementally by mtime.

### `POST /api/query`

Request `{"question": "what did I decide about the launch"}`. Blank → 400
envelope.

```json
{ "found": true,
  "answer": "You gave it 70% [[20260101100001]].",
  "confident": true,
  "provider": "openai-mini",
  "sources": [ { "id": "20260101100001", "title": "The launch slips past October",
                 "file": "09-Decisions/2026-01-01-launch.md", "type": "decision",
                 "snippet": "… the vendor is the long pole …" } ],
  "message": null }
```

The model sees only the retrieved excerpts, must answer from them alone, and
must cite ids inline as `[[id]]`. **A citation naming an id that wasn't
retrieved invalidates the answer** — one stricter retry, then the request
returns `found: false` with the three-part `message` and the retrieved
`sources` so the reader can look themselves. `found: false` is a normal 200,
not an error: it also covers "nothing matched" (no model is called at all) and
the model reporting `confident: false`. Nothing is ever invented to fill a gap.

The question is sanitised into quoted OR-joined terms before it reaches FTS5,
so operators typed by a user (`AND`, `NEAR`, `*`, `col:`) are literal words and
can never be a query injection.

### `POST /api/query/reindex`

Full rebuild — the user-facing repair action when search looks stale.
`200 {"ok": true, "indexed": 128, "took_ms": 240}`

### `GET /api/query/status`

`{"indexed": 128, "folders": ["01-Journal", …]}` — what the index currently holds.

### Model chain

`config.json → query.providers` (default `["openai-mini", "claude-haiku"]`),
falling back to `classification.providers` when unset, and always ending in the
`claude-haiku` floor. **`openai-mini`** is a provider in the same router
(`gpt-4o-mini` via the OpenAI chat-completions API, key from `OPENAI_API_KEY`,
env-only), selectable in either chain. `GET /api/config` reports the resolved
order as `query_providers`.

## Build tracker + model router (Pass B)

### `GET /api/build?fresh=1`

Runs the probes in `checks.json` (cached 60s; `fresh=1` busts). Reality is the
checklist — no manual checkboxes exist. Probe types: `file_exists`,
`config_field_set`, `binary_runs`, `endpoint_ok`, `git_log_contains`,
`vault_query`, `env_var_set` (booleans only, never values).

```json
{ "generated_at": "2026-07-04T09:00:00",
  "next": { "label": "whisper.cpp installed and runnable",
            "next_action": "brew install whisper-cpp, download the small.en model, put both paths in config.json." },
  "items": [ { "id": "pass1", "label": "Pass 1 — pipeline core", "phase": "Build passes",
               "done": true, "detail": "pipeline/watcher.py exists.", "next_action": null } ] }
```

`next` = the first unfinished item in manifest order (null when all done);
every unfinished item carries one plain-English `next_action`.

### `GET /api/providers`

Aggregates the router's per-attempt `stage='llm'` events:

```json
{ "providers": [ { "provider": "gemini-flash", "served": 41, "fell_through": 3,
                   "invalid_json": 2, "avg_confidence": 0.84 } ] }
```

Router rules (pipeline/llm.py): identical prompt to every provider in
`config.classification.providers` (default gemini-flash → groq-llama-3.3-70b →
openrouter-free → claude-haiku); responses must validate against the locked
classification schema; invalid JSON / schema violation / 10s timeout /
rate-limit fall through; keyless providers are skipped silently; claude-haiku
is the floor and stays last; all-fail → needs-review, never a guess. Keys are
env-only: GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY, ANTHROPIC_API_KEY.

## Link capture + enrichment (Pass L)

A text capture containing a URL is detected as `kind=link` at intake and routed
to `04-Resources` as a resource note — no classify LLM, no review gate (a link
IS a resource). **The note is written unconditionally; enrichment is best-effort
decoration.** Failure sets `enriched: false` + one quiet `enrich` event (status
`ok`, never `failed`) — no quarantine, no ntfy alarm. YouTube uses public oEmbed
(keyless); Instagram uses an Apify actor (`APIFY_TOKEN` env + `apify.actor_id`
in config.json — expected to break periodically, degrades gracefully); other
URLs use `<title>` + `og:image`.

Resource note frontmatter (SCHEMA §7 + Pass-L/6 fields; the Pass 6 gallery
consumes these unchanged):
`type: resource, resource_type (tool|tutorial|book|movie|recipe|place|article),
title, cover, source_url, description, status: inbox, platform (youtube|
instagram|web), enriched (bool), enrich_attempts, enrich_last` — plus the
universal block (`id, created, source, origin: human, meta_origin: ai`). Body:
`## Insight` (the user's own words, verbatim), `## Ingredients`/`## Steps` for
recipes, `## Transcript` or `## Caption` for enriched media.

### `POST /api/resources/{id}/enrich`

Manual re-attempt for a resource note (by frontmatter `id` in `04-Resources`).
Re-runs enrichment, rewrites the note (bumps `enrich_attempts`, sets
`enriched: true` on success), git-commits the vault (`api: enriched <id>`).
`200 {"ok": true, "enriched": true}`; unknown id → 404 envelope. Notes with
`enriched: false` also auto-retry once, 24h after the last attempt, on the
watcher's `--loop` tick.

### `GET /api/config` — enrichment block (extension)

`GET /api/config` now also returns:
```json
"enrichment": { "apify_token": false, "apify_actor_set": false,
                "apify_last_call": null, "youtube_keyless": true }
```
Booleans only — no token values (CLAUDE.md §7). `apify_last_call` = timestamp of
the most recent Instagram `enrich` event, or null.
