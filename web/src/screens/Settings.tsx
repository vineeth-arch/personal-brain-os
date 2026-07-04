import { useState } from "react";
import { api, DEFAULT_API_BASE, getApiBase, getToken, saveConnection } from "../api/client";
import type { ConfigWrite, EngineName, ErrorEnvelope } from "../api/types";
import { CloudEngineConfirm } from "../components/CloudEngineConfirm";
import { toast } from "../components/Toast";
import { usePolling } from "../hooks/usePolling";

// Everything manageable, in one place. What CAN'T live here on purpose:
// provider API keys. The constitution (CLAUDE.md §7) keeps them in server
// environment variables only — so the reference card documents where every
// knob lives instead of storing secrets in the browser.
const REFERENCE_ROWS: { name: string; where: string; what: string }[] = [
  {
    name: "ANTHROPIC_API_KEY",
    where: "Shell environment on the server (e.g. ~/.zshrc)",
    what: "Lets the classifier (Claude Haiku) type untagged captures. Never in config files, never in git.",
  },
  {
    name: "OPENAI_API_KEY",
    where: "Shell environment on the server",
    what: "Only needed if transcription.engine is set to \"openai\" (cloud fallback).",
  },
  {
    name: "api.auth_token",
    where: "config.json → api.auth_token",
    what: "The access token this app connects with. Any random string — just match it here.",
  },
  {
    name: "Vault & folders",
    where: "config.json → vault_path, inbox_path, archive_path, failed_path",
    what: "Where notes live, where captures arrive, where processed audio and failures go.",
  },
  {
    name: "whisper.cpp binary & model",
    where: "config.json → transcription.whispercpp (binary_path, model_path)",
    what: "Where the local transcriber lives. The engine choice itself is editable above.",
  },
  {
    name: "Event log & heartbeat",
    where: "SQLite events.db + the .watcher-heartbeat file (paths from config)",
    what: "Pipeline history and liveness. Disposable — deleting them loses no knowledge.",
  },
];

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="bg-subtle border-subtle rounded-xl border p-5">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">{title}</p>
      <div className="mt-3">{children}</div>
    </section>
  );
}

