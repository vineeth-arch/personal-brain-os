import type { Streak } from "../api/types";

// The dot-grid pattern from DESIGNSYSTEM.md §5: filled tonal dot = captured,
// hollow ring = not captured. Dots are decorative; the sr-only sentence and
// the big numeral carry the information.
export function StreakDots({ streak }: { streak: Streak }) {
  const capturedCount = streak.days.filter((d) => d.captured).length;
  return (
    <div>
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
        Capture streak
      </p>
      <div className="mt-1 flex items-baseline gap-3">
        <span className="font-cal text-emphasis text-7xl font-extrabold leading-[0.9] -tracking-[0.04em]">
          {streak.current}
        </span>
        <span className="text-subtle text-sm font-semibold">
          {streak.total_captures} capture{streak.total_captures === 1 ? "" : "s"} · {streak.last7} of last 7 days
        </span>
      </div>
      <p className="sr-only">
        {streak.current}-day streak. {streak.total_captures} total captures, {streak.last7} of the last 7 days,{" "}
        {capturedCount} of the last 30 days.
      </p>
      <div aria-hidden="true" className="mt-4 flex flex-wrap gap-2.5">
        {streak.days.map((d) => (
          <span
            key={d.date}
            className={`h-2.5 w-2.5 rounded-full ${
              d.captured ? "bg-inverted" : "border-emphasis border-2"
            }`}
          />
        ))}
      </div>
    </div>
  );
}
