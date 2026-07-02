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
.venv/bin/uvicorn api.main:app --reload    # available from Pass 2
```

Frontend:

```bash
cd web && npm run dev                       # available from Pass 3
```

Pipeline watcher:

```bash
.venv/bin/python -m pipeline                 # available from Pass 1
```

> Build passes are additive. Pass 0 is the scaffold; `api.main`, the `pipeline`
> entrypoint, and the `web` app arrive in their respective passes.
