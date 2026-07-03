// Types mirror API-CONTRACT.md, which mirrors the pipeline's real shapes
// (SQLite events columns, StageError{what,cause,todo}).

// The three-part plain-English error envelope (CLAUDE.md §5 /
// pipeline/errors.py). Rendered verbatim as "What happened / Likely cause /
// What to do".
export interface ErrorEnvelope {
  what: string;
  cause: string;
  todo: string;
}

// The 11 note TYPES — a distinct vocabulary from capture tags
// (pipeline/classify.py NOTE_TYPES, SCHEMA-REFERENCE.md §2).
export const NOTE_TYPES = [
  "musing",
  "learning",
  "todo",
  "journal",
  "project",
  "person",
  "resource",
  "decision",
  "principle",
  "insight",
  "reflection",
] as const;
export type NoteType = (typeof NOTE_TYPES)[number];

// The 8 capture/routing TAGS (pipeline/classify.py TAG_TO_TYPE,
// SCHEMA-REFERENCE.md §4). Not note types — #idea routes to type "musing".
export const CAPTURE_TAGS = [
  "todo",
  "idea",
  "journal",
  "learning",
  "person",
  "resource",
  "decision",
  "project",
] as const;
export type CaptureTag = (typeof CAPTURE_TAGS)[number];

export interface StatusCounts {
  pending: number;
  processed_today: number;
  needs_review: number;
  failed: number;
}

export interface Status {
  vault: string;
  engine: string;
  heartbeat: string | null;
  last_run: string | null;
  counts: StatusCounts;
}

export interface ReviewItem {
  id: string;
  file: string;
  title: string;
  excerpt: string;
  suggested_type: NoteType;
  confidence: number;
  created: string;
}

export interface FailedItem {
  id: number;
  file: string;
  timestamp: string;
  error: ErrorEnvelope;
}

// Exactly the SQLite events columns (pipeline/events.py).
export interface EventRow {
  id: number;
  timestamp: string;
  file: string;
  stage: string;
  status: "ok" | "failed" | "needs_review";
  duration_ms: number | null;
  message: string;
  plain_english_error: string;
}

export interface StreakDay {
  date: string;
  captured: boolean;
}

export interface Streak {
  current: number;
  days: StreakDay[];
}

export interface ResurfacedNote {
  id: string;
  title: string;
  file: string;
  excerpt: string;
  type: NoteType;
  created: string;
}

// Integrations screen (Pass 4). Health checks run server-side, cached 60s.
export type EngineName = "whispercpp" | "openai";
export type IntegrationStatus = "ok" | "warn" | "problem" | "unknown";
export type IntegrationGroup = "health" | "link";

export interface IntegrationCard {
  id: string;
  group: IntegrationGroup;
  name: string;
  description: string;
  icon: string; // key → inline SVG (IntegrationIcon), lettermark fallback
  status: IntegrationStatus;
  badge: string | null; // null for link cards
  detail?: string;
  error?: ErrorEnvelope; // present on warn/problem health cards
  url?: string; // present on link cards
  meta?: Record<string, string | number | boolean>;
}

export interface IntegrationsResponse {
  engine: EngineName;
  generated_at: string;
  fresh: boolean;
  cards: IntegrationCard[];
}
