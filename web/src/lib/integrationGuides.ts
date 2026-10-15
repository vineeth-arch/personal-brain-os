import type { IntegrationCard } from "../api/types";

// Pass G: traffic lights + per-card Check/Fix guides for the Integrations screen.
// Static registry (same idea as di-portal's lib/guides.ts). RULE: when a card is
// added or its setup changes in api/integrations.py, change its guide here —
// web/e2e/run-e2e.mjs fails when a non-link card has no guide.

export type Light = "ok" | "warn" | "bad" | "off";

export const LIGHT_WORD: Record<Light, string> = {
  ok: "Connected",
  warn: "Needs a look",
  bad: "Not working",
  off: "Not connected",
};
export const LIGHT_GLYPH: Record<Light, string> = { ok: "✓", warn: "!", bad: "✕", off: "–" };

const GOOGLE_LINK_IDS = new Set(["gmail", "gcal"]);

// Derived only from the existing payload; no backend change.
export function lightOf(card: IntegrationCard): Light | null {
  if (card.group === "link") return GOOGLE_LINK_IDS.has(card.id) ? "off" : null;
  if (card.status === "ok") return "ok";
  if (card.status === "warn") return "warn";
  if (card.status === "problem") return "bad";
  return card.error ? "off" : "warn"; // unknown: unset → grey, set but untested → amber
}

export interface GuideStep {
  text: string; // "{redirectUri}" is replaced at render
  done?: (c: IntegrationCard) => boolean; // absent = manual, ticked by hand
  link?: { label: string; url: string }; // https://… or #/settings
}

export interface Guide {
  optional?: boolean;
  check: GuideStep[];
  fix: GuideStep[];
  tip: string;
}

const okNow = (c: IntegrationCard) => c.status === "ok";
const SETTINGS = { label: "Open Settings", url: "#/settings" };
const RESTART = "Restart the API so it picks the change up.";
const ENV_HOME = "in the server's .env file (or the Railway service variable)";

const google: Guide = {
  check: [
    { text: "Read the light on the card. Green means Google is linked.", done: (c) => c.meta?.connected === true },
    { text: "Open the card's Open link and confirm you see your own mail or calendar." },
    { text: "If Gmail shows a count of unread mail, live data is flowing.", done: (c) => c.meta?.connected === true },
  ],
  fix: [
    {
      text: "In Google Cloud, create a project and enable three APIs: Gmail API, Google Calendar API and People API.",
      link: { label: "Open API library", url: "https://console.cloud.google.com/apis/library" },
    },
    {
      text: "Set up the OAuth consent screen: type External, add your own Google account as a test user, and add the Gmail read, Gmail compose, Calendar read-only and Contacts scopes.",
      link: { label: "Open consent screen", url: "https://console.cloud.google.com/apis/credentials/consent" },
    },
    {
      text: "Create an OAuth client ID of type Web application. Add this as the authorised redirect URI: {redirectUri}",
      link: { label: "Open credentials", url: "https://console.cloud.google.com/apis/credentials" },
    },
    {
      text: `Put the client ID and secret in GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET ${ENV_HOME}. ${RESTART}`,
      done: (c) => c.group === "google",
    },
    { text: "Press Connect Google on the card and approve the access.", done: (c) => c.meta?.connected === true },
  ],
  tip: "Test-mode apps work fine for personal use and need no Google review. The cockpit can read and draft, never send.",
};

