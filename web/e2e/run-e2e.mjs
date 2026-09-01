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
  try {
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
  } catch (err) {
    // D23: without this, a missing `git` (or any setup failure) leaves the
    // mkdtemp'd directory on disk forever and crashes with a raw Node trace
    // instead of the same clean "E2E FAILED" the rest of the run reports.
    fs.rmSync(root, { recursive: true, force: true });
    throw new Error(`Could not set up the e2e temp root (is git installed?): ${err.message}`);
  }
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

// POST /api/run, retrying on 409 ("a run is already in flight") instead of
// asserting immediate success. Earlier steps only poll the filesystem for
// their own expected output, not for the pipeline subprocess itself to
// exit — writing the note is one step inside that subprocess, not the last
// one (archiving the source, committing the vault, etc. still follow) — so
// the next /api/run can legitimately land while the previous run is still
// finishing up. That overlap is correctly rejected by the server; the test
// just needs to wait it out rather than treat it as a failure.
async function runPipeline(label) {
  for (let i = 0; i < 50; i++) {
    const res = await fetch(`${BASE}/api/run`, {
      method: "POST", headers: { Authorization: `Bearer ${TOKEN}` },
    });
    if (res.status === 202) return;
    if (res.status !== 409) {
      assert.equal(res.status, 202, `pipeline run did not start for ${label}`);
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`pipeline run never started for ${label} — a previous run never finished`);
}

// ---- the run --------------------------------------------------------------------
const dist = path.join(repo, "web", "dist", "index.html");
if (!fs.existsSync(dist)) {
  console.error("web/dist is missing — build first: cd web && npm run build");
  process.exit(2);
}

let root;
let uvicorn;
let browser;
let failed = false;
try {
  root = makeRoot();
  // prefer the repo venv; fall back to whatever uvicorn is on PATH (CI/containers
  // install the deps system-wide and have no .venv)
  const venvUvicorn = path.join(repo, ".venv", "bin", "uvicorn");
  const uvicornBin = fs.existsSync(venvUvicorn) ? venvUvicorn : "uvicorn";
  uvicorn = spawn(uvicornBin,
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
  // D23: spawn() failing outright (binary not found, permission denied) fires
  // an async 'error' event on its own tick — a throw here would NOT be caught
  // by the try/catch below (it isn't on that call stack), and with no
  // listener at all Node crashes the whole process unhandled, skipping the
  // finally cleanup. So this listener does the cleanup itself.
  uvicorn.on("error", (err) => {
    console.error(`\nE2E FAILED: uvicorn failed to start (${uvicornBin}): ${err.message}`);
    if (browser) browser.close().catch(() => {});
    fs.rmSync(root, { recursive: true, force: true });
    process.exit(1);
  });

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
  // Typing into the quick-capture box first also exercises D22: that text is
  // sent as ?name= when the recording stops, so the stamped filename carries
  // it instead of the generic "voice-note" fallback.
  // (Photo capture through the real pipeline is covered later — step 11,
  // "Pass V2/V3/V4" — which supersedes this file's earlier Pass 13 version:
  // that one asserted a multipart upload + a `.meta.json` sidecar, neither
  // of which exist anymore now that image capture uses the same raw-body
  // streaming transport as audio, with a `.insight` dotfile sidecar instead.)
  await page.goto(`${BASE}/#/today`);
  await page.getByLabel("Quick capture").fill("garden notes for later");
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
    // scoped to audio extensions defensively — nothing drains the inbox
    // between e2e steps (there's no watcher running here), and a later step
    // shares this same inbox for photo capture
    recorded = fs.readdirSync(inboxDir).filter((f) => /\.(webm|mp4|ogg)$/.test(f));
    if (recorded.length) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  assert.ok(recorded.length === 1, `expected one recording in the inbox, saw ${JSON.stringify(recorded)}`);
  const stamped = recorded[0];
  assert.match(stamped, /^\d{4}-\d{2}-\d{2}-\d{4} garden-notes-for-later\.(webm|mp4|ogg)$/,
    `recording filename '${stamped}' doesn't carry the quick-capture text (D22)`);
  assert.ok(fs.statSync(path.join(inboxDir, stamped)).size > 0, "the recording landed empty");
  // the text box is deliberately NOT cleared by a recording — it may still
  // become its own separate text capture
  assert.equal(await page.getByLabel("Quick capture").inputValue(), "garden notes for later");
  console.log(`✓ Mic capture wrote ${stamped} into the inbox, carrying the quick-capture text`);

  // ---- 8. People: draft refuses without a voice, then works (Pass MW) ----------
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

  // ---- 9. Profile push: preview → confirm (Pass D) ---------------------------
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

  // ---- 10. Quick-add a warm-up target (Pass X) --------------------------------
  // One name, one channel, one tap — and a schema-correct note on disk.
  await page.keyboard.press("Escape");   // the drawer closes on Escape
  await page.goto(`${BASE}/#/people`);
  await page.getByRole("button", { name: "+ Target" }).click();
  await page.getByLabel("Name").fill("Sara Khalid");
  await page.getByRole("button", { name: "email", exact: true }).click();
  await page.getByLabel("Email").fill("sara@example.com");
  await page.getByRole("button", { name: "Add target" }).click();
  await page.getByText("✅ Added Sara Khalid").waitFor();

  const peopleDir = path.join(root, "vault", "07-People");
  const targetFile = fs.readdirSync(peopleDir).find((f) => f.includes("sara-khalid"));
  assert.ok(targetFile, `no note written for the new target (saw ${fs.readdirSync(peopleDir)})`);
  const targetText = fs.readFileSync(path.join(peopleDir, targetFile), "utf8");
  assert.match(targetText, /type: person/);
  assert.match(targetText, /origin: human/);          // the owner typed it
  assert.match(targetText, /warmth_stage: identified/); // spotted, not researched
  assert.match(targetText, /channels: \{email: sara@example\.com\}/);
  for (const section of ["## Context", "## Needs", "## Interaction log", "## Next action"]) {
    assert.ok(targetText.includes(section), `${section} missing from the new note`);
  }
  const addLog = execSync(`git -C "${path.join(root, "vault")}" log -1 --format=%s`).toString();
  assert.match(addLog, /added target Sara Khalid/);
  console.log("✓ Quick-add writes a schema-correct person note and commits the vault");

  // ---- 10. Share-to-save: a {url, insight} capture becomes a resource note ----
  // (Pass S). No UI trigger yet — this is exactly what the "→ Brain Cloud"
  // Shortcut sends. Drives the REAL pipeline via POST /api/run (no stubbed
  // enrichment — a genuinely unreachable URL is fine, the note is written
  // unconditionally either way, per the Pass L principle).
  const shareRes = await fetch(`${BASE}/api/capture`, {
    method: "POST",
    headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      url: "https://example.com/e2e-share-test",
      insight: "the branding hook at the start is the whole idea",
    }),
  });
  assert.equal(shareRes.status, 201, "share capture was not accepted");

  await runPipeline("the share fixture");

  const resourcesDir = path.join(root, "vault", "04-Resources");
  let sharedNote = null;
  for (let i = 0; i < 100; i++) {
    if (fs.existsSync(resourcesDir)) {
      const found = fs.readdirSync(resourcesDir)
        .map((f) => path.join(resourcesDir, f))
        .find((p) => fs.readFileSync(p, "utf8").includes("e2e-share-test"));
      if (found) { sharedNote = found; break; }
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  assert.ok(sharedNote, "the shared link never became a resource note");
  const sharedText = fs.readFileSync(sharedNote, "utf8");
  assert.match(sharedText, /type: resource/);
  assert.match(sharedText, /source_url: https:\/\/example\.com\/e2e-share-test/);
  assert.match(sharedText, /## Insight/);
  assert.match(sharedText, /branding hook at the start/);
  // the URL itself must NOT be duplicated inside ## Insight (Pass S2)
  const insightBlock = sharedText.split("## Insight")[1] || "";
  assert.ok(!insightBlock.includes("https://example.com/e2e-share-test"),
    "the raw URL leaked into ## Insight instead of staying only in source_url");
  console.log("✓ Share capture ({url, insight}) becomes a resource note, URL kept out of ## Insight");

  // search finds it by the insight text alone, not just the title (Pass S4)
  const searchRes = await fetch(
    `${BASE}/api/resources?q=${encodeURIComponent("branding hook")}`,
    { headers: { Authorization: `Bearer ${TOKEN}` } });
  const searchBody = await searchRes.json();
  assert.ok(searchBody.items.some((i) => i.url === "https://example.com/e2e-share-test"),
    "search by insight text did not find the shared note");
  console.log("✓ Resource search matches on the saved insight, not just the title");

  // ---- 11. Photo capture: the cockpit's own photo button (Pass V2/V3/V4) ------
  // A real file through the real decode→downscale→JPEG pipeline in the
  // browser, then the real API + one pipeline pass. ANTHROPIC_API_KEY is
  // deliberately absent from this server's env (only OPENAI/GOOGLE/DEX keys
  // are set above), so this also proves the "vision unavailable → honest,
  // undescribed note" degradation path (Pass V3's D-VISION principle).
  const tinyPng = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
    "base64");
  const tinyPngPath = path.join(root, "tiny.png");
  fs.writeFileSync(tinyPngPath, tinyPng);

  await page.goto(`${BASE}/#/today`);
  const photoInput = page.locator('input[type="file"][aria-label="Attach a photo"]');
  await photoInput.waitFor({ state: "attached" });
  await photoInput.setInputFiles(tinyPngPath);
  await page.getByText("✅ Captured").waitFor();

  // the toast fires optimistically, before the upload's own await settles
  // (same pattern as the mic button) — poll the inbox for the upload to
  // actually land before triggering a pipeline run, so /api/run doesn't
  // race the in-flight request.
  const photoInboxDir = path.join(root, "inbox");
  let photoLanded = false;
  for (let i = 0; i < 50; i++) {
    if (fs.readdirSync(photoInboxDir).some((f) => !f.startsWith("."))) { photoLanded = true; break; }
    await new Promise((r) => setTimeout(r, 100));
  }
  assert.ok(photoLanded, "the photo upload never landed in the inbox");

  await runPipeline("the photo capture");

  const attachmentsDir = path.join(root, "vault", "attachments");
  let attachment = null;
  for (let i = 0; i < 100; i++) {
    if (fs.existsSync(attachmentsDir)) {
      const found = fs.readdirSync(attachmentsDir);
      if (found.length) { attachment = found[0]; break; }
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  assert.ok(attachment, "the photo never landed in vault/attachments");
  assert.ok(fs.statSync(path.join(attachmentsDir, attachment)).size > 0, "the attachment landed empty");

  let photoNote = null;
  for (let i = 0; i < 50; i++) {
    const found = fs.readdirSync(resourcesDir)
      .map((f) => path.join(resourcesDir, f))
      .find((p) => fs.readFileSync(p, "utf8").includes(`attachments/${attachment}`));
    if (found) { photoNote = found; break; }
    await new Promise((r) => setTimeout(r, 100));
  }
  assert.ok(photoNote, "no resource note references the captured photo");
  const photoText = fs.readFileSync(photoNote, "utf8");
  assert.match(photoText, /platform: photo/);
  assert.match(photoText, /enriched: false/);   // no ANTHROPIC_API_KEY in this server's env
  assert.match(photoText, new RegExp(`!\\[\\[attachments/${attachment}\\]\\]`));
  console.log("✓ Photo capture: browser downscale → attachments/ → honest undescribed resource note");

  // ---- 12. Whole-vault search (Pass Q) -------------------------------------------
  const searchWord = "trellisworthy";
  const searchCaptureRes = await fetch(`${BASE}/api/capture`, {
    method: "POST",
    headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify({ text: `a ${searchWord} garden structure idea`, tag: "idea" }),
  });
  assert.equal(searchCaptureRes.status, 201, "seed capture for search did not land");
  await runPipeline("the search fixture");

  // idea → musing (classify.TAG_TO_TYPE)
  const musingsDir = path.join(root, "vault", "02-Musings");
  let searchNoteFound = false;
  for (let i = 0; i < 100; i++) {
    if (fs.existsSync(musingsDir) &&
        fs.readdirSync(musingsDir).some((f) =>
          fs.readFileSync(path.join(musingsDir, f), "utf8").includes(searchWord))) {
      searchNoteFound = true;
      break;
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  assert.ok(searchNoteFound, "the search fixture note never landed in 02-Musings");

  await page.goto(`${BASE}/#/search`);
  await page.getByLabel("Search everything in the vault").fill(searchWord);
  await page.getByText(new RegExp(`${searchWord}`, "i")).first().waitFor();
  await page.getByText(/^1 result$/).waitFor();
  const obsidianHref = await page.getByRole("link", { name: "Open in Obsidian" }).first().getAttribute("href");
  assert.ok(obsidianHref && obsidianHref.startsWith("obsidian://open?vault="),
    `search result's Obsidian link was ${obsidianHref}`);

  // a too-short query shows the "keep typing" hint, not a request at all
  await page.getByLabel("Search everything in the vault").fill("a");
  await page.getByText(`Keep typing — at least 2 characters.`).waitFor();
  console.log("✓ Whole-vault search finds a seeded note and links back to Obsidian");

  // ---- 13. Triage is a bounded queue, not a backlog (Pass A) ------------------
  // Seven notes parked in review; Triage must offer five, then exactly as many
  // more as the human asks for. Real inbox notes on disk — the review queue is
  // a filesystem scan of 00-Inbox for status: needs-review (api/notes.py).
  const triageInbox = path.join(root, "vault", "00-Inbox");
  assert.equal(fs.readdirSync(triageInbox).length, 0,
    "00-Inbox wasn't empty before the triage fixtures — the counts below would be wrong");
  const seedReviewNote = (day, title, type) =>
    fs.writeFileSync(
      path.join(triageInbox, `2026-06-0${day}-${title}.md`),
      [
        "---", `id: 2026060${day}090000`, `type: ${type}`, `created: 2026-06-0${day}`,
        "source: text", "origin: human", "meta_origin: ai", "status: needs-review",
        "categories: []", "subjects: []", "tags: []", "---", "",
        `Something worth deciding about — ${title}.`, "",
      ].join("\n"),
    );
  seedReviewNote(1, "triage-oldest", "learning");
  for (let n = 2; n <= 7; n++) seedReviewNote(n, `triage-note-${n}`, "musing");

  await page.goto(`${BASE}/#/triage`);
  await page.getByText("Showing 5 of 7 — one decision each.").waitFor();
  const cards = page.locator("article[data-review-card]");
  assert.equal(await cards.count(), 5, "Triage rendered something other than one slice of five");
  // oldest first: the note that has waited longest is the one being asked about
  await cards.first().getByRole("heading", { name: "triage-oldest" }).waitFor();
  await page.getByRole("button", { name: "2 more" }).waitFor();
  console.log("✓ Triage shows 5 of 7, oldest first, with 2 more on offer");

  // one decision — the sixth card slides in, the slice stays five
  await cards.first().getByRole("button", { name: "Approve as learning" }).click();
  await page.getByText("Approved as learning").waitFor();
  await page.getByText("Showing 5 of 6 — one decision each.").waitFor();
  assert.equal(await cards.count(), 5, "the queue didn't refill to five after one decision");
  await page.getByRole("button", { name: "1 more" }).click();
  await page.getByText("Showing 6 of 6 — one decision each.").waitFor();
  assert.equal(await cards.count(), 6, "asking for more didn't render the rest of the queue");
  assert.equal(await page.getByRole("button", { name: /^\d+ more$/ }).count(), 0,
    "the more button stayed after the queue was exhausted");
  console.log("✓ Deciding one refills the slice; [n more] expands once and then goes away");

  // ---- 14. Trust boundary line (Pass A, Task A6) -------------------------------
  // Approve the remaining six to reach inbox zero, then read the honest sentence:
  // 7 notes gated by a human this month, 0 drained (the drain never ran here).
  const waitForCardCount = (n) =>
    page.waitForFunction(
      (count) => document.querySelectorAll("article[data-review-card]").length === count,
      n,
    );
  for (let remaining = 6; remaining >= 1; remaining--) {
    await cards.first().getByRole("button", { name: /^Approve as /, exact: false }).click();
    await waitForCardCount(remaining - 1);
  }
  // usePolling(api.review) fetched once on mount, before any of these seven
  // approvals — a reload forces a fresh /api/review so trust reflects them.
  await page.reload();
  await page.getByText("Inbox zero.").waitFor();
  await page
    .getByText("Nothing enters your vault without you — 7 notes gated this month.")
    .waitFor();
  console.log("✓ Triage empty state states the trust boundary — 7 gated, 0 drained (dropped)");

  // Settings shows the identical sentence — same trustLine(), same /api/review data.
  await page.goto(`${BASE}/#/settings`);
  await page
    .getByText("Nothing enters your vault without you — 7 notes gated this month.")
    .waitFor();
  console.log("✓ Settings shows the same trust line as Triage's empty state");

  // ---- 15. PWA offline outbox: idempotent retry never creates a duplicate (Task F4) ---
  // Task F3 built the server-side X-Capture-Key dedup; this proves the client
  // actually uses it. A plain page.route(...).abort() blocks a request before
  // it ever reaches the server, so a failure there can't test dedup on its
  // own (there's nothing server-side to deduplicate against). So this test
  // does two things: (a) confirms a live capture that fails purely
  // client-side gets queued, and (b) forwards a QUEUED retry to the real
  // server (so it genuinely writes a note) while still failing the response
  // back to the browser — the exact "silently landed" scenario F3/F4 exist
  // for — then retries that same still-queued item a second time and proves
  // the server's key-based dedup, not just the outbox's plumbing, is what
  // kept the file count at one.
  await page.goto(`${BASE}/#/today`);
  assert.equal(await page.getByRole("button", { name: "Send now" }).count(), 0,
    "outbox pill/Send-now rendered before anything was queued");

  const outboxWord = "quibblewroughtcapture";
  const outboxInboxDir = path.join(root, "inbox");
  const filesContaining = (word) =>
    fs.readdirSync(outboxInboxDir).filter((f) => {
      try {
        return fs.readFileSync(path.join(outboxInboxDir, f), "utf8").includes(word);
      } catch {
        return false; // non-text (e.g. audio) files from earlier steps
      }
    });
  const waitForFilesContaining = async (word, timeoutMs = 5000) => {
    const start = Date.now();
    let found = filesContaining(word);
    while (!found.length && Date.now() - start < timeoutMs) {
      await new Promise((r) => setTimeout(r, 100));
      found = filesContaining(word);
    }
    return found;
  };

  // (a) live attempt: no X-Capture-Key on this hop (the live path never
  // sends one) — aborted purely client-side, never reaches the server.
  let liveAttemptHits = 0;
  await page.route("**/api/capture", (route) => {
    liveAttemptHits++;
    return route.abort();
  });
  await page.getByLabel("Quick capture").fill(`${outboxWord} first try`);
  await page.getByRole("button", { name: "Capture", exact: true }).click();
  await page.getByText("Saved to outbox — sends when you're back online.").waitFor();
  await page.getByText("1 queued").waitFor();
  await page.unroute("**/api/capture");
  assert.equal(liveAttemptHits, 1, "the live capture attempt should have hit the route exactly once");
  console.log("✓ A network failure on the live capture path queues to the outbox");

  // (a2) Regression test for the "swallowed IndexedDB write failure" bug: if
  // enqueue()'s own IndexedDB write fails (private browsing, disabled IDB,
  // quota), the caller must fall through to the EXISTING error/restore-input
  // behavior — not silently report "Saved to outbox" while the text is lost
  // for good. Break the real IndexedDB and prove the old fallback UI still
  // fires, with the typed text still visible, not thrown away.
  await page.evaluate(() => {
    // test-only monkeypatch: shadow the prototype's open() with one that
    // always throws synchronously, simulating a broken IndexedDB
    window.indexedDB.open = () => {
      throw new Error("simulated IndexedDB failure (e2e)");
    };
  });
  await page.route("**/api/capture", (route) => route.abort());
  const fallbackWord = "wortleblazepending";
  await page.getByLabel("Quick capture").fill(fallbackWord);
  await page.getByRole("button", { name: "Capture", exact: true }).click();
  // the pre-existing network-failure fallback: an error toast and the typed
  // text restored to the input — NEVER the outbox "success" toast, and NEVER
  // a silently cleared, unrecoverable input
  await page.getByText(/Couldn't reach the Brain Cockpit server/).waitFor();
  assert.equal(await page.getByLabel("Quick capture").inputValue(), fallbackWord,
    "a failed enqueue (broken IndexedDB) must restore the typed text, not silently drop it");
  // still exactly the one item from (a) — a broken IndexedDB write must not
  // be reported as a second successful enqueue
  await page.getByText("1 queued").waitFor();
  await page.unroute("**/api/capture");
  await page.evaluate(() => {
    delete window.indexedDB.open; // restore the real (prototype) implementation
  });
  console.log("✓ A failed IndexedDB write falls back to the existing error/restore-input behavior, not a silent loss");

  // (b) first retry of the queued item from (a): genuinely forward to the
  // real server (so a note really gets written) but still fail the response
  // the browser sees, so the item stays queued for a second retry. The
  // forward's response status is asserted explicitly — otherwise a wrong
  // token, a changed request shape, or a null body would silently fail to
  // land anything, and "only one file" at the end would be vacuously true.
  let firstRetryKey = null;
  let firstForwardStatus = null;
  await page.route("**/api/capture", async (route) => {
    const key = await route.request().headerValue("x-capture-key");
    try {
      const res = await fetch(`${BASE}/api/capture`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${TOKEN}`,
          "Content-Type": "application/json",
          ...(key ? { "X-Capture-Key": key } : {}),
        },
        body: route.request().postData(),
      });
      firstForwardStatus = res.status;
    } catch {
      firstForwardStatus = null; // the forward itself failed — asserted below
    }
    await route.abort();
    // set only after the abort has been dispatched to the browser, so the
    // test's poll below can't observe "the key is known" before the drain
    // this route is servicing has actually settled client-side
    firstRetryKey = key;
  });
  await page.getByRole("button", { name: "Send now" }).click();
  // the onClick handler's own await isn't awaited by .click() itself, and a
  // still-in-flight drain would make a second "Send now" click below
  // early-return (the `draining` guard) without ever draining anything — so
  // wait for the button to be re-enabled, which only happens once drain()
  // (and its own internal `draining` reset) has fully settled.
  await page.waitForFunction(() => {
    const btn = [...document.querySelectorAll("button")].find((b) => b.textContent === "Send now");
    return btn instanceof HTMLButtonElement && !btn.disabled;
  });
  assert.equal(firstForwardStatus, 201,
    `the forwarded first-retry request did not actually land on the real server (status ${firstForwardStatus})`);
  assert.ok(firstRetryKey, "the retried send did not carry an X-Capture-Key header");
  // the drain attempt itself failed client-side too, so the item is still queued
  await page.getByText("1 queued").waitFor();
  await page.unroute("**/api/capture");
  console.log("✓ The retry carried the queued item's id as X-Capture-Key and reached the real server (201)");

  // Pin the dedup claim to an actual state transition, not just an
  // end-of-test snapshot: exactly one file must exist right now, from (b)'s
  // forward alone — before (c) even runs.
  const filesAfterFirstLanding = await waitForFilesContaining(outboxWord);
  assert.equal(filesAfterFirstLanding.length, 1,
    `expected exactly one file after the first landed retry, saw ${JSON.stringify(filesAfterFirstLanding)}`);
  console.log("✓ The first retry's forward genuinely wrote one file (state confirmed before the dedup retry)");

  // (c) second retry of the SAME item, unrouted (real server, real network):
  // the server must recognize firstRetryKey and refuse to write a second file.
  await page.getByRole("button", { name: "Send now" }).click();
  await page.getByText("1 queued").waitFor({ state: "detached" });
  console.log("✓ Send now (second attempt) drains the outbox and the pill disappears");

  // Settle-wait rather than stopping at the first match: confirms the count
  // holds at one instead of merely having been one at some instant.
  await new Promise((r) => setTimeout(r, 500));
  const outboxFiles = filesContaining(outboxWord);
  assert.equal(outboxFiles.length, 1,
    `expected exactly one file despite two real send attempts sharing one idempotency key, saw ${JSON.stringify(outboxFiles)}`);
  console.log("✓ Idempotency key genuinely prevented a duplicate note across two real server-side sends");

  console.log("\nE2E: all checks passed.");
} catch (err) {
  failed = true;
  console.error("\nE2E FAILED:", err);
} finally {
  if (browser) await browser.close();
  if (uvicorn) uvicorn.kill("SIGTERM");
  if (root) fs.rmSync(root, { recursive: true, force: true });
}
process.exit(failed ? 1 : 0);
