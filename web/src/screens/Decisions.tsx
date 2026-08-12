/**
 * Decisions — open bets, and whether your confidence means anything.
 *
 * The calibration chart is hand-drawn SVG (no charting library — locked deps).
 * Its geometry is expressed in `var(--cal-*)` tokens so it flips with the theme
 * without any JS, and the SVG itself is aria-hidden with the numbers repeated
 * in a real table below it: a chart nobody can read with a screen reader is
 * decoration, not information.
 *
 * The screen's single accent is the perfect-calibration diagonal — the line
 * you're trying to sit on. Bars, axes, badges and rows are all tonal.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { PROCESS_GRADES } from "../api/types";
import type {
  Calibration,
  CalibrationBucket,
  Decision,
  ErrorEnvelope,
  ProcessGrade,
} from "../api/types";
import { ErrorState } from "../components/ErrorState";
import { toast } from "../components/Toast";

// Enough closed bets that a shape means something rather than noise.
const MEANINGFUL_SAMPLE = 5;

function toastError(err: unknown, fallback: string) {
  const envelope = (err as { envelope?: ErrorEnvelope }).envelope;
  toast(envelope ? `${envelope.what} ${envelope.todo}` : fallback, "error");
}

function StatBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-subtle rounded-xl p-4">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">{label}</p>
      <p className="font-cal text-emphasis mt-1 text-3xl font-extrabold leading-none -tracking-[0.02em]">
        {value}
      </p>
    </div>
  );
}

/**
 * Predicted probability (x) against how often it actually happened (y).
 * Dots sit on the diagonal when you're well calibrated; above it means you're
 * under-confident, below means over-confident. Dot area grows with the number
 * of decisions in the bucket, so a lone outlier doesn't read like a trend.
 */
function CalibrationChart({ buckets }: { buckets: CalibrationBucket[] }) {
  const filled = buckets.filter((b) => b.count > 0 && b.actual !== null);
  const maxCount = Math.max(1, ...filled.map((b) => b.count));
  // viewBox units; a 100x100 plot with room for the axis labels
  const P = 12;
  const S = 100;
  const x = (percent: number) => P + (percent / 100) * S;
  const y = (fraction: number) => P + S - fraction * S;

  const summary = filled.length
    ? filled
        .map(
          (b) =>
            `${b.label}: ${Math.round((b.actual as number) * 100)}% of ${b.count} came true`,
        )
        .join("; ")
    : "No decisions with a stated probability have been resolved yet.";

  return (
    <div>
      <p className="sr-only">{summary}</p>
      <svg
        viewBox={`0 0 ${S + P * 2} ${S + P * 2}`}
        className="w-full"
        aria-hidden="true"
        role="presentation"
      >
        {/* grid */}
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <line
            key={t}
            x1={x(0)}
            y1={y(t)}
            x2={x(100)}
            y2={y(t)}
            stroke="var(--cal-border-subtle)"
            strokeWidth="0.5"
          />
        ))}
        {/* axes */}
        <line x1={x(0)} y1={y(0)} x2={x(100)} y2={y(0)} stroke="var(--cal-border-emphasis)" strokeWidth="0.8" />
        <line x1={x(0)} y1={y(0)} x2={x(0)} y2={y(1)} stroke="var(--cal-border-emphasis)" strokeWidth="0.8" />
        {/* the one accent: where a perfectly calibrated forecaster sits */}
        <line
          x1={x(0)}
          y1={y(0)}
          x2={x(100)}
          y2={y(1)}
          stroke="var(--cal-brand)"
          strokeWidth="1.2"
          strokeDasharray="3 2"
          strokeLinecap="round"
        />
        {filled.map((b) => (
          <circle
            key={b.bucket}
            cx={x(b.midpoint)}
            cy={y(b.actual as number)}
            r={2 + 3 * Math.sqrt(b.count / maxCount)}
            fill="var(--cal-text-emphasis)"
          />
        ))}
      </svg>
      <div className="text-muted mt-1 flex justify-between text-[10px] font-bold uppercase tracking-[0.08em]">
        <span>0% said</span>
        <span>how often it happened</span>
        <span>100% said</span>
      </div>
    </div>
  );
}

