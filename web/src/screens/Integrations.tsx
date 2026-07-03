import { useState } from "react";
import { api } from "../api/client";
import type { EngineName, IntegrationCard, IntegrationStatus } from "../api/types";
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
  busy,
}: {
  card: IntegrationCard;
  engine: EngineName;
  onRecheck: () => void;
  onEngine: (engine: EngineName) => void;
  onNtfy: () => void;
  busy: boolean;
}) {
  const [confirmCloud, setConfirmCloud] = useState(false);
  const isWhisper = card.id === "transcription-whispercpp";
  const isOpenAI = card.id === "transcription-openai";
  const isNtfy = card.id === "ntfy";
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
      <div className="w-full">
        <div className="bg-cal-muted border-emphasis rounded-xl border p-3 text-sm">
          <p className="text-emphasis font-bold">Cloud transcription sends your audio to OpenAI.</p>
          <p className="text-default mt-1">
            Your rule: sensitive data stays local. Use as fallback only.
          </p>
        </div>
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            onClick={() => {
              setConfirmCloud(false);
              onEngine("openai");
            }}
            disabled={busy}
            className="bg-inverted text-inverted min-h-11 rounded-xl px-4 text-sm font-bold disabled:opacity-60"
          >
            Use cloud anyway
          </button>
          <button
            type="button"
            onClick={() => setConfirmCloud(false)}
            className="border-subtle text-subtle min-h-11 rounded-xl border px-4 text-sm font-bold"
          >
            Keep local
          </button>
        </div>
      </div>
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

  if (loading && !data) return <SkeletonGrid />;
  if (error && !data) {
    return <ErrorState envelope={error.envelope} detail={error.detail} onRetry={refetch} />;
  }

  const cards = data!.cards;
  const health = cards.filter((c) => c.group === "health");
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
              busy={busy}
            />
          ))}
        </div>
      </section>

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
