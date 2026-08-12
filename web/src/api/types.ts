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

// Todos (Pass T): Obsidian Tasks-compatible checkbox lines in 06-Todos/.
export type TodoRange = "today" | "tomorrow" | "week" | "overdue";

export interface TodoItem {
  id: string;
  task: string;
  due: string | null; // YYYY-MM-DD
  time: string | null; // HH:MM — presence means a reminder fires at that time
  done: boolean;
  overdue: boolean;
  file: string;
}

// Build tracker (Pass B): live probes — reality is the checklist.
export interface BuildItem {
  id: string;
  label: string;
  phase: string;
  done: boolean;
  detail: string;
  next_action: string | null;
}

export interface BuildResponse {
  generated_at: string;
  next: { label: string; next_action: string } | null;
  items: BuildItem[];
}

// Model router stats (Pass B).
export interface ProviderStat {
  provider: string;
  served: number;
  fell_through: number;
  invalid_json: number;
  avg_confidence: number | null;
}

// Config (GET/PUT /api/config) — safe values only, never key values.
export interface AppConfig {
  engine: EngineName;
  confidence_threshold: number;
  ntfy_url: string;
  ntfy_topic: string;
  providers: string[]; // classification chain, in fallback order (read-only)
  keys: { anthropic: boolean; openai: boolean };
  enrichment: {
    apify_token: boolean;
    apify_actor_set: boolean;
    apify_last_call: string | null;
    youtube_keyless: boolean;
  };
}

// PUT /api/config body — only the fields being changed are sent.
export interface ConfigWrite {
  engine?: EngineName;
  confidence_threshold?: number;
  ntfy_url?: string;
  ntfy_topic?: string;
}

// Backup (Pass 5): git-commit the vault + copy events.db to backups/.
export interface BackupResult {
  ok: boolean;
  at: string;
  vault_committed: boolean;
  events_db_copied: boolean;
}

export interface BackupStatus {
  last_backup: string | null; // newest events-*.db copy, null = never
  last_vault_commit: string | null;
}

// Self-check (Pass 5): the boot-time structural check, re-run live.
export interface SelfCheckItem {
  id: string;
  label: string;
  ok: boolean;
  detail: string;
}

export interface SelfCheckResponse {
  ok: boolean;
  problems: ErrorEnvelope[];
  checks: SelfCheckItem[];
}

// Resource OS (Pass 6). The resource status lifecycle, verbatim from
// SCHEMA-REFERENCE.md §6 (and api/notes.py RESOURCE_LIFECYCLE). The status
// segmented control + the "advance to next step" button read from this order.
export const RESOURCE_STATUSES = [
  "inbox",
  "to-consume",
  "consumed",
  "referenced",
  "archived",
] as const;
export type ResourceStatus = (typeof RESOURCE_STATUSES)[number];

// A card in the gallery. `category` is the note's resource_type (lowercase per
// schema); the UI Title-cases it. `url` is the schema's source_url, exposed for
// the deep-link-out. `insight` is the '## Insight' body section (null = none).
export interface Resource {
  id: string;
  title: string;
  category: string;
  status: ResourceStatus;
  cover: string | null;
  url: string | null;
  created: string; // YYYY-MM-DD
  sample: boolean;
  file: string; // vault-relative, for the obsidian:// deep link
  has_insight: boolean;
  insight: string | null;
}

export interface ResourceSection {
  heading: string;
  text: string;
}

// GET /resources/{id} — the drawer's full detail.
export interface ResourceDetail extends Resource {
  description: string | null;
  author: string | null;
  rating: string | null;
  sections: ResourceSection[];
}

export type SampleScope = "1d" | "1w" | "1m" | "all";

export interface SampleCount {
  count: number;
  scope: SampleScope;
}

export interface SamplePurgeResult {
  removed: number;
  titles: string[];
  scope: SampleScope;
  message: string;
}

// ---- Relationship OS (Pass 7) ----

// The lifecycle from SCHEMA-REFERENCE.md §6, plus `unset` — which is not a
// stored status but the app's word for "this note predates the cadence fields",
// so the UI can ask for them instead of inventing a threshold.
export const PERSON_STATUSES = ["active", "cold", "dormant"] as const;
export type PersonStatus = (typeof PERSON_STATUSES)[number];
export type PersonLifecycle = PersonStatus | "unset";

// The warm-up ladder (SCHEMA-REFERENCE.md §7), in order.
export const WARMTH_STAGES = [
  "identified",
  "researched",
  "engaging",
  "conversing",
  "warm",
  "ready",
] as const;
export type WarmthStage = (typeof WARMTH_STAGES)[number];

export interface Person {
  id: string;
  name: string;
  file: string; // vault-relative, for the obsidian:// deep link
  relationship: string;
  company: string;
  warmth_stage: string;
  cadence_days: number | null;
  last_contact: string | null; // YYYY-MM-DD
  days_since_contact: number | null;
  days_overdue: number | null;
  status: PersonLifecycle;
  unset: boolean;
  dex_deeplink: string | null;
}

// GET /people/{id} — the drawer's full detail. `sections` is the body keyed by
// H2 heading (Context / Needs / Next action); `interactions` is the dated
// append-only log, oldest first.
export interface PersonDetail extends Person {
  sections: Record<string, string>;
  interactions: string[];
  channels: string;
  dex_id: string | null;
  created: string;
  origin: string;
}

export type PeopleFilter = "all" | PersonStatus | "unset";

export interface PersonPatch {
  cadence_days?: number;
  warmth_stage?: WarmthStage;
}

export interface DexSyncResult {
  ok: boolean;
  created: number;
  updated: number;
  unchanged: number;
  skipped: number;
  message: string;
}

// ---- Decision journal + calibration (Pass 8) ----

// A decision's probability is captured only when it was spoken at recording
// time (see pipeline/decisions.py). `null` means none was stated — the decision
// resolves normally, it just can't be scored, so `brier` is null too and it
// stays off the calibration chart. There is deliberately no way to add one
// after the fact: a hindsight number would corrupt the measurement.
export interface Decision {
  id: string;
  title: string;
  claim: string;
  file: string; // vault-relative, for the obsidian:// deep link
  created: string;
  resolves: string | null;
  resolved: string | null;
  status: "open" | "resolved";
  probability: number | null; // 0–100
  outcome: boolean | null;
  brier: number | null;
  process_grade: number | null; // 1–5, graded on PROCESS not outcome
}

// One column of the calibration chart: how often things you called at ~X%
// actually happened. `actual` is null when nothing has landed in the bucket —
// an empty bucket is unknown, not zero.
export interface CalibrationBucket {
  bucket: number;
  label: string;
  count: number;
  hits: number;
  actual: number | null;
  midpoint: number;
}

export interface Calibration {
  buckets: CalibrationBucket[];
  resolved_count: number;
  scored_count: number;
  open_count: number;
  mean_brier: number | null;
  mean_process_grade: number | null;
}

export interface DecisionsResponse {
  items: Decision[];
  calibration: Calibration;
}

export const PROCESS_GRADES = [1, 2, 3, 4, 5] as const;
export type ProcessGrade = (typeof PROCESS_GRADES)[number];
