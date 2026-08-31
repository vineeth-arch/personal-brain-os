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
  "created": "2026-07-03",
  "suggested_attendees": []
} ] }
```

Notes whose classification fell below the confidence threshold, PLUS every
`type: conversation` note (its type isn't in doubt — two-or-more speakers in
a Plaud transcript decide that deterministically — but it still parks here so
attendees can be confirmed). `id` is the immutable frontmatter id; `confidence`
is 0..1 (always `1.0` for a conversation).

`suggested_attendees` is always present, `[]` when there is nothing to
suggest. For a conversation, each entry is
`{"id": "<07-People id>", "label": "<raw speaker label>", "name": "<matched person, or label>"}`
— a SUGGESTION only (`pipeline.plaud.match_people`, conservative: it never
guesses between two people sharing a name). Nothing is written to the note or
to a person until confirmed via approve's `attendees` list below.

### `POST /api/review/{id}/approve`

Request `{"type": "learning", "attendees": []}` — `type` is one of the 13 note
types; `attendees` is optional (defaults to `[]`) and only means anything when
`type` is `"conversation"`. Covers both UI affordances: [Approve] echoes
`suggested_type` back; a chip tap sends the chosen type. The API rewrites the
note's `type`/`status` frontmatter and moves the file per `route.TYPE_FOLDER`.

For a conversation, `attendees` is the confirmed subset of
`suggested_attendees[].id` (a stale/unknown id is skipped, not an error). Each
confirmed person gets `attendees:` filled in on the moved note (as
`[[person-id]]` links) and a dated line appended to their own
`## Interaction log` — the pipeline never writes either; this is the one place
a human confirmation does (CLAUDE.md §3).

`200 {"ok": true, "moved_to": "03-Learnings/2026-07-03-note-title.md"}`

### `POST /api/capture`

Request `{"text": "call the plumber", "tag": "todo"}` — `tag` optional
(`null`/omitted), one of the 8 capture tags, no `#`. The API writes a text
capture into `inbox_path` with the tag baked into the filename so
`classify` free-routes it.

`201 {"id": "20260703061500", "status": "captured"}`

### `POST /api/capture/audio?tag=&name=`

