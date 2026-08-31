import { useEffect, useRef, useState } from "react";
import { api, getApiBase } from "../api/client";
import type {
  CalendarEvent,
  EngineName,
  GmailMessage,
  IntegrationCard,
  IntegrationStatus,
} from "../api/types";
import { CloudEngineConfirm } from "../components/CloudEngineConfirm";
import { ErrorState } from "../components/ErrorState";
import { IntegrationIcon } from "../components/IntegrationIcon";
import { StatusBadge } from "../components/StatusBadge";
import { toast } from "../components/Toast";
import { usePolling } from "../hooks/usePolling";

// Worst status across the health cards drives the one accent-lit summary strip.
const SEVERITY: Record<IntegrationStatus, number> = { problem: 3, warn: 2, ok: 0, unknown: 0 };

function overall(cards: IntegrationCard[]): IntegrationStatus {
  const health = cards.filter((c) => c.group === "health");
  return health.reduce<IntegrationStatus>(
    (worst, c) => (SEVERITY[c.status] > SEVERITY[worst] ? c.status : worst),
    "ok",
  );
}

function fmtClock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

// The ONE teal element on the screen: calm + a single accent dot when all-clear;
// tonal for warnings; spark pink for problems (mirrors Today's HeroCard).
function SummaryStrip({
  cards,
  checkedAt,
  onRecheck,
  rechecking,
}: {
  cards: IntegrationCard[];
  checkedAt: string;
  onRecheck: () => void;
  rechecking: boolean;
}) {
  const status = overall(cards);
  const health = cards.filter((c) => c.group === "health");
  const attention = health.filter((c) => c.status === "warn" || c.status === "problem").length;

  const shell =
    status === "problem"
      ? "bg-spark text-spark-text"
      : status === "warn"
        ? "bg-emphasis text-emphasis"
        : "bg-subtle border-subtle border";
  const eyebrow =
    status === "ok"
      ? "text-brand-default"
      : status === "problem"
        ? "text-spark-text opacity-80"
        : "text-subtle";
  const untested = health.filter((c) => c.status === "unknown").length;
  const line =
    status === "ok"
      ? untested > 0
        ? `All connected · ${untested} untested`
        : "All connected"
      : `${attention} of ${health.length} need${attention === 1 ? "s" : ""} a look`;

  return (
    <section className={`rounded-xl p-5 ${shell}`}>
      <p className={`flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.18em] ${eyebrow}`}>
        {status === "ok" && <span aria-hidden="true" className="bg-brand-default h-2 w-2 rounded-full" />}
        Systems · {status === "ok" ? "OK" : status === "problem" ? "Problem" : "Attention"}
      </p>
      <h2 className="font-cal mt-3 text-3xl font-extrabold leading-[0.95] -tracking-[0.02em]">
        {line}
      </h2>
      <div className="mt-4 flex items-center gap-4">
        <button
          type="button"
          onClick={onRecheck}
          disabled={rechecking}
          className={`min-h-11 rounded-xl border px-5 text-sm font-bold disabled:opacity-60 ${
            status === "ok" ? "border-emphasis text-emphasis" : "border-current"
          }`}
        >
          {rechecking ? "Checking…" : "Recheck all"}
        </button>
        <span className={`text-xs ${status === "ok" ? "text-subtle" : "opacity-80"}`}>
          Checked {fmtClock(checkedAt)}
        </span>
      </div>
    </section>
  );
}

function badgeLabel(card: IntegrationCard): string {
  return card.badge ?? "Untested";
}

