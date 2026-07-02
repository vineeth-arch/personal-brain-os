# Brain Cockpit — Constitution

These are instructions to myself (any future coding session in this repo). They
are **non-negotiable**. Features can change; these do not. If a request conflicts
with a rule below, stop and surface the conflict — do not quietly break the rule.

Two documents in this repo are law and are referenced by these rules:
`SCHEMA-REFERENCE.md` (note/vault structure) and `DESIGNSYSTEM.md` (frontend).

---

## 1. The vault is the only source of truth

The Obsidian vault at `vault_path` is the **ONLY** source of truth for knowledge.
This app **NEVER** stores note content in its own database. SQLite holds pipeline
events and logs only — what was captured, when, which stage ran, what failed.
**If the database is deleted, no knowledge is lost.** If I ever find myself about
to write note bodies, transcripts, or synthesized text into SQLite, I am wrong —
that content belongs in a file in the vault.

## 2. Re-read SCHEMA-REFERENCE.md before touching notes

Before writing **any** code that creates or edits notes, I **re-read
`SCHEMA-REFERENCE.md`** in full. `SCHEMA-REFERENCE.md` is the authority for the
exact type list and vocabularies — I do not hard-code them from memory. Locked
rules that are impossible or painful to retrofit:

- **Immutable `id`** in frontmatter on every note (timestamp `YYYYMMDDHHmmss`).
  All links and typed edges point to the `id`, never to a title or path.
- **`origin: human | ai`** provenance on every note and on all AI-written
  metadata. It cannot be reconstructed later — set it at creation.
- The **capture/routing tags** (`#todo #idea #journal #learning #person
  #resource #decision #project`) and the **note `type` vocabulary** are two
  **distinct vocabularies** — a capture tag is not a note type. Use the exact
  lists in `SCHEMA-REFERENCE.md`; do not conflate or invent synonyms.
- **One recording = one note, kept at full length.** The note body stays whole.
  Only a genuinely multi-topic **journal / idea / musing** recording may be
  split. Everything else stays as a single note.
- Action items are **additionally** extracted to the daily todo file
  (`06-Todos/<date>.md`) — this is an extra write, it does **not** shorten or
  fragment the note body.

## 3. Commit the vault before any batch write

Git-commit the vault **before** any batch write into it, so every AI change is
reviewable and revertible. **Never bulk-write unreviewed content.** Every bulk AI
write goes through a review gate + commit; AI-generated notes carry `origin: ai`.

## 4. Nothing auto-sends, ever

The app **never** auto-sends anything: no emails, no messages, no posts, no
DMs, no API calls that deliver content to another person or public surface.
Drafting is fine; sending is always a human action taken outside this app.

## 5. Three-part user-facing errors

Every user-facing error has three plain-English parts:
1. **What happened** — in the user's terms, not the exception's.
2. **The likely cause.**
3. **What to do next** — a concrete action.

Stack traces, exception types, and file/line detail go to the **logs**, never to
the UI.

## 6. Frontend follows DESIGNSYSTEM.md exactly

- **Dark mode is the default.**
- **Exactly one functional accent per mode**: teal on indigo (dark),
  pink on bone (light). The accent marks the single "lit" element, not everything.
- **Bricolage Grotesque** for display/headings, **Hanken Grotesk** for body/UI.
- **Tonal color-blocking** (steps within a hue), eyebrow labels, **flush-left**
  ragged-right text — no centered text on primary surfaces.
- **WCAG AA** contrast. The **accent color is never used on small body text**
  (large text and accents only).

Read `DESIGNSYSTEM.md` for tokens, type recipes, and component patterns.

## 7. Dependencies are locked — ask before adding

- **Backend:** `fastapi`, `uvicorn`, `anthropic`, `pydantic`, and the Python
  **standard library**. Nothing else.
- **Frontend:** `react`, `vite`, `typescript`, `tailwind`. Nothing else.
- **API keys come from environment variables only** — never from config, never
  committed.

Adding **any** dependency beyond these requires asking first. No exceptions for
"it's just a small util."

## 8. Scope discipline — one pass per session

This session builds **only the current pass**. Any feature idea beyond the
current pass goes into `DEFERRED.md` as **one line**, and I **do not build it**.
The passes:

- **Pass 1** — `pipeline/`: the watcher + processing stages.
- **Pass 2** — `api/`: the FastAPI app.
- **Pass 3** — `web/`: the React + Vite + TypeScript + Tailwind frontend.
