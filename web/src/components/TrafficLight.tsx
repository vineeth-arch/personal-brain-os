import { LIGHT_GLYPH, LIGHT_WORD, type Light } from "../lib/integrationGuides";

// Colour + glyph + word, never colour alone (WCAG 1.4.1). The disc is a status
// mark, not an accent, so it sits alongside the one teal/pink strip.
const DISC: Record<Light, string> = {
  ok: "bg-light-ok text-light-ok-ink",
  warn: "bg-light-warn text-light-warn-ink",
  bad: "bg-light-bad text-light-bad-ink",
  off: "bg-light-off text-light-off-ink",
};

export function Disc({ light }: { light: Light }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-extrabold leading-none ${DISC[light]}`}
    >
      {LIGHT_GLYPH[light]}
    </span>
  );
}

export function TrafficLight({ light, detail }: { light: Light; detail?: string }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
      <Disc light={light} />
      <span className="text-emphasis text-sm font-bold">{LIGHT_WORD[light]}</span>
      {detail && detail !== LIGHT_WORD[light] && <span className="text-subtle text-sm">· {detail}</span>}
    </span>
  );
}
