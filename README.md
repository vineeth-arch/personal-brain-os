# Brain Cockpit

Local-first personal pipeline: a watcher + processing stages (`pipeline/`), a
FastAPI backend (`api/`), and a React + Vite + TypeScript + Tailwind frontend
(`web/`). The Obsidian vault is the only source of truth for knowledge; the
app's SQLite holds pipeline events and logs only.

Read `CLAUDE.md` before making changes — it is the constitution. Structure and
note rules are locked in `SCHEMA-REFERENCE.md`; the frontend follows
`DESIGNSYSTEM.md`.

## Setup

```bash
cp config.example.json config.json   # then fill in the paths / tokens
```

API keys (Anthropic, OpenAI) come from environment variables only — never put
them in `config.json`:

```bash
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...   # only if transcription.engine = "openai"
```

## Run

Backend (venv already provisioned at `.venv/`):

```bash
.venv/bin/uvicorn api.main:app --reload    # the API + the built cockpit, one process
```

Frontend:

```bash
cd web && npm run dev                       # available from Pass 3
```

Pipeline watcher:

```bash
.venv/bin/python -m pipeline                 # available from Pass 1
```

> The API serves the built cockpit from `web/dist` at `/`, so `uvicorn
> api.main:app` is the whole app in one process. Run it from the repo root (or
> set `BRAIN_COCKPIT_ROOT`). Build the frontend first: `cd web && npm ci && npm
> run build`. Auth is the `api.auth_token` from `config.json`.
