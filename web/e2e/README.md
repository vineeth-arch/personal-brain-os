# End-to-end check (Pass 4 gate)

`run-e2e.mjs` drives the built cockpit against the **real** API in a throwaway
temp root, with a real Chromium. It proves the Settings engine toggle rewrites
`config.json` on disk, that Integrations reflects the change without a server
restart, and that link cards render from `config.links` (unknown keys as
lettermark tiles).

## Why no `@playwright/test` dependency

The constitution (CLAUDE.md §7) locks the frontend dependency list to
react / vite / typescript / tailwind. The script therefore uses a **globally
installed** playwright instead of adding one to `web/package.json`:

```sh
npm install -g playwright      # once per machine
npx playwright install chromium  # once per machine (downloads the browser)
```

(Environments that pre-install Playwright + Chromium — e.g. with
`PLAYWRIGHT_BROWSERS_PATH` already set — need neither step.)

## Run it

```sh
cd web && npm run build && cd ..   # the API serves web/dist
node web/e2e/run-e2e.mjs
```

Exit code 0 = all checks passed. The script builds a temp server root
(tmp vault git-inited, `config.json` with an `e2e-token`), launches
`.venv/bin/uvicorn api.main:app` on port 8765 with a dummy `OPENAI_API_KEY`
(so the engine switch is allowed; no real OpenAI call happens), and cleans
everything up afterwards.
