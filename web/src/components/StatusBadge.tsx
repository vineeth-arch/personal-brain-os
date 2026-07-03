import type { IntegrationStatus } from "../api/types";

// The §5 status-card badge, made 4-way. Never teal: the design system allows
// exactly one accent per screen (spent on the summary strip), so healthy state
// reads as an affirmative *tonal* chip, problem as the sanctioned spark pink.
const STYLES: Record<IntegrationStatus, string> = {
  ok: "bg-emphasis text-emphasis",
  warn: "bg-subtle border border-emphasis text-emphasis",
  problem: "bg-spark text-spark-text",
  unknown: "bg-subtle text-muted",
};

export function StatusBadge({ status, label }: { status: IntegrationStatus; label: string }) {
  return (
    <span
      className={`inline-block shrink-0 rounded-full px-2.5 py-1 text-[11px] font-bold uppercase tracking-[0.08em] ${STYLES[status]}`}
    >
      {label}
    </span>
  );
}