export const GUIDES: Record<string, Guide> = {
  "transcription-whispercpp": {
    check: [
      { text: "Read the light. Green means the binary and model files are found.", done: okNow },
      { text: "Record a short voice memo into the inbox and watch it turn into a note." },
    ],
    fix: [
      {
        text: "Download a whisper.cpp model file (a .bin) and note where you saved it.",
        link: { label: "Open model downloads", url: "https://huggingface.co/ggerganov/whisper.cpp" },
      },
      { text: "Set transcription.whispercpp.binary_path and model_path in config.json to the real files." },
      { text: "Make sure the binary is executable: chmod +x <path>." },
      { text: "Press Recheck on the card.", done: okNow },
    ],
    tip: "This is the private default: audio never leaves this machine.",
  },
  "transcription-openai": {
    optional: true,
    check: [
      { text: "Read the light. Green means the key works.", done: okNow },
      { text: "Active shows next to the name only when it is the engine in use." },
    ],
    fix: [
      {
        text: "Create an API key.",
        link: { label: "Open OpenAI keys", url: "https://platform.openai.com/api-keys" },
      },
      {
        text: `Set OPENAI_API_KEY ${ENV_HOME}. ${RESTART}`,
        done: (c) => c.meta?.key_present === true,
      },
      { text: "Press Recheck. It runs a free test call.", done: okNow },
      { text: "Only if you want the cloud engine: press Use as engine and confirm the caution." },
    ],
    tip: "Cloud transcription sends your audio to OpenAI. Leave it off if you want everything local.",
  },
  claude: {
    check: [
      { text: "Read the light. Green means the key answered a test call.", done: okNow },
      { text: "Untagged captures get a note type from Claude. A red or amber light means they wait for you in Triage." },
    ],
    fix: [
      {
        text: "Create an API key.",
        link: { label: "Open Anthropic keys", url: "https://platform.claude.com/settings/keys" },
      },
      {
        text: `Set ANTHROPIC_API_KEY ${ENV_HOME}. ${RESTART}`,
        done: (c) => c.meta?.key_present === true,
      },
      { text: "Press Recheck.", done: okNow },
    ],
    tip: "Key values never show in the cockpit. Only whether one is present.",
  },
  transliteration: {
    optional: true,
    check: [
      { text: "Read the light. Green means an engine and model are set and usable.", done: okNow },
      { text: "Send yourself a Hindi voice memo. The note should come out in Roman script." },
    ],
    fix: [
      {
        text: "Pick an engine. Local: install Ollama and pull a model.",
        link: { label: "Get Ollama", url: "https://ollama.com/download" },
      },
      {
        text: "Or cloud: create an OpenRouter key and set OPENROUTER_API_KEY where the server runs.",
        link: { label: "Open OpenRouter keys", url: "https://openrouter.ai/keys" },
      },
      {
        text: "In Settings, choose Ollama or OpenRouter and name a model.",
        link: SETTINGS,
        done: (c) => c.meta?.usable === true,
      },
      { text: "Press Recheck.", done: okNow },
    ],
    tip: "Without it nothing is lost. Hindi captures just stay in Devanagari.",
  },
  pdl: {
    optional: true,
    check: [
      { text: "Read the light. Green means a key is present.", done: okNow },
      { text: "Open a person's card and press the enrich action to try it." },
    ],
    fix: [
      {
        text: "Create a free account and copy your API key.",
        link: { label: "Open People Data Labs", url: "https://dashboard.peopledatalabs.com/" },
      },
      {
        text: `Set PDL_API_KEY ${ENV_HOME}. ${RESTART}`,
        done: (c) => c.meta?.key_present === true,
      },
      { text: "Press Recheck.", done: okNow },
    ],
    tip: "The free tier gives about 100 lookups a month. Nothing else depends on this.",
  },
  apify: {
    optional: true,
    check: [
      { text: "Read the light. Green means a token and actor are set.", done: okNow },
      { text: "Share an Instagram post to the cockpit. The note should get a caption." },
    ],
    fix: [
      {
        text: "Create an API token.",
        link: { label: "Open Apify tokens", url: "https://console.apify.com/settings/integrations" },
      },
      {
        text: `Set APIFY_TOKEN ${ENV_HOME}. Environment only, never config.json. ${RESTART}`,
        done: (c) => c.meta?.token_present === true,
      },
      { text: "Check apify.actor_id in config.json is set.", done: (c) => c.meta?.actor_set === true },
      { text: "Press Recheck.", done: okNow },
    ],
    tip: "Apify bills per result, so the check never makes a live call.",
  },
  ntfy: {
    check: [
      { text: "Press Send test push on the card.", done: okNow },
      { text: "Your phone should buzz within a few seconds." },
      { text: "Green means the server accepted it. Amber on a fresh setup just means not tested yet." },
    ],
    fix: [
      {
        text: "Install the ntfy app on your phone.",
        link: { label: "Open ntfy docs", url: "https://docs.ntfy.sh/" },
      },
      { text: "Pick a long, hard-to-guess topic name and subscribe to it in the app." },
      { text: "Set ntfy.url (for example https://ntfy.sh) and ntfy.topic in config.json or Settings.", link: SETTINGS },
      { text: "Press Send test push.", done: okNow },
    ],
    tip: "Anyone who knows the topic name can read it. Make it unguessable.",
  },
  "vault-sync": {
    check: [
      { text: "Read the light. Green means the inbox and vault folders are reachable.", done: okNow },
      { text: "Last file activity should be recent if you captured something lately." },
    ],
    fix: [
      { text: "Check the sync app (Syncthing or similar) is running and the share is mounted." },
      { text: "Check the inbox and vault paths in config.json point at real folders." },
      { text: "Press Recheck.", done: okNow },
    ],
    tip: "The vault is the only source of truth. A missing folder is the first thing to rule out.",
  },
  git: {
    check: [
      { text: "Read the light. Green means the vault has a recent commit and nothing waiting.", done: okNow },
      { text: "Amber means uncommitted changes or no repo." },
    ],
    fix: [
      { text: "No repo: run git init in the vault folder and make one clean commit." },
      { text: "Uncommitted changes: approve or run a pass (API writes commit on their own), or commit by hand." },
      { text: "Press Recheck.", done: okNow },
    ],
    tip: "Every AI change is committed first, so you can always revert it.",
  },
  "vault-git-sync": {
    check: [
      { text: "Read the light. Green means the last sync worked.", done: okNow },
      { text: "Press Sync now to run one on demand." },
    ],
    fix: [
      { text: "Create a private GitHub repo for the vault." },
      { text: `Set VAULT_GIT_REMOTE and VAULT_GIT_TOKEN ${ENV_HOME}. ${RESTART}` },
      { text: "Press Sync now.", done: okNow },
      { text: "If it names a conflict, resolve it by hand with a normal pull, then Sync now again." },
    ],
    tip: "Never paste the token into chat or a file in the vault.",
  },
  watcher: {
    check: [
      { text: "Read the light. Green means the watcher checked in recently.", done: okNow },
      { text: "The age beside the light is minutes since its last check-in." },
    ],
    fix: [
      { text: "Start it where the pipeline runs: python3 -m pipeline --loop" },
      { text: "Press Recheck.", done: okNow },
    ],
    tip: "Without the watcher, captures sit in the inbox and nothing is filed.",
  },
  gmail: google,
  gcal: google,
};

export const guideFor = (id: string): Guide | undefined => GUIDES[id];
