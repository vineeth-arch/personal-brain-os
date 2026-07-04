#!/usr/bin/env node
// Pass 4 gate: end-to-end check against the REAL API (uvicorn api.main:app),
// not the mock. Uses the globally-installed playwright package + browsers so
// web/package.json keeps its locked dependency set (CLAUDE.md §7) — see
// e2e/README.md for the one-time global install.
//
// What it proves:
//   1. Toggling the engine in Settings (with the cloud-caution confirm)
//      actually rewrites config.json on disk.
//   2. The Integrations screen reflects the change WITHOUT a server restart
//      (the config write busts the server-side health-card cache).
//   3. Link cards render from config.links — including an unknown key drawn
//      as a lettermark tile.
//
// Run from the repo root:  node web/e2e/run-e2e.mjs
import assert from "node:assert";
import { execSync, spawn } from "node:child_process";
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const TOKEN = "e2e-token";
const PORT = 8765;
const BASE = `http://127.0.0.1:${PORT}`;

// ---- resolve the GLOBAL playwright install (no local dependency) -------------
function loadPlaywright() {
  try {
    return createRequire(import.meta.url)("playwright");
  } catch {
    const globalRoot = execSync("npm root -g").toString().trim();
    return createRequire(path.join(globalRoot, "noop.js"))("playwright");
  }
}

// ---- temp server root ---------------------------------------------------------
function makeRoot() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "cockpit-e2e-"));
  for (const d of ["vault/00-Inbox", "vault/02-Musings", "vault/03-Learnings", "vault/wiki",
                   "inbox", "archive", "failed"]) {
    fs.mkdirSync(path.join(root, d), { recursive: true });
  }
  const vault = path.join(root, "vault");
  execSync(`git -C "${vault}" init -q && git -C "${vault}" config user.email t@t && git -C "${vault}" config user.name t`);
  fs.writeFileSync(
    path.join(root, "config.json"),
    JSON.stringify({
      vault_path: vault,
      inbox_path: path.join(root, "inbox"),
      archive_path: path.join(root, "archive"),
      failed_path: path.join(root, "failed"),
      transcription: { engine: "whispercpp", whispercpp: { binary_path: "", model_path: "" } },
      ntfy: { url: "", topic: "" },
      api: { auth_token: TOKEN },
      classification: { confidence_threshold: 0.7 },
      links: { dex: "https://getdex.com/", notion: "https://www.notion.so/x" },
    }, null, 2),
  );
  // create_app serves the built cockpit from <root>/web/dist
  fs.symlinkSync(path.join(repo, "web"), path.join(root, "web"));
  return root;
}

async function waitForHealth() {
  for (let i = 0; i < 100; i++) {
    try {
      const res = await fetch(`${BASE}/api/health`);
      if (res.ok) return;
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`API never answered on ${BASE}/api/health`);
}

// ---- the run --------------------------------------------------------------------
const dist = path.join(repo, "web", "dist", "index.html");
if (!fs.existsSync(dist)) {
  console.error("web/dist is missing — build first: cd web && npm run build");
  process.exit(2);
}

const root = makeRoot();
const uvicorn = spawn(path.join(repo, ".venv", "bin", "uvicorn"),
  ["api.main:app", "--host", "127.0.0.1", "--port", String(PORT)],
  {
    cwd: repo,
    env: { ...process.env, BRAIN_COCKPIT_ROOT: root, OPENAI_API_KEY: "sk-e2e-dummy" },
    stdio: ["ignore", "inherit", "inherit"],
  });

let browser;
let failed = false;
try {
  await waitForHealth();
  const { chromium } = loadPlaywright();
  browser = await chromium.launch();
  const page = await browser.newPage();
  // seed the connection before any script runs — skips the TokenGate screen
  await page.addInitScript(([base, token]) => {
    localStorage.setItem("cockpit.apiBase", base);
    localStorage.setItem("cockpit.token", token);
  }, [BASE, TOKEN]);

  // ---- 1. Settings: engine toggle with confirm rewrites config.json ------------
  await page.goto(`${BASE}/#/settings`);
  await page.getByRole("button", { name: "OpenAI (cloud)" }).click();
  await page.getByText("Cloud transcription sends your audio to OpenAI.").waitFor();
  await page.getByRole("button", { name: "Use cloud anyway" }).click();
  await page.getByText("Transcription engine → OpenAI (cloud fallback).").waitFor();
  const saved = JSON.parse(fs.readFileSync(path.join(root, "config.json"), "utf8"));
  assert.equal(saved.transcription.engine, "openai", "config.json engine did not change");
  assert.deepEqual(saved.links, { dex: "https://getdex.com/", notion: "https://www.notion.so/x" },
    "config.json links were not preserved by the write");
  console.log("✓ Settings engine toggle → config.json now says openai");

  // ---- 2. Integrations reflects the switch without a restart -------------------
  await page.goto(`${BASE}/#/integrations`);
  const openaiCard = page.locator("article", { hasText: "Transcription — OpenAI" });
  await openaiCard.getByText("· active").waitFor();
  console.log("✓ Integrations shows OpenAI as the active engine (no restart)");

  // ---- 3. Link cards render from config.links (incl. unknown key) --------------
  await page.locator("article", { hasText: "Obsidian" }).first().waitFor();
  await page.locator("article", { hasText: "Dex" }).first().waitFor();
  const notionCard = page.locator("article", { hasText: "Notion" });
  await notionCard.getByRole("link", { name: "Open ↗" }).waitFor();
  assert.equal(await notionCard.getByRole("link", { name: "Open ↗" }).getAttribute("href"),
    "https://www.notion.so/x");
  console.log("✓ Link cards render from config.links, unknown key included");

  // ---- 4. And back to local, for symmetry ---------------------------------------
  await page.goto(`${BASE}/#/settings`);
  await page.getByRole("button", { name: "Local whisper.cpp" }).click();
  await page.getByText("Transcription engine → local whisper.cpp.").waitFor();
  const saved2 = JSON.parse(fs.readFileSync(path.join(root, "config.json"), "utf8"));
  assert.equal(saved2.transcription.engine, "whispercpp");
  console.log("✓ Toggled back to local whisper.cpp");

  console.log("\nE2E: all checks passed.");
} catch (err) {
  failed = true;
  console.error("\nE2E FAILED:", err);
} finally {
  if (browser) await browser.close();
  uvicorn.kill("SIGTERM");
  fs.rmSync(root, { recursive: true, force: true });
}
process.exit(failed ? 1 : 0);
