import { api } from "../api/client";
import { ErrorState } from "../components/ErrorState";
import { usePolling } from "../hooks/usePolling";

// The build tracker: reality IS the checklist — every tick is a live probe,
// there are no manual checkboxes anywhere. The single accent on this screen
// is the header's "next thing" card (all-done → calm tonal, Today-hero style).
export function Build() {
  const { data, error, loading, refetch } = usePolling(() => api.build(), 60_000);

  if (loading && !data) {
    return (
      <div className="space-y-4" aria-hidden="true">
        <div className="bg-subtle h-32 animate-pulse rounded-xl" />
        <div className="bg-subtle h-96 animate-pulse rounded-xl" />
      </div>
    );
  }
  if (error && !data) {
    return <ErrorState envelope={error.envelope} detail={error.detail} onRetry={refetch} />;
  }

  const { next, items } = data!;
  const done = items.filter((i) => i.done).length;
  const phases: { name: string; items: typeof items }[] = [];
  for (const item of items) {
    const last = phases[phases.length - 1];
    if (last && last.name === item.phase) last.items.push(item);
    else phases.push({ name: item.phase, items: [item] });
  }

  return (
    <div className="space-y-8">
      {next ? (
        // the ONE accent-lit element: the single most important next thing
        <section className="bg-brand-default rounded-xl p-5">
          <p className="text-brand text-[11px] font-bold uppercase tracking-[0.18em] opacity-80">
            Next · {done} of {items.length} done
          </p>
          <h2 className="font-cal text-brand mt-3 text-2xl font-extrabold leading-[0.95] -tracking-[0.02em]">
            {next.label}
          </h2>
          <p className="text-brand mt-2 text-sm font-semibold">{next.next_action}</p>
        </section>
      ) : (
        <section className="bg-subtle border-subtle rounded-xl border p-5">
          <p className="text-brand-default flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.18em]">
            <span aria-hidden="true" className="bg-brand-default h-2 w-2 rounded-full" />
            Build · complete
          </p>
          <h2 className="font-cal text-emphasis mt-3 text-3xl font-extrabold leading-[0.95] -tracking-[0.02em]">
            Everything is built and wired
          </h2>
        </section>
      )}

      {phases.map((phase) => (
        <section key={phase.name}>
          <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
            {phase.name}
          </p>
          <ul className="border-subtle mt-3 ml-2 border-l-2">
            {phase.items.map((item) => (
              <li key={item.id} className="relative py-3 pl-7">
                <span
                  aria-hidden="true"
                  className={`absolute -left-[9px] top-4 flex h-4 w-4 items-center justify-center rounded-full ${
                    item.done ? "bg-inverted" : "bg-default border-emphasis border-2"
                  }`}
                >
                  {item.done && (
                    <svg width="9" height="9" viewBox="0 0 12 12">
                      <path d="M2 6.5 4.8 9 10 3.5" stroke="var(--cal-text-inverted)" strokeWidth="2.4" fill="none" strokeLinecap="round" />
                    </svg>
                  )}
                </span>
                <p className={`text-sm font-bold ${item.done ? "text-emphasis" : "text-default"}`}>
                  {item.label}
                  <span className="sr-only">{item.done ? " — done" : " — not yet"}</span>
                </p>
                <p className="text-subtle mt-0.5 text-xs">{item.detail}</p>
                {item.next_action && (
                  <p className="text-default mt-1 text-sm">→ {item.next_action}</p>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