A recording from the cockpit's mic button. The body is the **raw audio bytes**
— not multipart (`python-multipart` isn't a locked dependency, CLAUDE.md §7) —
and `Content-Type` decides the inbox file's extension: `audio/webm`,
`audio/ogg`, `audio/mp4`, `audio/m4a`, `audio/x-m4a`, `audio/mpeg`,
`audio/wav` (any `;codecs=` parameter is ignored). Both query params are
optional: `tag` is one of the 8 capture tags (no `#`), `name` becomes the
filename's human hint (default `voice-note`).

`201 {"id": "20260703061500", "status": "captured"}` — same shape as the text
capture, and the same minute-precision predicted id.

`400` when the Content-Type isn't audio the pipeline reads, when the tag isn't
a capture tag, or when the recording arrives empty. `413` when the upload
passes the server's 100 MB limit — nothing partial is left in the inbox in any
of those cases.

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

Per card: `id`, `group` (`"health"` | `"link"` | `"google"`), `name`, `description`, `icon`
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

**Link cards** — no health check, no badge, just `url` from the config
`links` section (below): `obsidian` (built server-side as
`obsidian://open?vault=<basename(vault_path)>`), then the known keys
(`dex`, `caldiy`, `n8n`, `zima`, `supabase`), **plus any
other key present in `links`** — unknown keys render with `icon` set to the
key itself, which the frontend draws as a lettermark tile. Keys with an empty
URL are skipped.

**Google cards (2)** — `gmail` and `gcal` (Pass 12). Three states:
- server has no Google OAuth client → `group: "link"`, exactly as before;
- client configured, account not linked → `group: "google"`, `status:
  "unknown"`, `badge: "Not connected"`, plus the `{what,cause,todo}` envelope
  and `meta: {configured: true, connected: false}`;
- linked → `group: "google"`, `status: "ok"`, `badge: "Connected"`,
  `meta.connected: true`.

No Google network call happens while building this payload (it is cached 60s
and must not hang on a Google outage) — the client fetches live mail/events
from the routes below.

### `POST /api/integrations/engine`

Body `{"engine": "whispercpp" | "openai"}`. Writes `transcription.engine` in
`config.json` (a config **write**). `200 {"ok": true, "engine": "openai"}`;
unknown engine → `400` + envelope. The client shows the cloud caution
("Cloud transcription sends your audio to OpenAI…") and requires one confirm
before switching to `openai`.

### `POST /api/integrations/ntfy/test`

No body. Attempts one real push — a user-initiated self-notification (allowed
under CLAUDE.md §4). `200 {"ok": true}` means the ntfy server accepted the
message (a truthful send receipt — still not proof the phone displayed it);
the send failing (network down/blocked, wrong url) → `502` + envelope, and the
ntfy card reports the failed test until a later one succeeds; unconfigured →
`400` + envelope.

## Google — read + draft (Pass 12)

Gmail and Calendar, **read-only plus draft creation**. There is deliberately
**no send route** — the app never sends anything (CLAUDE.md §4); drafts are
sent by the user, in Gmail. `api/tests/test_google.py` fails the build if a
Gmail send URL ever appears in the codebase.

Server config: `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in the environment
(the user's own Google Cloud OAuth client — GO-LIVE.md §6). The long-lived
refresh token is runtime state in `config.json` under
`google.refresh_token`; access tokens are cached in memory only. Scopes:
`gmail.readonly`, `gmail.compose`, `calendar.readonly`, and (Pass D)
`contacts` — for updating existing contacts' notes, never creating any. What
Google actually granted is stored alongside the token as `google.scopes`, so
"is the contacts permission there?" is answerable without a network call; an
account linked before Pass D reports honestly that it needs one re-consent.

### `GET /api/google/connect?redirect_uri=<uri>`

Mints a single-use CSRF state and returns `{"url": "<Google consent URL>"}`
for the client to navigate to. `503` + envelope when the server has no OAuth
client configured.

### `GET /api/google/callback?state=&code=` (no auth)

Google's browser redirect — the only HTML page the API serves. No bearer
token (a redirected browser has none); the single-use `state` from
`/connect` (which *did* require the token) is the proof the flow started
here. Stores the refresh token in `config.json`, preserving every other key,
and renders a plain-English "connected" / "couldn't connect" page.

### `GET /api/google/inbox`

`{"items": [{"id", "from", "subject", "date", "snippet", "url"}]}` — up to 10
recent unread messages from the inbox. Subject falls back to `"(no subject)"`.

### `GET /api/google/events`

`{"items": [{"id", "summary", "start", "end", "all_day", "location", "url"}]}`
— the next 7 days of the primary calendar, single events, time-ordered.

### `POST /api/google/draft`

Body `{"to", "subject", "text"}` → creates a Gmail **draft**.
`200 {"id": "<draft id>", "url": "https://mail.google.com/mail/u/0/#drafts"}`.
The UI's success copy is "Draft saved — send it from Gmail."

### `POST /api/google/disconnect`

Removes `google` from `config.json` and clears the cached access token.
`200 {"ok": true}`

Shared failure envelopes: `409` when no account is linked, `401` when the
link was revoked (both tell the user to press Connect Google), `502` for a
Google outage or a refresh that came back without an access token.

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
{ "engine": "whispercpp", "language": "hi", "confidence_threshold": 0.7,
  "ntfy_url": "https://ntfy.sh", "ntfy_topic": "brain-cockpit",
  "providers": ["gemini-flash", "groq-llama-3.3-70b", "openrouter-free", "claude-haiku"],
  "keys": { "anthropic": true, "openai": false },
  "transliteration": {
    "engine": "ollama", "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen2.5", "openrouter_model": "", "openrouter_key_present": false
  } }
```

`keys` are presence booleans. `providers` is the classification fallback chain
in order (read-only here — reordering lives in config.json).
`language` is the whisper language hint (`""` = auto-detect).
`api.auth_token` is never included, and `transliteration.*_model`/`*_url` are
the model/URL strings themselves (not secrets — `OPENROUTER_API_KEY` is a
presence boolean, same as `keys`).

### Capture-pipeline config keys (Pass P, Settings surface added in Pass H)

```json
"transcription": { "engine": "openai", "language": "hi" },
"transliteration": {
  "engine": "",                                     // "" | "ollama" | "openrouter"
  "ollama": { "url": "http://localhost:11434", "model": "" },
  "openrouter": { "model": "" }                     // key from OPENROUTER_API_KEY
},
"watch_folders": [ { "path": "~/…/Plaud", "source": "plaud" } ]
```

`transliteration` rewrites Devanagari transcripts in Roman Hindi; unset or
unreachable is a normal state — the note is written in Devanagari instead, and
`GET /api/integrations` carries a `transliteration` health card saying so.
`watch_folders` are app-owned folders the watcher copies new recordings *out
of* (never modifying them); `source: plaud` lands them in `inbox/plaud/` —
this list is still config.json-hand-edit only (no Settings UI for it).

### `PUT /api/config`

Body may set any of `engine`, `language`, `confidence_threshold` (0..1),
`ntfy_topic`, `ntfy_url`, `transliteration_engine` (`""` | `"ollama"` |
`"openrouter"`), `transliteration_ollama_url`, `transliteration_ollama_model`,
`transliteration_openrouter_model` (omitted fields unchanged). Writes
`config.json` atomically, preserving unknown keys (`links`, paths, `api`,
`watch_folders`). Rejects `engine: "openai"` when `OPENAI_API_KEY` is missing,
and `transliteration_engine: "openrouter"` when `OPENROUTER_API_KEY` is missing
(both 400 + envelope, same shape). Returns the same shape as `GET`.
`POST /api/integrations/engine` shares this validated writer.

## People — Relationship OS (Pass MW)

The PEOPLE screen. Every route reads and writes notes of `type: person` in
`07-People` (SCHEMA-REFERENCE.md §7). **Nothing here sends anything to anyone**
(CLAUDE.md §4): a draft comes back as text plus the person's raw channel values,
and the cockpit builds the chat/mail deep link in the browser for a human to
tap. `api/tests/test_no_send.py` fails the build if a delivery URL ever appears
in server code.

### `GET /api/people`

```json
{ "items": [ {
  "id": "20260701090000",
  "name": "Priya Raman",
  "relationship": "client",
  "company": "Alserkal Avenue",
  "warmth_stage": "conversing",
  "status": "active",
  "cadence_days": 3,
  "last_contact": "2026-07-27",
  "days_since_contact": 24,
  "going_cold": true,
  "warmup_due": true,
  "commitment_due": true,
  "channels": { "whatsapp": "+9715…", "email": "priya@example.com" },
  "next_action": "Send the studio deck today",
  "sample": false,
  "file": "2026-07-01-priya-raman.md",
  "dex_id": "dex-priya",
  "dex_deeplink": ""
} ] }
```

Ranked by how far each person is through their OWN cadence, so a 3-day contact
a month silent outranks a 90-day one a fortnight silent. `cadence_days` is the
effective figure: the note's `cadence_days` when set, otherwise the default for
its `warmth_stage`. `days_since_contact` is `null` when `last_contact` is empty,
which counts as cold. `dormant` people are never flagged.

### `POST /api/people` (Pass X)

Quick-add a warm-up target — one name, one channel, so feeding the warm-up
engine never requires opening Obsidian.

Body `{"name": "Sara Khalid", "channel": {"kind": "whatsapp"|"email"|"linkedin",
"value": "…"}}`.

Writes a schema-correct note in `07-People` (`origin: human`, `source: manual`,
`warmth_stage: identified`, empty Context/Needs/Interaction log/Next action) and
git-commits the vault (`api: added target <name>`). `201` with the same person
object `GET /api/people` returns.

The frontmatter `id` is a `YYYYMMDDHHmmss` timestamp and is guaranteed unique —
two targets added in the same second step the stamp forward rather than sharing
an id, because every link in the vault points at it (SCHEMA-REFERENCE.md §1).

`400` + envelope for a blank name, a blank channel value, or a channel kind
outside the three; nothing is written in those cases.

### `GET /api/people/{id}`

The same object plus `context`, `needs` and `interaction_log` (the `## Context`,
`## Needs` and `## Interaction log` body sections). `404` + envelope for an
unknown id.

### `POST /api/people/{id}/draft`

Body `{"channel": "whatsapp" | "email" | "linkedin" | null}` — omitted picks the
first channel the person has, in that order.

```json
{ "text": "hey Priya — long time…", "channel": "whatsapp",
  "channels": { "whatsapp": "+9715…", "email": "priya@example.com" },
  "provider": "claude-haiku" }
```

The message is written in the owner's voice from `_System/my-voice.md` and
leashed to the person's interaction log — an empty log produces a shorter,
vaguer message rather than an invented shared history.

`409` when `_System/my-voice.md` doesn't exist (the cockpit sends the user to
Settings → My voice rather than drafting in a generic voice). `502` when every
provider in the chain failed. `404` for an unknown id.