function HealthCard({
  card,
  engine,
  onRecheck,
  onEngine,
  onNtfy,
  onVaultSync,
  busy,
}: {
  card: IntegrationCard;
  engine: EngineName;
  onRecheck: () => void;
  onEngine: (engine: EngineName) => void;
  onNtfy: () => void;
  onVaultSync: () => void;
  busy: boolean;
}) {
  const [confirmCloud, setConfirmCloud] = useState(false);
  const isWhisper = card.id === "transcription-whispercpp";
  const isOpenAI = card.id === "transcription-openai";
  const isNtfy = card.id === "ntfy";
  const isVaultSync = card.id === "vault-git-sync";
  const active = (isWhisper && engine === "whispercpp") || (isOpenAI && engine === "openai");

  let action: React.ReactNode;
  if (isNtfy) {
    action = (
      <button
        type="button"
        onClick={onNtfy}
        disabled={busy}
        className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold disabled:opacity-60"
      >
        Send test push
      </button>
    );
  } else if (isVaultSync) {
    action = (
      <button
        type="button"
        onClick={onVaultSync}
        disabled={busy}
        className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold disabled:opacity-60"
      >
        Sync now
      </button>
    );
  } else if (isOpenAI) {
    action = active ? (
      <button
        type="button"
        onClick={() => onEngine("whispercpp")}
        disabled={busy}
        className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold disabled:opacity-60"
      >
        Switch to local
      </button>
    ) : confirmCloud ? (
      <CloudEngineConfirm
        busy={busy}
        onConfirm={() => {
          setConfirmCloud(false);
          onEngine("openai");
        }}
        onCancel={() => setConfirmCloud(false)}
      />
    ) : (
      <button
        type="button"
        onClick={() => setConfirmCloud(true)}
        className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold"
      >
        Use as engine
      </button>
    );
  } else {
    action = (
      <button
        type="button"
        onClick={onRecheck}
        disabled={busy}
        className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold disabled:opacity-60"
      >
        Recheck
      </button>
    );
  }

  return (
    <article className="bg-default border-subtle flex flex-col rounded-xl border p-4">
      <div className="flex items-start gap-3">
        <IntegrationIcon icon={card.icon} name={card.name} />
        <div className="min-w-0 flex-1">
          <h3 className="text-emphasis text-sm font-bold leading-tight">
            {card.name}
            {active && <span className="text-subtle font-semibold"> · active</span>}
          </h3>
          <p className="text-default mt-1 text-sm">{card.description}</p>
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <StatusBadge status={card.status} label={badgeLabel(card)} />
      </div>
      {card.detail && <p className="text-subtle mt-2 text-sm">{card.detail}</p>}
      {card.error && (
        <details className="mt-2">
          <summary className="text-muted min-h-11 cursor-pointer list-none py-2 text-xs font-semibold">
            What to do
          </summary>
          <dl className="mt-1 space-y-2 text-sm">
            <div>
              <dt className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
                What happened
              </dt>
              <dd className="text-default mt-0.5">{card.error.what}</dd>
            </div>
            <div>
              <dt className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
                Likely cause
              </dt>
              <dd className="text-default mt-0.5">{card.error.cause}</dd>
            </div>
            <div>
              <dt className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
                What to do
              </dt>
              <dd className="text-default mt-0.5">{card.error.todo}</dd>
            </div>
          </dl>
        </details>
      )}
      <div className="mt-4">{action}</div>
    </article>
  );
}

function LinkCard({ card }: { card: IntegrationCard }) {
  const external = card.url?.startsWith("http");
  return (
    <article className="bg-default border-subtle flex flex-col rounded-xl border p-4">
      <div className="flex items-start gap-3">
        <IntegrationIcon icon={card.icon} name={card.name} />
        <div className="min-w-0 flex-1">
          <h3 className="text-emphasis text-sm font-bold leading-tight">{card.name}</h3>
          <p className="text-default mt-1 text-sm">{card.description}</p>
        </div>
      </div>
      <div className="mt-4">
        <a
          href={card.url}
          target={external ? "_blank" : undefined}
          rel={external ? "noreferrer" : undefined}
          className="border-emphasis text-emphasis inline-flex min-h-11 items-center rounded-xl border px-5 text-sm font-bold"
        >
          Open ↗
        </a>
      </div>
    </article>
  );
}

// ---- Google (Pass 12) --------------------------------------------------------
// Read + draft only. There is no send button anywhere in this file, and no
// send route on the server — sending is always a human action in Gmail
// (CLAUDE.md §4).