export function Settings() {
  const status = usePolling(api.status);
  const [base, setBase] = useState(getApiBase());
  const [token, setToken] = useState(getToken() ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorEnvelope | null>(null);

  const saveAndTest = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const cleanBase = base.trim().replace(/\/+$/, "") || DEFAULT_API_BASE;
    try {
      saveConnection(cleanBase, token.trim());
      await api.status();
      toast("Connected. Everything checks out.");
      status.refetch();
    } catch (err) {
      const envelope = (err as { envelope?: ErrorEnvelope }).envelope;
      setError(
        envelope ?? {
          what: "Connecting didn't work.",
          cause: "The server answered in a way the app didn't expect.",
          todo: "Check the server logs, then try again.",
        },
      );
    } finally {
      setBusy(false);
    }
  };

  const vault = status.data?.vault;
  const obsidianVaultLink = vault ? `obsidian://open?vault=${encodeURIComponent(vault)}` : null;
  const todosLink = vault
    ? `obsidian://open?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(
        `06-Todos/${todayISO()}`,
      )}`
    : null;

  return (
    <div className="space-y-6">
      <Card title="Connection">
        <form onSubmit={saveAndTest} className="space-y-4">
          <label className="block">
            <span className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
              Server address
            </span>
            <input
              type="url"
              value={base}
              onChange={(e) => setBase(e.target.value)}
              placeholder={DEFAULT_API_BASE}
              autoComplete="off"
              className="bg-default border-subtle text-emphasis mt-1.5 block w-full rounded-xl border px-4 py-3 text-base"
            />
          </label>
          <label className="block">
            <span className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
              Access token
            </span>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              required
              autoComplete="off"
              className="bg-default border-subtle text-emphasis mt-1.5 block w-full rounded-xl border px-4 py-3 text-base"
            />
          </label>
          <button
            type="submit"
            disabled={busy}
            className="bg-brand-default text-brand min-h-12 w-full rounded-xl text-base font-bold disabled:opacity-60"
          >
            {busy ? "Testing…" : "Save & test connection"}
          </button>
        </form>
        {error && (
          <div className="bg-cal-muted border-emphasis mt-4 rounded-xl border p-4 text-sm" aria-live="polite">
            <p className="text-emphasis font-bold">{error.what}</p>
            <p className="text-default mt-1.5">
              <span className="text-subtle font-bold">Likely cause:</span> {error.cause}
            </p>
            <p className="text-default mt-1.5">
              <span className="text-subtle font-bold">What to do:</span> {error.todo}
            </p>
          </div>
        )}
        {status.data && (
          <p className="text-subtle mt-4 text-sm">
            Connected to vault <span className="text-emphasis font-semibold">{status.data.vault}</span>,
            transcribing with{" "}
            <span className="text-emphasis font-semibold">{status.data.engine}</span>.
          </p>
        )}
      </Card>

      <PipelineSettingsCard onSaved={() => status.refetch()} />

      <Card title="Deep links">
        <ul className="space-y-2">
          <li>
            <a
              href={obsidianVaultLink ?? undefined}
              aria-disabled={!obsidianVaultLink}
              className={`flex min-h-11 items-center text-sm font-bold ${
                obsidianVaultLink ? "text-emphasis" : "text-muted pointer-events-none"
              }`}
            >
              Open vault in Obsidian ↗
            </a>
          </li>
          <li>
            <a
              href={todosLink ?? undefined}
              aria-disabled={!todosLink}
              className={`flex min-h-11 items-center text-sm font-bold ${
                todosLink ? "text-emphasis" : "text-muted pointer-events-none"
              }`}
            >
              Open today's todos in Obsidian ↗
            </a>
          </li>
          <li>
            <a
              href="help.html"
              target="_blank"
              rel="noreferrer"
              className="text-emphasis flex min-h-11 items-center text-sm font-bold"
            >
              Open the operating manual ↗
            </a>
          </li>
          <li>
            <a href="#/build" className="text-emphasis flex min-h-11 items-center text-sm font-bold">
              Build tracker — what's done, what's next →
            </a>
          </li>
        </ul>
        {!vault && (
          <p className="text-subtle mt-2 text-xs">
            Obsidian links light up once the app can reach the server (it needs the vault name).
          </p>
        )}
      </Card>

      <Card title="Where everything lives">
        <p className="text-default text-sm">
          Keys and paths belong to the server, not this app — that's deliberate. Change them at
          the source; this list tells you where.
        </p>
        <ul className="mt-4 space-y-4">
          {REFERENCE_ROWS.map((row) => (
            <li key={row.name} className="border-subtle border-t pt-3 first:border-t-0 first:pt-0">
              <p className="text-emphasis text-sm font-bold">{row.name}</p>
              <p className="text-subtle mt-0.5 text-xs font-semibold">{row.where}</p>
              <p className="text-default mt-1 text-sm">{row.what}</p>
            </li>
          ))}
        </ul>
      </Card>

      <EnrichmentCards />

      <ModelRouterCard />

      <Card title="Help">
        <p className="text-default text-sm">
          The <a href="help.html" target="_blank" rel="noreferrer" className="text-emphasis font-bold underline">operating manual</a>{" "}
          covers the daily loop, first-run wiring, and a symptom → cause → fix table. Every error
          in this app follows the same shape: what happened, the likely cause, what to do next.
        </p>
      </Card>
    </div>
  );
}