### `POST /api/people/{id}/contact`

Body `{"note": "…", "channel": "whatsapp"}`. Appends a dated line to the
`## Interaction log`, resets `last_contact` to today, revives a `cold` person to
`active`, and commits the vault. Returns the refreshed person plus
`"suggest_stage"` — the next warmth stage, offered for one tap, never applied
automatically.

### `POST /api/people/{id}/warmth`

Body `{"stage": "conversing"}` — one of the six stages in SCHEMA-REFERENCE.md
§7. Returns the refreshed person; `400` for anything outside the six. (POST, not
PATCH: every mutation in this API is a POST verb.)

### `POST /api/people/{id}/enrich`

Looks the person up at People Data Labs and appends role/company under
`## Context`, marked `origin: ai`, then commits. Returns the refreshed person
plus `"enriched"`, `"credits_remaining"` and a one-line `"detail"`.

`503` when `PDL_API_KEY` isn't set — the honest not-configured state; every
other People feature works without it. `502` when the lookup itself fails.

## Profile push — Dex + Google Contacts (Pass D)

Pushes a generated **profile summary** into the owner's own CRM (Dex) and their
own address book (Google Contacts). This is not a send: nothing reaches another
person, and no messaging URL is built anywhere
(`api/tests/test_no_send.py::test_the_push_modules_write_profile_data_never_messages`
fails the build if `pipeline/dex.py` or `api/google_contacts.py` ever reference
a messaging surface).

