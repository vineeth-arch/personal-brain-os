import { useState } from "react";
import { api } from "../api/client";
import type { EventRow } from "../api/types";
import { ErrorState } from "../components/ErrorState";
import { toast } from "../components/Toast";
import { usePolling } from "../hooks/usePolling";

const FILTERS = [
  { key: "", label: "All" },
  { key: "ok", label: "OK" },
  { key: "needs_review", label: "Review" },
  { key: "failed", label: "Failed" },
] as const;

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function StatBlock({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bg-subtle rounded-xl p-4">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">{label}</p>
      <p className="font-cal text-emphasis mt-1 text-3xl font-extrabold -tracking-[0.02em]">
        {value}
      </p>
    </div>
  );
}

function EventLine({ event }: { event: EventRow }) {
  const statusLabel =
    event.status === "needs_review" ? "review" : event.status === "failed" ? "failed" : "ok";
  return (
    <li className="border-subtle border-b py-3 last:border-b-0">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-emphasis truncate text-sm font-semibold">{event.file}</p>
        <p className="text-muted shrink-0 text-xs">{fmtTime(event.timestamp)}</p>
      </div>
      <p className="text-subtle mt-0.5 text-xs font-semibold uppercase tracking-[0.08em]">
        {event.stage} · {statusLabel}
      </p>
      {(event.message || event.plain_english_error || event.duration_ms != null) && (
        <details className="mt-1">
          <summary className="text-muted min-h-11 cursor-pointer list-none py-2 text-xs font-semibold">
            Technical detail
          </summary>
          <pre className="text-subtle overflow-x-auto whitespace-pre-wrap break-all text-xs">
            {[
              event.message && `message: ${event.message}`,
              event.duration_ms != null && `duration: ${event.duration_ms}ms`,
              event.plain_english_error,
            ]
              .filter(Boolean)
              .join("\n")}
          </pre>
        </details>
      )}
    </li>
  );
}

export function Pipeline() {
  const status = usePolling(api.status, 20_000);
  const failed = usePolling(api.failed, 20_000);
  const [filter, setFilter] = useState<string>("");
  const events = usePolling(() => api.events(filter || undefined), 20_000);
  const [running, setRunning] = useState(false);
  const [retrying, setRetrying] = useState<number | null>(null);

  const runNow = async () => {
    setRunning(true);
    try {
      await api.run();
      toast("Pipeline run started.");
      status.refetch();
    } catch (err) {
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(envelope ? `${envelope.what} ${envelope.todo}` : "Couldn't start a run.", "error");
    } finally {
      setRunning(false);
    }
  };

  const retry = async (id: number) => {
    setRetrying(id);
    try {
      await api.retry(id);
      toast("Sent back to the inbox — the watcher will pick it up.");
      failed.refetch();
      status.refetch();
    } catch (err) {
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(envelope ? `${envelope.what} ${envelope.todo}` : "Retry didn't reach the server.", "error");
    } finally {
      setRetrying(null);
    }
  };

  if (status.loading && !status.data) {
    return (
      <div className="space-y-4" aria-hidden="true">
        <div className="bg-subtle h-24 animate-pulse rounded-xl" />
        <div className="bg-subtle h-40 animate-pulse rounded-xl" />
        <div className="bg-subtle h-64 animate-pulse rounded-xl" />
      </div>
    );
  }
  if (status.error && !status.data) {
    return (
      <ErrorState
        envelope={status.error.envelope}
        detail={status.error.detail}
        onRetry={status.refetch}
      />
    );
  }

  const s = status.data!;
  return (
    <div className="space-y-8">
      <section>
        <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
          Engine · {s.engine} · last run {s.last_run ? fmtTime(s.last_run) : "never"}
        </p>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <StatBlock label="Pending" value={s.counts.pending} />
          <StatBlock label="Processed today" value={s.counts.processed_today} />
          <StatBlock label="Needs review" value={s.counts.needs_review} />
          <StatBlock label="Failed" value={s.counts.failed} />
        </div>
        <button
          type="button"
          onClick={runNow}
          disabled={running}
          className="bg-brand-default text-brand mt-4 min-h-12 w-full rounded-xl text-base font-bold disabled:opacity-60"
        >
          {running ? "Starting…" : "Run now"}
        </button>
      </section>

      {failed.data && failed.data.items.length > 0 && (
        <section>
          <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
            Failed items
          </p>
          <div className="mt-3 space-y-3">
            {failed.data.items.map((item) => (
              <div key={item.id} className="bg-cal-muted border-emphasis rounded-xl border p-5">
                <p className="text-emphasis text-sm font-bold">{item.file}</p>
                <p className="text-muted mt-0.5 text-xs">{fmtTime(item.timestamp)}</p>
                <dl className="mt-3 space-y-2 text-sm">
                  <div>
                    <dt className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
                      What happened
                    </dt>
                    <dd className="text-default mt-0.5">{item.error.what}</dd>
                  </div>
                  <div>
                    <dt className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
                      Likely cause
                    </dt>
                    <dd className="text-default mt-0.5">{item.error.cause}</dd>
                  </div>
                  <div>
                    <dt className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
                      What to do
                    </dt>
                    <dd className="text-default mt-0.5">{item.error.todo}</dd>
                  </div>
                </dl>
                <button
                  type="button"
                  onClick={() => retry(item.id)}
                  disabled={retrying === item.id}
                  className="border-emphasis text-emphasis mt-4 min-h-11 rounded-xl border px-5 text-sm font-bold disabled:opacity-60"
                >
                  {retrying === item.id ? "Retrying…" : "Retry"}
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">Event log</p>
        <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Filter events by status">
          {FILTERS.map((f) => {
            const active = filter === f.key;
            return (
              <button
                key={f.key}
                type="button"
                aria-pressed={active}
                onClick={() => setFilter(f.key)}
                className={`min-h-11 rounded-full border px-4 text-sm font-semibold ${
                  active
                    ? "bg-emphasis border-emphasis text-emphasis"
                    : "bg-subtle border-subtle text-default"
                }`}
              >
                {f.label}
              </button>
            );
          })}
        </div>
        {events.error && !events.data ? (
          <div className="mt-3">
            <ErrorState envelope={events.error.envelope} onRetry={events.refetch} />
          </div>
        ) : events.data && events.data.events.length === 0 ? (
          <p className="text-subtle mt-4 text-sm">
            No events{filter ? " with that status" : " yet"}. The log fills as the pipeline runs.
          </p>
        ) : (
          <ul className="mt-2">
            {events.data?.events.map((e) => <EventLine key={e.id} event={e} />)}
          </ul>
        )}
      </section>
    </div>
  );
}
