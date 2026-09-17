import type { LintItem } from "../api/types";

// P20: the list IS the contract this task ships — no underline overlay on the
// textarea (logged to DEFERRED). Lints are informational, never urgent, so
// they stay in text-subtle and never borrow the accent color.
export function LintList({ lints }: { lints: LintItem[] }) {
  if (lints.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1">
      {lints.map((lint, i) => (
        <li key={`${lint.code}-${i}`} className="text-subtle text-sm">
          "{lint.snippet}" → {lint.message}
        </li>
      ))}
    </ul>
  );
}