function ChartTable({ buckets }: { buckets: CalibrationBucket[] }) {
  const filled = buckets.filter((b) => b.count > 0);
  if (filled.length === 0) return null;
  return (
    <details className="mt-3">
      <summary className="text-subtle min-h-11 cursor-pointer list-none py-2 text-sm font-semibold">
        The numbers behind the chart
      </summary>
      <div className="overflow-x-auto">
        <table className="mt-1 w-full text-left text-sm">
          <thead>
            <tr className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
              <th className="py-1 pr-3 font-bold">You said</th>
              <th className="py-1 pr-3 font-bold">Happened</th>
              <th className="py-1 font-bold">Decisions</th>
            </tr>
          </thead>
          <tbody>
            {filled.map((b) => (
              <tr key={b.bucket} className="border-subtle border-t">
                <td className="text-default py-1.5 pr-3">{b.label}</td>
                <td className="text-emphasis py-1.5 pr-3 font-semibold">
                  {b.actual === null ? "—" : `${Math.round(b.actual * 100)}%`}
                </td>
                <td className="text-subtle py-1.5">
                  {b.hits}/{b.count}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function ResolveDialog({
  decision,
  onCancel,
  onResolved,
}: {
  decision: Decision;
  onCancel: () => void;
  onResolved: () => void;
}) {
  const [outcome, setOutcome] = useState<boolean | null>(null);
  const [grade, setGrade] = useState<ProcessGrade | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const submit = async () => {
    if (outcome === null || grade === null) return;
    setBusy(true);
    try {
      await api.resolveDecision(decision.id, outcome, grade);
      onResolved();
      toast("Closed the loop on that one.");
    } catch (err) {
      toastError(err, "That didn't reach the server.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-label={`Resolve: ${decision.claim || decision.title}`}
      className="fixed inset-0 z-50 flex items-end justify-center sm:items-center"
    >
      <button type="button" aria-label="Cancel" onClick={onCancel} className="absolute inset-0 bg-black/50" />
      <div className="bg-default border-subtle relative max-h-[92dvh] w-full max-w-sm overflow-y-auto rounded-t-2xl border p-5 sm:rounded-2xl">
        <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">Resolve</p>
        <h2 className="font-cal text-emphasis mt-1 text-xl font-bold leading-tight">
          {decision.claim || decision.title}
        </h2>
        <p className="text-subtle mt-2 text-sm">
          {decision.probability === null
            ? "No probability was stated when you recorded this, so it won't be scored — it still closes."
            : `You said ${decision.probability}% at the time.`}
        </p>

        <p className="text-subtle mt-5 text-[11px] font-bold uppercase tracking-[0.08em]">
          Did it happen?
        </p>
        <div className="mt-2 flex gap-2">
          {[
            { value: true, label: "Yes" },
            { value: false, label: "No" },
          ].map((option) => (
            <button
              key={option.label}
              type="button"
              aria-pressed={outcome === option.value}
              onClick={() => setOutcome(option.value)}
              className={`min-h-11 flex-1 rounded-xl border text-sm font-bold ${
                outcome === option.value
                  ? "bg-emphasis border-emphasis text-emphasis"
                  : "border-subtle text-subtle"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>

        <p className="text-subtle mt-5 text-[11px] font-bold uppercase tracking-[0.08em]">
          How good was the thinking? (1–5)
        </p>
        <p className="text-muted mt-1 text-xs">
          Grade the process, not the result — a good call can still lose.
        </p>
        <div className="mt-2 flex gap-2">
          {PROCESS_GRADES.map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={grade === value}
              onClick={() => setGrade(value)}
              className={`min-h-11 flex-1 rounded-xl border text-sm font-bold ${
                grade === value
                  ? "bg-emphasis border-emphasis text-emphasis"
                  : "border-subtle text-subtle"
              }`}
            >
              {value}
            </button>
          ))}
        </div>

        <div className="mt-6 flex gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="border-subtle text-subtle min-h-12 flex-1 rounded-xl border text-sm font-bold"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={busy || outcome === null || grade === null}
            onClick={() => void submit()}
            className="bg-inverted text-inverted min-h-12 flex-1 rounded-xl text-sm font-bold disabled:opacity-40"
          >
            {busy ? "Saving…" : "Resolve"}
          </button>
        </div>
      </div>
    </div>
  );
}

function DecisionRow({
  decision,
  onResolve,
}: {
  decision: Decision;
  onResolve: (decision: Decision) => void;
}) {
  const isOpen = decision.status === "open";
  return (
    <li className="border-subtle relative border-t py-3 pl-5 first:border-t-0">
      <span
        aria-hidden="true"
        className={`absolute left-0 top-5 h-2.5 w-2.5 rounded-full ${
          isOpen ? "border-emphasis border-2" : "bg-inverted"
        }`}
      />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-emphasis text-sm font-semibold">{decision.claim || decision.title}</p>
          <p className="text-subtle mt-1 text-xs">
            {decision.probability === null ? (
              <span className="text-muted">No probability stated</span>
            ) : (
              <>You said {decision.probability}%</>
            )}
            {isOpen
              ? decision.resolves
                ? ` · resolves ${decision.resolves}`
                : ""
              : ` · ${decision.outcome ? "happened" : "didn't happen"}${
                  decision.brier === null ? "" : ` · Brier ${decision.brier}`
                }`}
          </p>
        </div>
        {isOpen ? (
          <button
            type="button"
            onClick={() => onResolve(decision)}
            className="border-emphasis text-emphasis min-h-11 shrink-0 rounded-xl border px-3 text-xs font-bold"
          >
            Resolve
          </button>
        ) : (
          decision.process_grade !== null && (
            <span className="bg-emphasis text-emphasis shrink-0 rounded px-2 py-1 text-[11px] font-bold">
              Process {decision.process_grade}/5
            </span>
          )
        )}
      </div>
    </li>
  );
}

export function Decisions() {
  const [items, setItems] = useState<Decision[] | null>(null);
  const [calibration, setCalibration] = useState<Calibration | null>(null);
  const [error, setError] = useState<ErrorEnvelope | null>(null);
  const [resolving, setResolving] = useState<Decision | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.decisions();
      setItems(res.items);
      setCalibration(res.calibration);
      setError(null);
    } catch (err) {
      const envelope = (err as { envelope?: ErrorEnvelope }).envelope;
      setError(
        envelope ?? {
          what: "Couldn't load your decisions.",
          cause: "The server didn't answer.",
          todo: "Check the connection on the Settings screen, then try again.",
        },
      );
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (!items && error) return <ErrorState envelope={error} onRetry={() => void load()} />;
  if (!items || !calibration) {
    return <div aria-hidden="true" className="bg-subtle h-40 animate-pulse rounded-xl" />;
  }

  const open = items.filter((d) => d.status === "open");
  const closed = items.filter((d) => d.status !== "open");
  const sparse = calibration.scored_count < MEANINGFUL_SAMPLE;

  return (
    <div className="space-y-6">
      <p className="text-brand-default text-[11px] font-bold uppercase tracking-[0.18em]">
        Decision journal
      </p>

      {items.length === 0 ? (
        <div className="border-subtle rounded-xl border border-dashed p-8">
          <p className="font-cal text-emphasis text-xl font-bold">No decisions yet.</p>
          <p className="text-subtle mt-2 max-w-sm text-sm">
            Say “Decision: …” into a capture — and say a probability out loud if you have
            one (“I'd say 70%”). That number is only ever recorded when you say it.
          </p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-3">
            <StatBlock label="Open" value={String(calibration.open_count)} />
            <StatBlock label="Resolved" value={String(calibration.resolved_count)} />
            <StatBlock
              label="Mean Brier"
              value={calibration.mean_brier === null ? "—" : calibration.mean_brier.toFixed(2)}
            />
          </div>

          <section>
            <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
              Calibration
            </p>
            {sparse ? (
              <p className="text-default mt-2 max-w-md text-sm">
                {calibration.scored_count === 0
                  ? "Nothing scored yet."
                  : `${calibration.scored_count} scored so far.`}{" "}
                A calibration pattern needs at least a few resolutions before it means
                anything — this fills in as you close the loop on real bets.
              </p>
            ) : null}
            <div className="mt-3">
              <CalibrationChart buckets={calibration.buckets} />
            </div>
            <ChartTable buckets={calibration.buckets} />
            {calibration.mean_process_grade !== null && (
              <p className="text-subtle mt-3 text-sm">
                Average process grade {calibration.mean_process_grade.toFixed(1)} / 5 across
                {" "}
                {calibration.resolved_count} resolved.
              </p>
            )}
          </section>

          {open.length > 0 && (
            <section>
              <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
                Open ({open.length})
              </p>
              <ul className="mt-2">
                {open.map((d) => (
                  <DecisionRow key={d.id} decision={d} onResolve={setResolving} />
                ))}
              </ul>
            </section>
          )}

          {closed.length > 0 && (
            <section>
              <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
                Resolved ({closed.length})
              </p>
              <ul className="mt-2">
                {closed.map((d) => (
                  <DecisionRow key={d.id} decision={d} onResolve={setResolving} />
                ))}
              </ul>
            </section>
          )}
        </>
      )}

      {resolving && (
        <ResolveDialog
          decision={resolving}
          onCancel={() => setResolving(null)}
          onResolved={() => {
            setResolving(null);
            void load();
          }}
        />
      )}
    </div>
  );
}
