import type { ErrorEnvelope } from "../api/types";

interface Props {
  envelope: ErrorEnvelope;
  detail?: string;
  onRetry?: () => void;
}

// The three-part plain-English error card (CLAUDE.md §5), rendered verbatim.
// Technical detail never appears in the first view — only behind <details>.
export function ErrorState({ envelope, detail, onRetry }: Props) {
  return (
    <div className="bg-cal-muted border-emphasis rounded-xl border p-5">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
        Something needs you
      </p>
      <h3 className="font-cal text-emphasis mt-2 text-xl font-bold leading-tight -tracking-[0.01em]">
        {envelope.what}
      </h3>
      <dl className="mt-3 space-y-2 text-sm">
        <div>
          <dt className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
            Likely cause
          </dt>
          <dd className="text-default mt-0.5">{envelope.cause}</dd>
        </div>
        <div>
          <dt className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
            What to do
          </dt>
          <dd className="text-default mt-0.5">{envelope.todo}</dd>
        </div>
      </dl>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="border-emphasis text-emphasis mt-4 min-h-11 rounded-xl border px-5 text-sm font-bold"
        >
          Try again
        </button>
      )}
      {detail && (
        <details className="mt-3">
          <summary className="text-muted cursor-pointer text-xs font-semibold">
            Technical detail
          </summary>
          <pre className="text-subtle mt-2 overflow-x-auto whitespace-pre-wrap break-all text-xs">
            {detail}
          </pre>
        </details>
      )}
    </div>
  );
}
