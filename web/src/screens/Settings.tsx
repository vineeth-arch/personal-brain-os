import { useState } from "react";
import { api, DEFAULT_API_BASE, getApiBase, getToken, saveConnection } from "../api/client";
import type { ErrorEnvelope } from "../api/types";
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
    name: "Transcription engine",
    where: "config.json → transcription (engine, binary_path, model_path)",
    what: "whisper.cpp binary + model, or the OpenAI engine.",
  },
  {
    name: "Confidence threshold",
    where: "config.json → classification.confidence_threshold (default 0.7)",
    what: "Below this, a classification parks in the triage queue instead of filing silently.",
  },
  {
    name: "ntfy push",
    where: "config.json → ntfy (url, topic)",
    what: "One push per failure. Match the topic in the ntfy app on your phone.",
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
