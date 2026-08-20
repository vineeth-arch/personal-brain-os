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
                   "vault/07-People", "inbox", "archive", "failed"]) {
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
  // a person well past a 3-day cadence — the People screen's whole reason to exist
  fs.writeFileSync(
    path.join(root, "vault", "07-People", "2026-07-01-priya-raman.md"),
    [
      "---", "id: 20260701090000", "type: person", "created: 2026-07-01",
      "source: manual", "origin: human", "relationship: client",
      "company: Alserkal Avenue",
      "channels: {whatsapp: +971500000001, email: priya@example.com}",
      "cadence_days: 3", "last_contact: 2026-06-01", "dex_id: dex-priya",
      "dex_deeplink:", "warmth_stage: conversing",
      "status: active", "---", "", "# Priya Raman", "", "## Context", "",
      "Met at a studio visit.", "", "## Interaction log", "",
      "- 2026-06-01 — coffee at Alserkal", "", "## Next action", "", "",
    ].join("\n"),
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
// prefer the repo venv; fall back to whatever uvicorn is on PATH (CI/containers
// install the deps system-wide and have no .venv)
const venvUvicorn = path.join(repo, ".venv", "bin", "uvicorn");
const uvicornBin = fs.existsSync(venvUvicorn) ? venvUvicorn : "uvicorn";
const uvicorn = spawn(uvicornBin,
  ["api.main:app", "--host", "127.0.0.1", "--port", String(PORT)],
  {
    cwd: repo,
    env: {
      ...process.env,
      BRAIN_COCKPIT_ROOT: root,
      OPENAI_API_KEY: "sk-e2e-dummy",
      // a Google client is "configured" so the Gmail/Calendar cards render in
      // their live form; no real Google call is ever made (see step 5)
      GOOGLE_CLIENT_ID: "e2e-client-id",
      GOOGLE_CLIENT_SECRET: "e2e-client-secret",
      // Dex "configured" so the push button renders; the contacts scope is
      // deliberately absent so the honest not-configured pill renders too.
      // No real Dex call is ever made (the push routes are stubbed in step 8).
      DEX_API_KEY: "e2e-dex-key",
    },
    stdio: ["ignore", "inherit", "inherit"],
  });

let browser;
let failed = false;
try {
  await waitForHealth();
  const { chromium } = loadPlaywright();
  // CI images often ship a chromium that predates the installed playwright;
  // COCKPIT_CHROMIUM points at it instead of demanding a matching download.
  // --use-fake-* give the headless browser a silent synthetic microphone, so
  // the mic button's getUserMedia resolves without a real device or a prompt.
  browser = await chromium.launch({
    ...(process.env.COCKPIT_CHROMIUM ? { executablePath: process.env.COCKPIT_CHROMIUM } : {}),
    args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
  });
  const context = await browser.newContext({ permissions: ["microphone"] });
  const page = await context.newPage();
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

  // ---- 5. Google: not connected → connected → draft (Pass 12) -------------------
  // The server's Google client is configured (env above) but no account is
  // linked, so the cards must offer Connect Google rather than pretending.
  await page.goto(`${BASE}/#/integrations`);
  const gmailCard = page.locator("article", { hasText: "Gmail" });
  await gmailCard.getByRole("button", { name: "Connect Google" }).waitFor();
  console.log("✓ Gmail card offers Connect Google while unlinked");

  // Link an account by writing the refresh token the way the OAuth callback
  // would, then bust the 60s health-card cache from the UI.
  const linked = JSON.parse(fs.readFileSync(path.join(root, "config.json"), "utf8"));
  linked.google = { refresh_token: "e2e-refresh-token" };
  fs.writeFileSync(path.join(root, "config.json"), JSON.stringify(linked, null, 2));

  // Google itself is never called: intercept the cockpit's own read routes and
  // serve fixtures, which is what the real cards render from.
  await page.route("**/api/google/inbox", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [
        { id: "m1", from: "Priya Raman <priya@example.com>", subject: "Re: studio visit",
          date: "2026-08-12T09:00:00", snippet: "Works for me.",
          url: "https://mail.google.com/mail/u/0/#inbox/m1" },
        { id: "m2", from: "billing@hetzner.com", subject: "Your invoice for August",
          date: "2026-08-12T02:00:00", snippet: "Invoice available.",
          url: "https://mail.google.com/mail/u/0/#inbox/m2" },
      ] }),
    }));
  await page.route("**/api/google/events", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [
        { id: "e1", summary: "Studio visit", start: "2026-08-13T12:00:00",
          end: "2026-08-13T13:00:00", all_day: false, location: "Alserkal",
          url: "https://calendar.google.com/e1" },
      ] }),
    }));

  await page.getByRole("button", { name: "Recheck all" }).click();
  await gmailCard.getByText("Connected").waitFor();
  await gmailCard.getByText("2 unread").waitFor();
  await gmailCard.getByText("Re: studio visit").waitFor();
  const calCard = page.locator("article", { hasText: "Google Calendar" });
  await calCard.getByText("1 in the next 7 days").waitFor();
  await calCard.getByText("Studio visit").first().waitFor();
  console.log("✓ Connected Google cards render live mail + events");

  // The draft composer must never send: assert the POST goes to /drafts only.
  let draftPosted = null;
  await page.route("**/api/google/draft", async (route) => {
    draftPosted = JSON.parse(route.request().postData() || "{}");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: "draft-1", url: "https://mail.google.com/mail/u/0/#drafts" }),
    });
  });
  await gmailCard.getByRole("button", { name: "New draft" }).click();
  await gmailCard.getByLabel("To").fill("priya@example.com");
  await gmailCard.getByLabel("Subject").fill("Thursday works");
  await gmailCard.getByLabel("Message").fill("See you at the studio.");
  await gmailCard.getByRole("button", { name: "Save draft to Gmail" }).click();
  // the exact copy matters: the cockpit cannot send, so it hands the send back
  await page.getByText("Draft saved — send it from Gmail.").waitFor();
  assert.deepEqual(draftPosted,
    { to: "priya@example.com", subject: "Thursday works", text: "See you at the studio." },
    "draft body was not what the composer showed");
  console.log("✓ Draft composer saves a draft and says to send it from Gmail");

  // Rule 4, from the outside: the API must not answer a send route at all.
  // (404 from the router, or 405 from the static mount which only serves GET —
  // either way nothing handles it; what matters is that it never succeeds.)
  for (const sendPath of ["/api/google/send", "/api/google/messages/send"]) {
    const probe = await fetch(`${BASE}${sendPath}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
      body: JSON.stringify({ to: "x@y.z", subject: "s", text: "t" }),
    });
    assert.ok(!probe.ok, `${sendPath} answered ${probe.status} — CLAUDE.md §4 forbids a send path`);
    assert.ok([404, 405].includes(probe.status), `unexpected status ${probe.status} for ${sendPath}`);
  }
  console.log("✓ No send route exists on the API");

  // ---- 6. Mic capture: record in the PWA → a file lands in the inbox (Pass V) ---
  await page.goto(`${BASE}/#/today`);
  const micButton = page.getByRole("button", { name: "Record a voice capture" });
  await micButton.waitFor();
  await micButton.click();
  // the running timer is the proof it armed, not just that the button toggled
  await page.getByRole("button", { name: "Stop recording" }).waitFor();
  await page.getByText("Tap to stop").waitFor();
  await new Promise((r) => setTimeout(r, 1200));   // let the recorder collect audio
  await page.getByRole("button", { name: "Stop recording" }).click();
  await page.getByText("✅ Captured").waitFor();

  const inboxDir = path.join(root, "inbox");
  let recorded = null;
  for (let i = 0; i < 50; i++) {
    recorded = fs.readdirSync(inboxDir).filter((f) => !f.startsWith("."));
    if (recorded.length) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  assert.ok(recorded.length === 1, `expected one recording in the inbox, saw ${JSON.stringify(recorded)}`);
  const stamped = recorded[0];
  assert.match(stamped, /^\d{4}-\d{2}-\d{2}-\d{4} voice-note\.(webm|mp4|ogg)$/,
    `recording filename '${stamped}' is not the stamp intake parses`);
  assert.ok(fs.statSync(path.join(inboxDir, stamped)).size > 0, "the recording landed empty");
  console.log(`✓ Mic capture wrote ${stamped} into the inbox`);

  // ---- 7. People: draft refuses without a voice, then works (Pass MW) ----------
  await page.goto(`${BASE}/#/people`);
  const card = page.locator("article", { hasText: "Priya Raman" });
  await card.waitFor();
  await card.getByText("Past a 3-day cadence").waitFor();
  console.log("✓ People screen shows the going-cold person with the reason");

  // no my-voice.md yet → the drawer must refuse rather than draft generically
  await card.getByRole("button", { name: "Draft a message" }).click();
  await page.getByText("Drafts need your own voice on file first.").waitFor();
  await page.keyboard.press("Escape");   // the drawer's own escape hatch
  await page.getByRole("dialog").waitFor({ state: "detached" });
  console.log("✓ Drafting refuses honestly until my-voice.md exists");

  // teach it a voice through the real Settings card, then assert the vault file
  await page.goto(`${BASE}/#/settings`);
  await page.getByLabel("Writing samples").fill(
    "hey! sorry for the slow reply — this week has been mad\n\nSounds good. Tuesday 4pm works.");
  await page.getByRole("button", { name: "Save my voice" }).click();
  await page.getByText("✅ Voice saved").waitFor();
  const voiceFile = path.join(root, "vault", "_System", "my-voice.md");
  assert.ok(fs.existsSync(voiceFile), "my-voice.md was not written to the vault");
  assert.match(fs.readFileSync(voiceFile, "utf8"), /sorry for the slow reply/);
  console.log("✓ Settings → My voice writes _System/my-voice.md");

  // the model itself is stubbed: this checks the drawer, not the provider chain
  await page.route("**/api/people/*/draft", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        text: "hey Priya — long time. still thinking about that studio conversation.",
        channel: "whatsapp",
        channels: { whatsapp: "+971500000001", email: "priya@example.com" },
        provider: "claude-haiku",
      }),
    }));

  await page.goto(`${BASE}/#/people`);
  await page.locator("article", { hasText: "Priya Raman" })
    .getByRole("button", { name: "Draft a message" }).click();
  const drawer = page.getByRole("dialog");
  await drawer.getByLabel("Draft message").waitFor();
  assert.match(await drawer.getByLabel("Draft message").inputValue(), /hey Priya/);

  // CLAUDE.md §4 from the outside: the channel button is a deep link the human
  // taps, and it must be a link — never a button that posts a send.
  const channelLink = drawer.getByRole("link", { name: "Open WhatsApp" });
  const href = await channelLink.getAttribute("href");
  assert.ok(href.startsWith("https://wa.me/971500000001?text="),
    `channel deep link was ${href}`);
  console.log("✓ Draft drawer renders the draft and a tap-to-open channel link");

  // logging contact resets the counter on disk and commits the vault
  await drawer.getByRole("button", { name: "Log contact" }).click();
  await page.getByText("✅ Logged").waitFor();
  const personFile = path.join(root, "vault", "07-People", "2026-07-01-priya-raman.md");
  const personText = fs.readFileSync(personFile, "utf8");
  assert.ok(!personText.includes("last_contact: 2026-06-01"), "last_contact was not reset");
  assert.ok(personText.includes("- 2026-06-01 — coffee at Alserkal"),
    "the interaction log must be append-only");
  const vaultLog = execSync(`git -C "${path.join(root, "vault")}" log -1 --format=%s`).toString();
  assert.match(vaultLog, /logged contact/);
  console.log("✓ Log contact resets the counter, appends to the log, commits the vault");

  // ---- 8. Profile push: preview → confirm (Pass D) ---------------------------
  // Dex itself is stubbed at the cockpit's own routes — what is under test is
  // the human gate: the exact text must be shown, and the confirmed text must
  // be what gets posted.
  let pushedBody = null;
  await page.route("**/api/people/*/push/preview", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        target: "dex",
        person_id: "20260701090000",
        name: "Priya Raman",
        summary: "Priya runs the artist programme at Alserkal.\nLast spoke on 2026-06-01.",
        block: "<!-- BRAIN-OS -->\nPriya runs the artist programme at Alserkal.\n"
          + "Last spoke on 2026-06-01.\n· via Brain OS 2026-08-20\n<!-- /BRAIN-OS -->",
        destination: "Dex contact dex-priya · description",
        replaced: "",
      }),
    }));
  await page.route("**/api/people/*/push", (route) => {
    if (route.request().url().includes("/preview")) return route.fallback();
    pushedBody = JSON.parse(route.request().postData() || "{}");
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true, target: "dex",
        changed: "Dex contact dex-priya · description", replaced: false,
      }),
    });
  });

  // step 7's drawer is still open on the "move them to conversing?" question
  await drawer.getByRole("button", { name: "Not yet" }).click();
  await page.goto(`${BASE}/#/people`);
  // step 7 logged contact, so she is inside her cadence now and the default
  // "Needs you" filter correctly hides her — push is not only for the overdue
  await page.getByRole("button", { name: "Everyone" }).click();
  await page.locator("article", { hasText: "Priya Raman" })
    .getByRole("button", { name: "Draft a message" }).click();
  const pushDrawer = page.getByRole("dialog");
  await pushDrawer.getByRole("button", { name: "Push to Dex" }).click();

  // the preview must show the real text BEFORE anything can be confirmed
  await pushDrawer.getByText("Read it before it goes").waitFor();
  await pushDrawer.getByText("<!-- BRAIN-OS -->").waitFor();
  assert.equal(pushedBody, null, "a preview must not write anything");

  // Google contacts isn't connected in this fixture — an honest pill, not a button
  await pushDrawer.getByText("Reconnect Google for contacts").waitFor();

  await pushDrawer.getByRole("button", { name: "Confirm push" }).click();
  await page.getByText("✅ Updated Dex contact dex-priya · description").waitFor();
  assert.equal(pushedBody?.target, "dex", "the confirmed push named the wrong target");
  assert.match(pushedBody?.text ?? "", /artist programme at Alserkal/,
    "the text posted was not the text the human read");
  console.log("✓ Profile push previews the exact text and only writes on confirm");

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
