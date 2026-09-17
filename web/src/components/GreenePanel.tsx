import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Reads, Situation } from "../api/types";

interface Props {
  personId: string;
  expanded: boolean;
  onToggle: () => void;
  reads: Reads;
  outcome: string;
  onOutcomeChange: (value: string) => void;
  presets: string[];
  seducer: string[];
  onInsertLine: (line: string, code: string) => void;
  onInsertPride: (text: string) => void;
  onHeld: (until: string) => void;
  text: string;
  channel: string;
  touchType: string;
}

function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(() => !window.matchMedia("(min-width: 1024px)").matches);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const onChange = () => setMobile(!mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return mobile;
}

// GreenePanel — the Four Reads strip + Greene situation lookup + Anti-Seducer
// chips, sitting above the composer's textarea. Collapsed it is a one-line
// strip; expanded it grows in place at `lg` and becomes a bottom sheet below
// it (patterns doc, T11).
export function GreenePanel({
  personId,
  expanded,
  onToggle,
  reads,
  outcome,
  onOutcomeChange,
  presets,
  seducer,
  onInsertLine,
  onInsertPride,
  onHeld,
  text,
  channel,
  touchType,
}: Props) {
  const [situations, setSituations] = useState<Situation[]>([]);
  const [query, setQuery] = useState("");
  const [holding, setHolding] = useState(false);
  const isMobile = useIsMobile();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    void api
      .greeneSituations()
      .then((r) => live && setSituations(r.situations))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  // Bottom sheet on small screens: Esc closes, focus moves in on open and
  // returns to the toggle on close.
  useEffect(() => {
    if (!expanded) return;
    if (isMobile) {
      sheetRef.current?.focus();
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onToggle();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (isMobile) toggleRef.current?.focus();
    };
  }, [expanded, isMobile, onToggle]);

  const hold = async () => {
    setHolding(true);
    try {
      const { until } = await api.hold(personId, { text, channel, touch_type: touchType });
      onHeld(until);
    } catch {
      onHeld("");
    } finally {
      setHolding(false);
    }
  };

  const filtered = situations.filter((s) => {
    if (!query.trim()) return true;
    const q = query.toLowerCase();
    return (
      s.code.toLowerCase().includes(q) ||
      s.title.toLowerCase().includes(q) ||
      s.happening.toLowerCase().includes(q)
    );
  });
  const ordered = [
    ...filtered.filter((s) => presets.includes(s.code)),
    ...filtered.filter((s) => !presets.includes(s.code)),
  ];

  const panelBody = (
    <div
      ref={sheetRef}
      tabIndex={-1}
      className={
        isMobile
          ? "bg-default border-subtle fixed inset-x-0 bottom-0 z-50 max-h-[80dvh] overflow-y-auto rounded-t-2xl border p-4"
          : "border-subtle mt-2 rounded-xl border p-4"
      }
    >
      {isMobile && (
        <div className="mb-3 flex items-center justify-between">
          <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
            Greene panel
          </p>
          <button
            type="button"
            onClick={onToggle}
            aria-label="Close"
            className="border-subtle text-default flex h-9 w-9 items-center justify-center rounded-full border"
          >
            ✕
          </button>
        </div>
      )}
      <button
        type="button"
        onClick={() => void hold()}
        disabled={holding}
        className="border-emphasis text-emphasis min-h-11 rounded-xl border px-4 text-sm font-bold disabled:opacity-60"
      >
        {holding ? "Holding…" : "Hold until tomorrow 9 am"}
      </button>

      <label className="text-subtle mt-4 block text-[11px] font-bold uppercase tracking-[0.08em]">
        Find a situation
      </label>
      <input
        aria-label="Find a situation"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="e.g. price pushback"
        className="bg-subtle border-subtle text-emphasis mt-1 w-full rounded-xl border p-3 text-sm"
      />
      <ul className="mt-3 max-h-64 space-y-2 overflow-y-auto">
        {ordered.map((s) => (
          <li key={s.code} className="border-subtle rounded-xl border p-3">
            <p className="text-emphasis text-sm font-bold">
              {s.code} · {s.title}
            </p>
            {s.trap && <p className="text-subtle mt-1 text-sm">Trap: {s.trap}</p>}
            {s.move && <p className="text-default mt-1 text-sm">Move: {s.move}</p>}
            {s.line && <p className="text-subtle mt-1 text-sm italic">"{s.line}"</p>}
            {s.line && (
              <button
                type="button"
                onClick={() => onInsertLine(s.line, s.code)}
                className="border-emphasis text-emphasis mt-2 min-h-11 rounded-xl border px-4 text-sm font-bold"
              >
                Use this line
              </button>
            )}
          </li>
        ))}
        {ordered.length === 0 && <li className="text-muted text-sm">No situation matches that.</li>}
      </ul>

      {seducer.length > 0 && (
        <div className="mt-4">
          <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
            Anti-Seducer
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {seducer.map((chip) => (
              <span
                key={chip}
                className="bg-subtle text-default border-subtle rounded-full border px-3 py-1 text-xs font-semibold"
              >
                {chip}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );

  return (
    <div>
      <div className="border-subtle flex flex-wrap items-center gap-2 rounded-xl border p-3">
        <p className="text-subtle min-w-0 flex-1 truncate text-sm">
          Calm?
          {" · Pride: "}
          {reads.pride ? (
            <button
              type="button"
              onClick={() => onInsertPride(reads.pride)}
              className="text-default underline decoration-dotted"
            >
              {reads.pride}
            </button>
          ) : (
            "–"
          )}
          {" · Record: "}
          {reads.record || "–"}
        </p>
        <input
          aria-label="Outcome"
          value={outcome}
          onChange={(e) => onOutcomeChange(e.target.value)}
          placeholder="Outcome"
          className="bg-subtle border-subtle text-emphasis min-h-9 w-32 rounded-lg border px-2 text-sm"
        />
        <button
          ref={toggleRef}
          type="button"
          aria-label="Greene panel"
          aria-expanded={expanded}
          onClick={onToggle}
          className="border-subtle text-default flex h-9 w-9 shrink-0 items-center justify-center rounded-full border"
        >
          {expanded ? "▲" : "▼"}
        </button>
      </div>
      {expanded && panelBody}
    </div>
  );
}