function fmtEventTime(ev: CalendarEvent): string {
  if (ev.all_day) {
    const d = new Date(`${ev.start}T00:00:00`);
    return Number.isNaN(d.getTime())
      ? "All day"
      : `${d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })} · all day`;
  }
  const d = new Date(ev.start);
  return Number.isNaN(d.getTime())
    ? ev.start
    : d.toLocaleString(undefined, {
        weekday: "short",
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
}

function senderName(from: string): string {
  // "Jane Doe <jane@x.com>" → "Jane Doe"; a bare address stays as-is
  const match = from.match(/^\s*"?([^"<]+?)"?\s*</);
  return (match ? match[1] : from).trim() || from;
}

function DraftComposer({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const firstField = useRef<HTMLInputElement>(null);

  useEffect(() => {
    firstField.current?.focus();
  }, []);

  const save = async () => {
    setBusy(true);
    try {
      await api.googleDraft({ to, subject, text });
      // The copy is deliberate: the cockpit cannot send, so the success
      // message has to hand the next step back to the user.
      toast("Draft saved — send it from Gmail.");
      onSaved();
      onClose();
    } catch (err) {
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(envelope ? `${envelope.what} ${envelope.todo}` : "Couldn't save the draft.", "error");
    } finally {
      setBusy(false);
    }
  };

  const field =
    "bg-default border-subtle text-emphasis min-h-11 w-full rounded-xl border px-3 text-sm";

  return (
    <form
      className="border-subtle mt-4 space-y-3 border-t pt-4"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <div>
        <label htmlFor="draft-to" className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
          To
        </label>
        <input
          id="draft-to"
          ref={firstField}
          type="email"
          required
          value={to}
          onChange={(e) => setTo(e.target.value)}
          className={`${field} mt-1`}
          placeholder="name@example.com"
        />
      </div>
      <div>
        <label htmlFor="draft-subject" className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
          Subject
        </label>
        <input
          id="draft-subject"
          type="text"
          required
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          className={`${field} mt-1`}
        />
      </div>
      <div>
        <label htmlFor="draft-body" className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
          Message
        </label>
        <textarea
          id="draft-body"
          required
          rows={5}
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="bg-default border-subtle text-emphasis mt-1 w-full rounded-xl border p-3 text-sm"
        />
      </div>
      <p className="text-subtle text-xs">
        This saves a draft in Gmail. The cockpit never sends mail — you send it
        yourself, from Gmail.
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          type="submit"
          disabled={busy}
          className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold disabled:opacity-60"
        >
          {busy ? "Saving…" : "Save draft to Gmail"}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="text-subtle min-h-11 rounded-xl px-4 text-sm font-bold"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function GoogleCard({
  card,
  onConnect,
  onDisconnect,
  busy,
}: {
  card: IntegrationCard;
  onConnect: () => void;
  onDisconnect: () => void;
  busy: boolean;
}) {
  const connected = card.meta?.connected === true;
  const isGmail = card.id === "gmail";
  const [mail, setMail] = useState<GmailMessage[] | null>(null);
  const [events, setEvents] = useState<CalendarEvent[] | null>(null);
  const [liveError, setLiveError] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);

  useEffect(() => {
    if (!connected) return;
    let alive = true;
    const load = isGmail
      ? api.googleInbox().then((r) => alive && setMail(r.items))
      : api.googleEvents().then((r) => alive && setEvents(r.items));
    load.catch((err) => {
      const envelope = (err as { envelope?: { what: string } }).envelope;
      if (alive) setLiveError(envelope?.what ?? "Couldn't reach Google just now.");
    });
    return () => {
      alive = false;
    };
  }, [connected, isGmail]);

  const count = isGmail ? mail?.length : events?.length;
  const countLabel = isGmail ? "unread" : "in the next 7 days";

  return (
    <article className="bg-default border-subtle flex flex-col rounded-xl border p-4">
      <div className="flex items-start gap-3">
        <IntegrationIcon icon={card.icon} name={card.name} />
        <div className="min-w-0 flex-1">
          <h3 className="text-emphasis text-sm font-bold leading-tight">{card.name}</h3>
          <p className="text-default mt-1 text-sm">{card.description}</p>
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <StatusBadge status={card.status} label={badgeLabel(card)} />
      </div>

      {connected && count !== undefined && count !== null && (
        <p className="font-cal text-emphasis mt-3 text-2xl font-extrabold leading-none -tracking-[0.02em]">
          {count} <span className="text-subtle text-sm font-semibold">{countLabel}</span>
        </p>
      )}

      {connected && liveError && <p className="text-subtle mt-2 text-sm">{liveError}</p>}

      {connected && isGmail && mail && mail.length > 0 && (
        <ul className="mt-3 space-y-2">
          {mail.slice(0, 3).map((m) => (
            <li key={m.id} className="border-subtle border-t pt-2 first:border-t-0 first:pt-0">
              <a href={m.url} target="_blank" rel="noreferrer" className="block">
                <p className="text-emphasis truncate text-sm font-semibold">{m.subject}</p>
                <p className="text-subtle truncate text-xs">{senderName(m.from)}</p>
              </a>
            </li>
          ))}
        </ul>
      )}

      {connected && !isGmail && events && events.length > 0 && (
        <ul className="mt-3 space-y-2">
          {events.slice(0, 3).map((ev) => (
            <li key={ev.id} className="border-subtle border-t pt-2 first:border-t-0 first:pt-0">
              <a href={ev.url} target="_blank" rel="noreferrer" className="block">
                <p className="text-emphasis truncate text-sm font-semibold">{ev.summary}</p>
                <p className="text-subtle truncate text-xs">{fmtEventTime(ev)}</p>
              </a>
            </li>
          ))}
        </ul>
      )}

      {card.detail && !connected && <p className="text-subtle mt-2 text-sm">{card.detail}</p>}

      {composing ? (
        <DraftComposer onClose={() => setComposing(false)} onSaved={() => setMail(null)} />
      ) : (
        <div className="mt-4 flex flex-wrap gap-2">
          {connected ? (
            <>
              {isGmail && (
                <button
                  type="button"
                  onClick={() => setComposing(true)}
                  className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold"
                >
                  New draft
                </button>
              )}
              <a
                href={card.url}
                target="_blank"
                rel="noreferrer"
                className="border-emphasis text-emphasis inline-flex min-h-11 items-center rounded-xl border px-5 text-sm font-bold"
              >
                Open ↗
              </a>
              <button
                type="button"
                onClick={onDisconnect}
                disabled={busy}
                className="text-subtle min-h-11 rounded-xl px-4 text-sm font-bold disabled:opacity-60"
              >
                Disconnect
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={onConnect}
              disabled={busy}
              className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold disabled:opacity-60"
            >
              Connect Google
            </button>
          )}
        </div>
      )}
    </article>
  );
}

function SkeletonGrid() {
  return (
    <div className="space-y-6" aria-hidden="true">
      <div className="bg-subtle h-32 animate-pulse rounded-xl" />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="bg-subtle h-40 animate-pulse rounded-xl" />
        ))}
      </div>
    </div>
  );
}