// Live pipeline settings (Pass 4): the safe config subset, edited in place.
// Every save goes through the server's validated writer (PUT /api/config),
// which also invalidates the integrations cache — so the Integrations screen
// reflects a change here without a restart. Key VALUES never appear: the
// reference card below documents where those live.
// Accent discipline: this screen's single lit element is the Connection
// card's save button — everything here stays tonal/neutral.
function PipelineSettingsCard({ onSaved }: { onSaved: () => void }) {
  const { data, refetch } = usePolling(api.config, 60_000);
  const [draft, setDraft] = useState<{ threshold?: string; url?: string; topic?: string }>({});
  const [confirmCloud, setConfirmCloud] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorEnvelope | null>(null);

  const applyEngine = async (engine: EngineName) => {
    setBusy(true);
    setError(null);
    try {
      await api.putConfig({ engine });
      toast(
        engine === "openai"
          ? "Transcription engine → OpenAI (cloud fallback)."
          : "Transcription engine → local whisper.cpp.",
      );
      refetch();
      onSaved();
    } catch (err) {
      const envelope = (err as { envelope?: ErrorEnvelope }).envelope;
      setError(
        envelope ?? {
          what: "The engine didn't switch.",
          cause: "The server answered in a way the app didn't expect.",
          todo: "Check the server logs, then try again.",
        },
      );
    } finally {
      setBusy(false);
    }
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!data) return;
    const changes: ConfigWrite = {};
    const changed: string[] = [];
    if (draft.threshold !== undefined) {
      const n = Number(draft.threshold);
      if (Number.isNaN(n) || n < 0 || n > 1) {
        setError({
          what: "That confidence threshold can't be saved.",
          cause: `"${draft.threshold}" isn't a number between 0 and 1.`,
          todo: "Pick a value between 0 and 1 (0.7 is the tested default).",
        });
        return;
      }
      if (n !== data.confidence_threshold) {
        changes.confidence_threshold = n;
        changed.push(`confidence threshold → ${n}`);
      }
    }
    if (draft.url !== undefined && draft.url.trim() !== data.ntfy_url) {
      changes.ntfy_url = draft.url.trim();
      changed.push(`ntfy server → ${draft.url.trim() || "(cleared)"}`);
    }
    if (draft.topic !== undefined && draft.topic.trim() !== data.ntfy_topic) {
      changes.ntfy_topic = draft.topic.trim();
      changed.push(`ntfy topic → ${draft.topic.trim() || "(cleared)"}`);
    }
    if (changed.length === 0) {
      toast("Nothing changed — settings already match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.putConfig(changes);
      toast(`Saved: ${changed.join(", ")}.`);
      setDraft({});
      refetch();
      onSaved();
    } catch (err) {
      const envelope = (err as { envelope?: ErrorEnvelope }).envelope;
      setError(
        envelope ?? {
          what: "The settings didn't save.",
          cause: "The server answered in a way the app didn't expect.",
          todo: "Check the server logs, then try again.",
        },
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Pipeline settings">
      {!data ? (
        <div className="bg-default h-40 animate-pulse rounded-xl" aria-hidden="true" />
      ) : (
        <form onSubmit={save} className="space-y-5">
          <div>
            <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
              Transcription engine
            </p>
            <div className="mt-1.5 flex gap-2" role="group" aria-label="Transcription engine">
              <button
                type="button"
                onClick={() => data.engine !== "whispercpp" && applyEngine("whispercpp")}
                disabled={busy}
                aria-pressed={data.engine === "whispercpp"}
                className={`min-h-11 flex-1 rounded-xl border px-4 text-sm font-bold disabled:opacity-60 ${
                  data.engine === "whispercpp"
                    ? "bg-emphasis text-emphasis border-emphasis"
                    : "border-subtle text-subtle"
                }`}
              >
                Local whisper.cpp
              </button>
              <button
                type="button"
                onClick={() => data.engine !== "openai" && setConfirmCloud(true)}
                disabled={busy}
                aria-pressed={data.engine === "openai"}
                className={`min-h-11 flex-1 rounded-xl border px-4 text-sm font-bold disabled:opacity-60 ${
                  data.engine === "openai"
                    ? "bg-emphasis text-emphasis border-emphasis"
                    : "border-subtle text-subtle"
                }`}
              >
                OpenAI (cloud)
              </button>
            </div>
            {confirmCloud && (
              <div className="mt-3">
                <CloudEngineConfirm
                  busy={busy}
                  onConfirm={() => {
                    setConfirmCloud(false);
                    applyEngine("openai");
                  }}
                  onCancel={() => setConfirmCloud(false)}
                />
              </div>
            )}
          </div>

          <label className="block">
            <span className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
              Confidence threshold
            </span>
            <input
              type="number"
              min={0}
              max={1}
              step={0.05}
              inputMode="decimal"
              value={draft.threshold ?? String(data.confidence_threshold)}
              onChange={(e) => setDraft((d) => ({ ...d, threshold: e.target.value }))}
              className="bg-default border-subtle text-emphasis mt-1.5 block w-full rounded-xl border px-4 py-3 text-base"
            />
            <span className="text-subtle mt-1 block text-xs">
              Below this, a classification parks in the triage queue instead of filing silently.
            </span>
          </label>

          <label className="block">
            <span className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
              ntfy server
            </span>
            <input
              type="url"
              value={draft.url ?? data.ntfy_url}
              onChange={(e) => setDraft((d) => ({ ...d, url: e.target.value }))}
              placeholder="https://ntfy.sh"
              autoComplete="off"
              className="bg-default border-subtle text-emphasis mt-1.5 block w-full rounded-xl border px-4 py-3 text-base"
            />
          </label>
          <label className="block">
            <span className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
              ntfy topic
            </span>
            <input
              type="text"
              value={draft.topic ?? data.ntfy_topic}
              onChange={(e) => setDraft((d) => ({ ...d, topic: e.target.value }))}
              placeholder="brain-cockpit"
              autoComplete="off"
              className="bg-default border-subtle text-emphasis mt-1.5 block w-full rounded-xl border px-4 py-3 text-base"
            />
            <span className="text-subtle mt-1 block text-xs">
              Match the topic your phone subscribes to in the ntfy app, then send a test push from
              Integrations.
            </span>
          </label>

          <button
            type="submit"
            disabled={busy}
            className="border-emphasis text-emphasis min-h-12 w-full rounded-xl border text-base font-bold disabled:opacity-60"
          >
            {busy ? "Saving…" : "Save pipeline settings"}
          </button>

          {error && (
            <div
              className="bg-cal-muted border-emphasis rounded-xl border p-4 text-sm"
              aria-live="polite"
            >
              <p className="text-emphasis font-bold">{error.what}</p>
              <p className="text-default mt-1.5">
                <span className="text-subtle font-bold">Likely cause:</span> {error.cause}
              </p>
              <p className="text-default mt-1.5">
                <span className="text-subtle font-bold">What to do:</span> {error.todo}
              </p>
            </div>
          )}

          <div className="border-subtle border-t pt-4">
            <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
              Classification chain
            </p>
            <ol className="mt-2 space-y-1">
              {data.providers.map((p, i) => (
                <li key={p} className="text-default text-sm">
                  <span className="text-subtle font-semibold">{i + 1}.</span>{" "}
                  <span className="text-emphasis font-semibold">{p}</span>
                </li>
              ))}
            </ol>
            <p className="text-subtle mt-2 text-xs">
              Tried in order until one answers well; reordering lives in config.json →
              classification.providers.
            </p>
          </div>
        </form>
      )}
    </Card>
  );
}

// Model router stats (Pass B): which provider actually served, per outcome.
function ModelRouterCard() {
  const { data } = usePolling(api.providers, 60_000);
  const rows = data?.providers ?? [];
  return (
    <section className="bg-subtle border-subtle rounded-xl border p-5">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">Model router</p>
      {rows.length === 0 ? (
        <p className="text-default mt-3 text-sm">
          No classifications routed yet. The chain runs Gemini → Groq → OpenRouter → Claude Haiku
          (the floor); providers without a key are skipped silently.
        </p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-subtle text-left text-[11px] font-bold uppercase tracking-[0.08em]">
                <th className="py-1 pr-3">Provider</th>
                <th className="py-1 pr-3">Served</th>
                <th className="py-1 pr-3">Fell through</th>
                <th className="py-1 pr-3">Bad JSON</th>
                <th className="py-1">Avg conf</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.provider} className="border-subtle border-t">
                  <td className="text-emphasis py-2 pr-3 font-semibold">{r.provider}</td>
                  <td className="text-default py-2 pr-3">{r.served}</td>
                  <td className="text-default py-2 pr-3">{r.fell_through}</td>
                  <td className="text-default py-2 pr-3">{r.invalid_json}</td>
                  <td className="text-default py-2">{r.avg_confidence ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// Enrichment status (Pass L): YouTube is keyless and always on; Instagram runs
// through Apify (ToS-grey, expected to break — failures never lose the note).
function EnrichmentCards() {
  const { data } = usePolling(api.config, 60_000);
  const e = data?.enrichment;
  return (
    <section className="bg-subtle border-subtle rounded-xl border p-5">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">Link enrichment</p>
      <div className="mt-3 space-y-4">
        <div>
          <div className="flex items-center justify-between gap-3">
            <p className="text-emphasis text-sm font-bold">YouTube</p>
            <span className="bg-emphasis text-emphasis rounded-full px-2.5 py-1 text-[11px] font-bold uppercase tracking-[0.08em]">
              Always on
            </span>
          </div>
          <p className="text-default mt-1 text-sm">
            Title, channel, and thumbnail via public oEmbed — keyless, no setup.
          </p>
        </div>
        <div className="border-subtle border-t pt-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-emphasis text-sm font-bold">Instagram · Apify</p>
            <span
              className={`rounded-full px-2.5 py-1 text-[11px] font-bold uppercase tracking-[0.08em] ${
                e?.apify_token && e?.apify_actor_set
                  ? "bg-emphasis text-emphasis"
                  : "bg-subtle text-muted"
              }`}
            >
              {e?.apify_token && e?.apify_actor_set ? "Configured" : "Not configured"}
            </span>
          </div>
          <p className="text-default mt-1 text-sm">
            Caption + image via an Apify actor.{" "}
            {e && !e.apify_token && "Set APIFY_TOKEN in the server's environment"}
            {e && e.apify_token && !e.apify_actor_set && "Set apify.actor_id in config.json"}
            {e?.apify_last_call && (
              <span className="text-subtle"> Last call {new Date(e.apify_last_call).toLocaleString()}.</span>
            )}
          </p>
          <p className="text-subtle mt-2 text-xs">
            Instagram scraping is against Instagram's terms and breaks periodically — that's
            expected. When it fails, the note is still saved and retries on its own; nothing is
            lost.
          </p>
        </div>
      </div>
    </section>
  );
}
