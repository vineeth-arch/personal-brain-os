import { useState } from "react";
import { api, DEFAULT_API_BASE, getApiBase, saveConnection } from "../api/client";
import type { ErrorEnvelope } from "../api/types";

interface Props {
  onConnected: () => void;
}

// First-run connect screen. Two-step check: /api/health (no auth) proves the
// server is reachable; /api/status proves the token. Errors render as the
// three-part envelope. The accent budget for this screen: the Connect button.
export function TokenGate({ onConnected }: Props) {
  const [base, setBase] = useState(getApiBase());
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorEnvelope | null>(null);

  const connect = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const cleanBase = base.trim().replace(/\/+$/, "") || DEFAULT_API_BASE;
    try {
      let healthy = false;
      try {
        const res = await api.health(cleanBase);
        healthy = res.ok;
      } catch {
        healthy = false;
      }
      if (!healthy) {
        setError({
          what: "Couldn't reach the Brain Cockpit server.",
          cause: "The API isn't running at that address, or the address is wrong.",
          todo: `Start the API (uvicorn api.main:app), then check the address — the default is ${DEFAULT_API_BASE}.`,
        });
        return;
      }
      saveConnection(cleanBase, token.trim());
      await api.status(); // throws 401 → envelope below
      onConnected();
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

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center p-6">
      <p className="text-brand-default text-[11px] font-bold uppercase tracking-[0.18em]">
        Brain Cockpit
      </p>
      <h1 className="font-cal text-emphasis mt-2 text-4xl font-extrabold leading-[0.95] -tracking-[0.02em]">
        <span className="text-subtle block text-2xl font-bold">Connect to</span>
        your pipeline
      </h1>
      <p className="text-default mt-4 text-sm">
        The address of your Brain Cockpit server and the access token from{" "}
        <code className="bg-subtle rounded px-1.5 py-0.5 text-xs">config.json</code>{" "}
        (the <code className="bg-subtle rounded px-1.5 py-0.5 text-xs">api.auth_token</code>{" "}
        value). Both stay on this device.
      </p>
      <form onSubmit={connect} className="mt-8 space-y-5">
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
            className="bg-subtle border-subtle text-emphasis mt-1.5 block w-full rounded-xl border px-4 py-3 text-base"
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
            className="bg-subtle border-subtle text-emphasis mt-1.5 block w-full rounded-xl border px-4 py-3 text-base"
          />
        </label>
        <button
          type="submit"
          disabled={busy}
          className="bg-brand-default text-brand min-h-12 w-full rounded-xl text-base font-bold disabled:opacity-60"
        >
          {busy ? "Connecting…" : "Connect"}
        </button>
      </form>
      {error && (
        <div className="mt-6" aria-live="polite">
          <div className="bg-cal-muted border-emphasis rounded-xl border p-4 text-sm">
            <p className="text-emphasis font-bold">{error.what}</p>
            <p className="text-default mt-1.5">
              <span className="text-subtle font-bold">Likely cause:</span> {error.cause}
            </p>
            <p className="text-default mt-1.5">
              <span className="text-subtle font-bold">What to do:</span> {error.todo}
            </p>
          </div>
        </div>
      )}
    </main>
  );
}
