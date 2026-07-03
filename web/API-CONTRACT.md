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