export function Integrations() {
  const { data, error, loading, refetch } = usePolling(() => api.integrations());
  const [busy, setBusy] = useState(false);

  const recheck = async () => {
    setBusy(true);
    try {
      await api.integrations(true); // bust the 60s server cache
    } catch {
      // the poll below surfaces any real error; ignore the throwaway fresh call
    } finally {
      setBusy(false);
      refetch();
    }
  };

  const switchEngine = async (engine: EngineName) => {
    setBusy(true);
    try {
      await api.setEngine(engine);
      toast(engine === "openai" ? "Switched to OpenAI (cloud fallback)." : "Switched to local whisper.cpp.");
      refetch();
    } catch (err) {
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(envelope ? `${envelope.what} ${envelope.todo}` : "Couldn't switch engine.", "error");
    } finally {
      setBusy(false);
    }
  };

  const testNtfy = async () => {
    setBusy(true);
    try {
      await api.ntfyTest();
      toast("Test push sent — check your phone.");
      refetch();
    } catch (err) {
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(envelope ? `${envelope.what} ${envelope.todo}` : "Couldn't send a test push.", "error");
    } finally {
      setBusy(false);
    }
  };

  const syncVault = async () => {
    setBusy(true);
    try {
      const result = await api.vaultSync();
      toast(result.ok ? "✅ Vault synced." : `Vault sync: ${result.status} — ${result.detail}`,
        result.ok ? "ok" : "error");
      refetch();
    } catch (err) {
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(envelope ? `${envelope.what} ${envelope.todo}` : "Vault sync didn't reach the server.", "error");
    } finally {
      setBusy(false);
    }
  };

  const connectGoogle = async () => {
    setBusy(true);
    try {
      // Google redirects back to the API's own callback page, which is why
      // the redirect URI is built from the API base, not the app's location.
      const { url } = await api.googleConnect(`${getApiBase()}/api/google/callback`);
      window.location.href = url;
    } catch (err) {
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(envelope ? `${envelope.what} ${envelope.todo}` : "Couldn't start the Google sign-in.", "error");
      setBusy(false);
    }
  };

  const disconnectGoogle = async () => {
    setBusy(true);
    try {
      await api.googleDisconnect();
      toast("Google disconnected.");
      refetch();
    } catch {
      toast("Couldn't disconnect Google.", "error");
    } finally {
      setBusy(false);
    }
  };

  if (loading && !data) return <SkeletonGrid />;
  if (error && !data) {
    return <ErrorState envelope={error.envelope} detail={error.detail} onRetry={refetch} />;
  }

  const cards = data!.cards;
  const health = cards.filter((c) => c.group === "health");
  const googleCards = cards.filter((c) => c.group === "google");
  const links = cards.filter((c) => c.group === "link");

  return (
    <div className="space-y-8">
      <SummaryStrip cards={cards} checkedAt={data!.generated_at} onRecheck={recheck} rechecking={busy} />

      <section>
        <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">Health checks</p>
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {health.map((card) => (
            <HealthCard
              key={card.id}
              card={card}
              engine={data!.engine}
              onRecheck={recheck}
              onEngine={switchEngine}
              onNtfy={testNtfy}
              onVaultSync={syncVault}
              busy={busy}
            />
          ))}
        </div>
      </section>

      {googleCards.length > 0 && (
        <section>
          <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">Google</p>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {googleCards.map((card) => (
              <GoogleCard
                key={card.id}
                card={card}
                onConnect={connectGoogle}
                onDisconnect={disconnectGoogle}
                busy={busy}
              />
            ))}
          </div>
        </section>
      )}

      <section>
        <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">Your tools</p>
        {links.length === 0 ? (
          <p className="text-subtle mt-3 text-sm">
            Add a <code className="bg-subtle rounded px-1.5 py-0.5 text-xs">links</code> section to
            config.json to pin your tools here.
          </p>
        ) : (
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {links.map((card) => (
              <LinkCard key={card.id} card={card} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
