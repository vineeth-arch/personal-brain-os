// icon key → inline 20px SVG (no icon lib — deps are locked). Any unmapped key
// falls back to a single-letter lettermark, itself an on-brand micro-detail
// (DESIGNSYSTEM §1 "single-letter" motif).

const S = 20;
const stroke = { stroke: "currentColor", strokeWidth: 1.8, fill: "none" as const };

const PATHS: Record<string, React.ReactNode> = {
  waveform: (
    <g {...stroke} strokeLinecap="round">
      <path d="M3 12h2M19 12h2" />
      <path d="M7 8v8M11 4v16M15 7v10" />
    </g>
  ),
  cloud: (
    <path
      {...stroke}
      strokeLinejoin="round"
      d="M7 18a4 4 0 0 1 0-8 5 5 0 0 1 9.6-1.3A3.5 3.5 0 0 1 17.5 18H7Z"
    />
  ),
  brain: (
    <path
      {...stroke}
      strokeLinejoin="round"
      d="M9 4a2.5 2.5 0 0 0-2.5 2.5A2.5 2.5 0 0 0 5 11a2.5 2.5 0 0 0 1.5 2.3A2.5 2.5 0 0 0 9 20c1 0 1.5-.5 1.5-1.5V5.5C10.5 4.5 10 4 9 4Zm6 0a2.5 2.5 0 0 1 2.5 2.5A2.5 2.5 0 0 1 19 11a2.5 2.5 0 0 1-1.5 2.3A2.5 2.5 0 0 1 15 20c-1 0-1.5-.5-1.5-1.5V5.5C13.5 4.5 14 4 15 4Z"
    />
  ),
  bell: (
    <path
      {...stroke}
      strokeLinejoin="round"
      d="M6 16V10a6 6 0 0 1 12 0v6l1.5 2h-15L6 16Zm4 3a2 2 0 0 0 4 0"
    />
  ),
  "folder-sync": (
    <g {...stroke} strokeLinejoin="round">
      <path d="M3 7a1 1 0 0 1 1-1h4l2 2h9a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7Z" />
      <path d="M9 12a3 3 0 0 1 5-1m1 3a3 3 0 0 1-5 1" strokeLinecap="round" />
    </g>
  ),
  git: (
    <g {...stroke}>
      <path d="M6 3v10a3 3 0 0 0 3 3h3" strokeLinecap="round" />
      <circle cx="6" cy="3.5" r="1.6" />
      <circle cx="15" cy="16.5" r="1.6" />
      <circle cx="6" cy="20.5" r="1.6" />
    </g>
  ),
  pulse: (
    <path {...stroke} strokeLinecap="round" strokeLinejoin="round" d="M3 12h4l2 6 4-14 2 8h6" />
  ),
  obsidian: (
    <path {...stroke} strokeLinejoin="round" d="M12 3 5 9l3 10h8l3-8-5-8Zm0 0-2 8 4 3" />
  ),
  link: (
    <g {...stroke} strokeLinecap="round">
      <path d="M10 14a3.5 3.5 0 0 0 5 0l3-3a3.5 3.5 0 0 0-5-5l-1 1" />
      <path d="M14 10a3.5 3.5 0 0 0-5 0l-3 3a3.5 3.5 0 0 0 5 5l1-1" />
    </g>
  ),
  calendar: (
    <g {...stroke} strokeLinejoin="round">
      <rect x="4" y="5" width="16" height="15" rx="2" />
      <path d="M4 9h16M8 3v4M16 3v4" strokeLinecap="round" />
    </g>
  ),
  mail: (
    <g {...stroke} strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m4 7 8 6 8-6" />
    </g>
  ),
  server: (
    <g {...stroke} strokeLinejoin="round">
      <rect x="4" y="4" width="16" height="7" rx="1.5" />
      <rect x="4" y="13" width="16" height="7" rx="1.5" />
      <path d="M8 7.5h.01M8 16.5h.01" strokeLinecap="round" />
    </g>
  ),
  database: (
    <g {...stroke} strokeLinejoin="round">
      <ellipse cx="12" cy="6" rx="7" ry="2.5" />
      <path d="M5 6v12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5V6M5 12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5" />
    </g>
  ),
};

export function IntegrationIcon({ icon, name }: { icon: string; name: string }) {
  const path = PATHS[icon];
  return (
    <span
      aria-hidden="true"
      className="bg-subtle text-emphasis flex h-10 w-10 shrink-0 items-center justify-center rounded-xl"
    >
      {path ? (
        <svg width={S} height={S} viewBox="0 0 24 24">
          {path}
        </svg>
      ) : (
        <span className="font-cal text-base font-extrabold">{name.charAt(0).toUpperCase()}</span>
      )}
    </span>
  );
}
