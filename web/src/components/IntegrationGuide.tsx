import { useState, useSyncExternalStore } from "react";
import type { IntegrationCard } from "../api/types";
import { lightOf, type Guide, type GuideStep } from "../lib/integrationGuides";
import { TrafficLight } from "./TrafficLight";

// Manual-step ticks live in this browser only: a personal checklist, not data.
const KEY = "brain-guide-ticks";
const listeners = new Set<() => void>();
function subscribe(listener: () => void) {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}
const read = () => {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
};
function toggle(id: string) {
  const set = new Set(read().split(",").filter(Boolean));
  if (set.has(id)) set.delete(id);
  else set.add(id);
  try {
    localStorage.setItem(KEY, [...set].join(","));
  } catch {
    // storage refused (private mode): ticks just don't persist
  }
  listeners.forEach((l) => l());
}

type Tab = "check" | "fix";

export function IntegrationGuide({
  card,
  guide,
  redirectUri,
  onRecheck,
  onClose,
  busy,
}: {
  card: IntegrationCard;
  guide: Guide;
  redirectUri: string;
  onRecheck: () => void;
  onClose: () => void;
  busy: boolean;
}) {
  const light = lightOf(card);
  const [tab, setTab] = useState<Tab>(light === "ok" ? "check" : "fix");
  const ticks = new Set(useSyncExternalStore(subscribe, read, () => "").split(",").filter(Boolean));
  const steps: GuideStep[] = guide[tab];
  const tickId = (i: number) => `${card.id}:${tab}:${i}`;
  const isDone = (s: GuideStep, i: number) => (s.done ? s.done(card) : ticks.has(tickId(i)));
  const current = steps.findIndex((s, i) => !isDone(s, i));
  const hasManual = steps.some((s) => !s.done);

  return (
    <div>
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.18em]">Guide</p>
      <h2 className="font-cal text-emphasis mt-1 text-2xl font-extrabold leading-[0.95] -tracking-[0.02em]">
        {card.name}
      </h2>
      <div className="mt-3">{light && <TrafficLight light={light} detail={card.badge ?? undefined} />}</div>

      <div role="tablist" aria-label="Guide sections" className="mt-4 flex gap-2">
        {(["check", "fix"] as const).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`min-h-11 rounded-xl px-5 text-sm font-bold ${
              tab === t ? "bg-emphasis text-emphasis" : "text-subtle"
            }`}
          >
            {t === "check" ? "Check" : "Fix"}
          </button>
        ))}
      </div>

      <ol className="mt-3 flex flex-col gap-1">
        {steps.map((step, i) => {
          const done = isDone(step, i);
          const now = i === current;
          const text = step.text.replace("{redirectUri}", redirectUri);
          const mark = (
            <span
              aria-hidden="true"
              className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-bold tabular-nums ${
                done
                  ? "bg-light-ok text-light-ok-ink border-transparent"
                  : now
                    ? "bg-inverted text-inverted border-transparent"
                    : "border-emphasis text-subtle"
              }`}
            >
              {done ? "✓" : i + 1}
            </span>
          );
          const words = (
            <span className={`text-sm ${now ? "text-emphasis font-semibold" : done ? "text-subtle" : "text-default"}`}>
              <span className="sr-only">{done ? "Done: " : now ? "Next: " : ""}</span>
              {text}
            </span>
          );
          const external = step.link?.url.startsWith("http");
          return (
            <li key={i} className={`rounded-xl p-2 ${now ? "bg-emphasis" : ""}`}>
              {step.done ? (
                <div className="flex gap-3">
                  {mark}
                  {words}
                </div>
              ) : (
                <button
                  type="button"
                  aria-pressed={done}
                  onClick={() => toggle(tickId(i))}
                  className="flex min-h-11 w-full gap-3 text-left"
                >
                  {mark}
                  {words}
                </button>
              )}
              {step.link && (
                <a
                  href={step.link.url}
                  target={external ? "_blank" : undefined}
                  rel={external ? "noreferrer" : undefined}
                  onClick={external ? undefined : onClose}
                  className="border-emphasis text-emphasis mt-2 ml-9 inline-flex min-h-11 items-center rounded-xl border px-4 text-sm font-bold"
                >
                  {step.link.label}
                  {external ? " ↗" : ""}
                </a>
              )}
            </li>
          );
        })}
      </ol>
      {hasManual && <p className="text-subtle mt-2 text-xs">Tick each step as you do it.</p>}

      <p className="text-default border-subtle mt-4 border-t pt-4 text-sm">
        <span className="font-bold">Tip. </span>
        {guide.tip}
      </p>

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onRecheck}
          disabled={busy}
          className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold disabled:opacity-60"
        >
          {busy ? "Checking…" : "Recheck"}
        </button>
        <button type="button" onClick={onClose} className="text-subtle min-h-11 rounded-xl px-4 text-sm font-bold">
          Close
        </button>
      </div>
    </div>
  );
}