Two properties hold for every push:

- **Append-only.** The app owns the text between `<!-- BRAIN-OS -->` and
  `<!-- /BRAIN-OS -->` and replaces exactly that on each push. Everything the
  owner typed into the field themselves is preserved byte for byte
  (`pipeline/dex.py::merge`).
- **Human-gated.** `preview` is the dry run of `push` — same summary, same
  merge, same payload — and `push` writes back **the text the preview
  returned**. There is no auto-push and no unattended batch (CLAUDE.md §3).

Server config: `DEX_API_KEY` in the environment (env-only, CLAUDE.md §7).
Contacts rides the existing Google OAuth client with the added
`https://www.googleapis.com/auth/contacts` scope, so an account linked before
this pass needs one re-consent — reported as a `409`, never a crash.

### `POST /api/people/{id}/push/preview`

Body `{"target": "dex" | "contacts"}`.

```json
{ "target": "dex", "person_id": "20260701090000", "name": "Priya Raman",
  "summary": "Priya runs the artist programme at Alserkal.\n…",
  "block": "<!-- BRAIN-OS -->\n…\n· via Brain OS 2026-08-20\n<!-- /BRAIN-OS -->",
  "destination": "Dex contact dex-priya · description",
  "replaced": "" }
```

`replaced` is our previous block (`""` when the app owns nothing there yet) so
the UI can show what is being overwritten. Nothing is written to produce this.

`400` unknown target · `404` unknown person, or (contacts) no matching contact
in the address book — this app never creates one · `409` no `dex_id` on the
note, or Google linked without the contacts scope · `503` target not configured
(checked **before** any model call, so an unconfigured push costs nothing) ·
`502` every provider in the chain failed.

### `POST /api/people/{id}/push`

Body `{"target": "dex" | "contacts", "text": "<the confirmed summary>"}` —
`text` is what the preview returned and the human approved; the server never
regenerates it here, so what was read is what is written.

`200 {"ok": true, "target": "dex", "changed": "Dex contact dex-priya · description", "replaced": false}`

Same error envelopes as the preview. The vault is unchanged by a push (nothing
was learned about the person); the durable record is one `stage="push"` row in
events.db.

### `GET /api/push/queue`

The staged half of the batch — who has moved on since their profile last went
out. Generates no summaries and calls no external API, so it is cheap to poll.

```json
{ "items": [ { "…person fields…", "targets": ["dex"], "last_pushed": null } ],
  "available": { "dex": true, "contacts_scope": false } }
```

