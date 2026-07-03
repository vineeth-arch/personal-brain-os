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

`200 {"ok": true, "moved_to": "02-Wiki/2026-07-03-note-title.md"}`

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
  "file": "02-Wiki/2026-01-01-note-title.md",
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
  (`transcribe.py`); `meta.model` = model filename; `meta.engine_active`.
- `transcription-openai` — `OPENAI_API_KEY` present (boolean) + cached test-call
  result; `meta.engine_active`. (The engine toggle lives on this card.)
- `claude` — `ANTHROPIC_API_KEY` present + cached test call.
- `ntfy` — configured topic; `unknown` until a test push is sent.
- `vault-sync` — `inbox_path` & `vault_path` reachable + `meta.minutes_since_activity`.
- `git` — vault repo clean/dirty + commit age; **`warn` if uncommitted > 24h**.
- `watcher` — heartbeat file age; `problem` if missing or > 20 min.

**Link cards (8)** — no health check, no badge, just `url` from the config
`links` section (below): `obsidian` (built server-side as
`obsidian://open?vault=<basename(vault_path)>`), `dex`, `gmail`, `gcal`,
`caldiy`, `n8n`, `zima`, `supabase`.

### `POST /api/integrations/engine`

Body `{"engine": "whispercpp" | "openai"}`. Writes `transcription.engine` in
`config.json` (a config **write**). `200 {"ok": true, "engine": "openai"}`;
unknown engine → `400` + envelope. The client shows the cloud caution
("Cloud transcription sends your audio to OpenAI…") and requires one confirm
before switching to `openai`.

### `POST /api/integrations/ntfy/test`

No body. Sends one push via `pipeline/errors.py::ntfy()` — a user-initiated
self-notification (allowed under CLAUDE.md §4). `200 {"ok": true}` means "sent"
(ntfy() never raises, so it is not a delivery receipt); unconfigured →
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