A person is staged when they have never been pushed, or when their
`last_contact` is on/after their last successful push. `dormant` and `sample`
people are never staged. The morning digest (`pipeline/morning.py::push_section`)
adds one line naming the count — it rides the existing 8am push and never
pushes anything itself.

### `GET /api/config` — push block (extension)

```json
"push": { "dex": false, "contacts_scope": false }
```

Presence booleans only. The People drawer renders a quiet "not configured" pill
instead of a button for any target that is false.

### `GET` / `POST /api/people/voice`

`GET` → `{"exists": false, "file": "_System/my-voice.md", "samples": 0}`.
`POST` body `{"samples": ["…", "…"]}` writes the file from messages the owner
actually sent (stored verbatim, `origin: human`) and commits the vault; `400`
when every sample is blank.

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
`- [ ] task (from [[<note-id>]]) 📅 2026-07-05 ⏰ 14:00 ^<block-id>` — markers
only when known; a ⏰ time means a reminder fires at that time (once, via the
watcher's --loop tick). Completing flips `- [ ]` to `- [x]` in place; lines are
never deleted. All date ranges are **Asia/Kolkata**.

### `GET /api/todos?range=today|tomorrow|week|overdue`

```json
{ "items": [ { "id": "20260703140000-1", "task": "call the dentist",
  "due": "2026-07-05", "time": "14:00", "done": false, "overdue": false,
  "file": "06-Todos/2026-07-03.md" } ] }
```

Only lines with a due date and a block id appear (undated todos live in the
daily note). `week` = the day after tomorrow through +7 days. Unknown range →
400 envelope.

### `POST /api/todos/{block_id}/toggle`

Flips the checkbox in place and git-commits the vault
(`api: todo <id> marked done|open`). `200 {"ok": true, "done": true}`;
unknown id → 404 envelope.

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

## Link capture + enrichment (Pass L, hardened in Pass C)

A text capture containing a URL is detected as `kind=link` at intake and routed
to `04-Resources` as a resource note — no classify LLM, no review gate (a link
IS a resource) — **unless a capture tag (filename or spoken/typed `#tag`) names
something other than `resource`** (Pass C, D13): a tagged capture flows through
the normal classify/route path instead, with the URL left intact in the body,
so `#journal ... here's an article https://…` is a journal note, not a resource.
**The note is written unconditionally; enrichment is best-effort decoration.**
Failure sets `enriched: false` + one quiet `enrich` event (status `ok`, never
`failed`) — no quarantine, no ntfy alarm. YouTube uses public oEmbed (keyless)
for title/author/cover and the same public innertube endpoint the YouTube apps
themselves call for a transcript (also keyless — the old `timedtext` endpoint
this replaced had gone dead); Instagram uses an Apify actor (`APIFY_TOKEN` env
+ `apify.actor_id` in config.json, default `apify~instagram-scraper` —
expected to break periodically, degrades gracefully; a note that failed only
for lack of configuration gets extra auto-retries, capped at 4 attempts total,
once Apify is configured); other URLs use `<title>` + `og:image`.

The `## Insight` body section is the user's own words **minus the URL itself**
(Pass C, D14) — the URL already lives in `source_url`, so gluing it into the
insight text too was pure duplication. A capture that was only a bare URL with
no other words gets no `## Insight` section at all.

Resource note frontmatter (SCHEMA §7 + Pass-L/6 fields; the Pass 6 gallery
consumes these unchanged):
`type: resource, resource_type (tool|tutorial|book|movie|recipe|place|article),
title, cover, source_url, description, status: inbox, platform (youtube|
instagram|web), enriched (bool), enrich_attempts, enrich_last` — plus the
universal block (`id, created, source, origin: human, meta_origin: ai`). Body:
`## Insight` (the user's own words, URL removed), `## Ingredients`/`## Steps`
for recipes, `## Transcript` or `## Caption` for enriched media, and
`## Slides` for an Instagram carousel — a numbered list of `image_url —
caption` (Pass C); a single post/reel has no `## Slides` section. Every field
and section above is what enrichment owns; re-enrichment (below) merges these
back in and leaves everything else on the note — `status`, `rating`, `sample`,
the user's own `## Insight` edits, any hand-added section — untouched.

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

## MCP — Claude Desktop (Pass X)

`scripts/cockpit_mcp.py` is a stdio MCP server that **proxies to these same
routes**. It holds no logic and no data: every tool is one authenticated HTTP
call to an endpoint above, and the answer is passed back unchanged. It is not
a second API and adds no route — if a tool needs something, the route has to
exist here first.

Configured by environment only (CLAUDE.md §7): `COCKPIT_URL`, `COCKPIT_TOKEN`.

| Tool | Route |
| --- | --- |
| `cockpit_status` | `GET /api/status` |
| `people_list(going_cold?, warmth_stage?)` | `GET /api/people` (filters applied in the proxy) |
| `people_draft(person_id, channel?)` | `POST /api/people/{id}/draft` |
| `capture_text(text, tag?)` | `POST /api/capture` |
| `todos_today` | `GET /api/todos?range=today` |

Read-mostly by design: `capture_text` is the only write. `people_draft` returns
draft **text**, exactly as the cockpit's own drawer does — the MCP layer has no
way to deliver a message and none may be added (CLAUDE.md §4, pinned by
`api/tests/test_mcp.py::test_the_mcp_layer_has_no_way_to_send_anything`).

Non-2xx responses keep their `{what,cause,todo}` envelope, re-rendered as the
tool's error text ("What happened: … / Likely cause: … / What to do: …"), so a
model holding these tools tells the owner the same thing the cockpit would.

## Resource OS (Pass 6, documented in Pass H)

The Resources screen's full surface — six routes that existed since Pass 6 but
were missing from this contract (and from web/mock-api.py) until now. Every
mutation git-commits the vault; every read walks `04-Resources` live (no
server-side cache, no note content in SQLite — CLAUDE.md §1).

### `GET /api/resources?category=&status=&q=&has_insight=&sort=`

```json
{ "items": [ {
  "id": "20260703140000", "title": "Weeknight dal", "category": "recipe",
  "status": "to-consume", "cover": "https://picsum.photos/seed/dal/400/560",
  "url": "https://example.com/dal", "created": "2026-07-03",
  "sample": false, "file": "04-Resources/2026-07-03-weeknight-dal.md",
  "has_insight": true, "insight": "Halve the chili next time."
} ] }
```

All filters are optional and combine with AND. `q` matches the **title only**,
case-insensitive substring (not description/insight/body — that's whole-vault
search, Pass Q). `sort` is one of `created` (default, newest first), `oldest`,
`title`; anything else → 400 envelope. `cover`/`url`/`insight` are `null` when
absent, never an empty string.

### `GET /api/resources/{id}`

The same object plus `description`, `rating`, the nine type-extra fields
(`author, where_to_watch, runtime, ingredients, steps, tools_mentioned,
transcript, map_url, best_time` — `null` when not applicable to that
`resource_type`), and `sections`: `[{heading, text}]` for every body `## `
section in order (heading `""` holds any text before the first H2). 404
envelope for an unknown id.

### `POST /api/resources/{id}/status`

Body `{"status": "to-consume"}` — one of the SCHEMA §6 lifecycle values
(`inbox → to-consume → consumed → referenced → archived`); anything else → 400
envelope. Reaching `consumed` stamps a `consumed: <today>` frontmatter date.
Returns the refreshed summary object (the same shape as one `GET
/api/resources` item). 404 for an unknown id.

### `POST /api/resources/{id}/insight`

Body `{"text": "…"}`. Writes (or replaces, or — for empty text — removes) the
`## Insight` body section, always with `origin: human` (an insight is never AI
prose, even when the rest of the note is `origin: ai`). Returns the refreshed
summary object. 404 for an unknown id.

### `GET /api/resources/sample/count?older_than=1d|1w|1m|all`

`200 {"count": 4, "scope": "1w"}` — how many `sample: true` notes match the
age scope; `count` never includes a real (non-sample) note. Unknown scope →
400 envelope.

### `DELETE /api/resources/sample?older_than=1d|1w|1m|all`

Removes only `sample: true` notes matching the scope — real notes are never a
candidate, regardless of scope. Git-commits the vault **before** deleting
(`pre-purge: N sample notes, scope=…`) so the whole purge is one `git revert`
away, then commits again after if anything was removed.

```json
{ "removed": 4, "titles": ["Weeknight dal", "…"], "scope": "1w",
  "message": "Removed 4 sample notes older than a week. Your real notes were "
             "never touched, and the vault was git-committed first." }
```
