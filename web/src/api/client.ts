import type {
  AppConfig,
  BackupResult,
  BackupStatus,
  BuildResponse,
  CalendarEvent,
  CaptureTag,
  ConfigWrite,
  DraftCreated,
  DraftWrite,
  EngineName,
  GmailMessage,
  ProviderStat,
  SelfCheckResponse,
  TodoItem,
  TodoRange,
  ErrorEnvelope,
  EventRow,
  FailedItem,
  IntegrationsResponse,
  ChannelKind,
  ContactResult,
  EnrichResult,
  NoteType,
  Person,
  PersonDetail,
  PersonDraft,
  PushAvailability,
  PushPreview,
  PushQueueItem,
  PushResult,
  PushTarget,
  ResurfacedNote,
  ReviewItem,
  Resource,
  ResourceDetail,
  ResourceStatus,
  SampleCount,
  SampleScope,
  SamplePurgeResult,
  Status,
  Streak,
  VoiceStatus,
  WarmthStage,
} from "./types";

const BASE_KEY = "cockpit.apiBase";
const TOKEN_KEY = "cockpit.token";
export const DEFAULT_API_BASE = "http://127.0.0.1:8000";

// Fired when the server rejects the token so App can swap to the connect screen.
export const UNAUTHORIZED_EVENT = "cockpit:unauthorized";

// Connectivity store (Pass 5): flips offline on any network-level failure and
// back on any response from the server — even an error status proves the
// server is reachable. OfflineBanner subscribes (same module-level idiom as
// the toast store). Distinct from error states: screens keep their stale data
// while the banner explains why nothing is updating.
let offline = false;
const offlineListeners = new Set<() => void>();

function setOffline(value: boolean) {
  if (offline === value) return;
  offline = value;
  offlineListeners.forEach((l) => l());
}

export function subscribeOffline(cb: () => void): () => void {
  offlineListeners.add(cb);
  return () => offlineListeners.delete(cb);
}

export function isOffline(): boolean {
  return offline;
}

export function getApiBase(): string {
  return localStorage.getItem(BASE_KEY) || DEFAULT_API_BASE;
}
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function saveConnection(base: string, token: string): void {
  localStorage.setItem(BASE_KEY, base.replace(/\/+$/, ""));
  localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

// Every failure path ends in one of these: an envelope the UI renders
// verbatim, plus technical detail kept for the "copy details" disclosure.
export class ApiError extends Error {
  envelope: ErrorEnvelope;
  status: number; // 0 = network-level failure
  detail: string;

  constructor(envelope: ErrorEnvelope, status: number, detail: string) {
    super(envelope.what);
    this.envelope = envelope;
    this.status = status;
    this.detail = detail;
  }
}

const NETWORK_ENVELOPE: ErrorEnvelope = {
  what: "Couldn't reach the Brain Cockpit server.",
  cause: "The API isn't running yet, or the server address is wrong.",
  todo: "Start the API, or check the server address on the connect screen.",
};

const UNAUTHORIZED_ENVELOPE: ErrorEnvelope = {
  what: "The server rejected the access token.",
  cause: "The token doesn't match api.auth_token in the server's config.json.",
  todo: "Re-enter the token from your config on the connect screen.",
};

function genericEnvelope(status: number): ErrorEnvelope {
  return {
    what: "The server couldn't complete that request.",
    cause: `It answered with an unexpected error (error ${status}).`,
    todo: "Try again; if it keeps happening, check the pipeline logs.",
  };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  let res: Response;
  try {
    res = await fetch(getApiBase() + path, {
      ...init,
      cache: "no-store", // live data always — the SW additionally never touches API requests
      headers: {
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init.headers,
      },
    });
  } catch (e) {
    setOffline(true);
    throw new ApiError(NETWORK_ENVELOPE, 0, String(e));
  }
  setOffline(false); // any answer — even an error — means the server is reachable

  if (res.ok) {
    return (await res.json()) as T;
  }

  const body = await res.text();
  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    throw new ApiError(UNAUTHORIZED_ENVELOPE, 401, body);
  }
  let envelope = genericEnvelope(res.status);
  try {
    const parsed = JSON.parse(body);
    if (parsed?.error?.what && parsed?.error?.cause && parsed?.error?.todo) {
      envelope = parsed.error; // server envelopes pass through verbatim
    }
  } catch {
    // non-JSON body — keep the generic envelope, body stays in detail
  }
  throw new ApiError(envelope, res.status, body);
}

export const api = {
  health: (base: string) => fetch(`${base.replace(/\/+$/, "")}/api/health`, { cache: "no-store" }),
  status: () => request<Status>("/api/status"),
  review: () => request<{ items: ReviewItem[] }>("/api/review"),
  approve: (id: string, type: NoteType) =>
    request<{ ok: boolean; moved_to: string }>(`/api/review/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ type }),
    }),
  capture: (text: string, tag: CaptureTag | null) =>
    request<{ id: string; status: string }>("/api/capture", {
      method: "POST",
      body: JSON.stringify({ text, tag }),
    }),
  // Pass S: the same route, a share shape instead of the quick-capture box —
  // insight rides alongside the url rather than being mashed into one blob.
  captureLink: (url: string, insight: string, tag: CaptureTag | null) =>
    request<{ id: string; status: string }>("/api/capture", {
      method: "POST",
      body: JSON.stringify({ url, insight: insight || null, tag }),
    }),
  // The recording goes up as the raw body (the server takes no multipart —
  // python-multipart isn't a locked dependency), so the blob's own mime type
  // is what tells the server which extension the inbox file gets.
  captureAudio: (blob: Blob, tag: CaptureTag | null) => {
    const params = new URLSearchParams();
    if (tag) params.set("tag", tag);
    const query = params.toString();
    return request<{ id: string; status: string }>(
      `/api/capture/audio${query ? `?${query}` : ""}`,
      {
        method: "POST",
        body: blob,
        headers: { "Content-Type": blob.type || "audio/webm" },
      },
    );
  },
  // Pass V2/V4: the photo button — the browser has already downscaled and
  // converted the blob to JPEG (Today.tsx's canvas step), same raw-body
  // transport as captureAudio. `insight` and `name` ride as query params,
  // mirroring capture/audio's shape.
  captureImage: (blob: Blob, tag: CaptureTag | null, name: string, insight: string) => {
    const params = new URLSearchParams();
    if (tag) params.set("tag", tag);
    if (name) params.set("name", name);
    if (insight) params.set("insight", insight);
    const query = params.toString();
    return request<{ id: string; status: string }>(
      `/api/capture/image${query ? `?${query}` : ""}`,
      {
        method: "POST",
        body: blob,
        headers: { "Content-Type": blob.type || "image/jpeg" },
      },
    );
  },
  people: () => request<{ items: Person[] }>("/api/people"),
  // Pass X: one name, one channel — feeding the warm-up engine without Obsidian
  addTarget: (name: string, kind: ChannelKind, value: string) =>
    request<Person>("/api/people", {
      method: "POST",
      body: JSON.stringify({ name, channel: { kind, value } }),
    }),
  person: (id: string) => request<PersonDetail>(`/api/people/${id}`),
  personDraft: (id: string, channel?: string) =>
    request<PersonDraft>(`/api/people/${id}/draft`, {
      method: "POST",
      body: JSON.stringify({ channel: channel ?? null }),
    }),
  logContact: (id: string, note: string, channel: string) =>
    request<ContactResult>(`/api/people/${id}/contact`, {
      method: "POST",
      body: JSON.stringify({ note, channel }),
    }),
  setWarmth: (id: string, stage: WarmthStage) =>
    request<Person>(`/api/people/${id}/warmth`, {
      method: "POST",
      body: JSON.stringify({ stage }),
    }),
  enrichPerson: (id: string) =>
    request<EnrichResult>(`/api/people/${id}/enrich`, { method: "POST" }),
  // Pass D: preview is the dry run of push — the human confirms the exact text
  // the server will write, and nothing leaves without that tap.
  pushPreview: (id: string, target: PushTarget) =>
    request<PushPreview>(`/api/people/${id}/push/preview`, {
      method: "POST",
      body: JSON.stringify({ target }),
    }),
  push: (id: string, target: PushTarget, text: string) =>
    request<PushResult>(`/api/people/${id}/push`, {
      method: "POST",
      body: JSON.stringify({ target, text }),
    }),
  pushQueue: () =>
    request<{ items: PushQueueItem[]; available: PushAvailability }>("/api/push/queue"),
  voice: () => request<VoiceStatus>("/api/people/voice"),
  saveVoice: (samples: string[]) =>
    request<VoiceStatus>("/api/people/voice", {
      method: "POST",
      body: JSON.stringify({ samples }),
    }),
  failed: () => request<{ items: FailedItem[] }>("/api/failed"),
  retry: (id: number) =>
    request<{ ok: boolean }>(`/api/failed/${id}/retry`, { method: "POST" }),
  events: (status?: string, limit = 100) =>
    request<{ events: EventRow[] }>(
      `/api/events?limit=${limit}${status ? `&status=${status}` : ""}`,
    ),
  run: () => request<{ started: boolean }>("/api/run", { method: "POST" }),
  streak: () => request<Streak>("/api/streak"),
  resurfaced: () => request<{ note: ResurfacedNote | null }>("/api/resurfaced"),
  integrations: (fresh = false) =>
    request<IntegrationsResponse>(`/api/integrations${fresh ? "?fresh=1" : ""}`),
  setEngine: (engine: EngineName) =>
    request<{ ok: boolean; engine: EngineName }>("/api/integrations/engine", {
      method: "POST",
      body: JSON.stringify({ engine }),
    }),
  ntfyTest: () => request<{ ok: boolean }>("/api/integrations/ntfy/test", { method: "POST" }),
  // Google (Pass 12): read + draft only. There is deliberately no send call
  // here and no send route on the server — CLAUDE.md §4.
  googleConnect: (redirectUri: string) =>
    request<{ url: string }>(
      `/api/google/connect?redirect_uri=${encodeURIComponent(redirectUri)}`,
    ),
  googleInbox: () => request<{ items: GmailMessage[] }>("/api/google/inbox"),
  googleEvents: () => request<{ items: CalendarEvent[] }>("/api/google/events"),
  googleDraft: (draft: DraftWrite) =>
    request<DraftCreated>("/api/google/draft", {
      method: "POST",
      body: JSON.stringify(draft),
    }),
  googleDisconnect: () =>
    request<{ ok: boolean }>("/api/google/disconnect", { method: "POST" }),
  todos: (range: TodoRange) => request<{ items: TodoItem[] }>(`/api/todos?range=${range}`),
  toggleTodo: (id: string) =>
    request<{ ok: boolean; done: boolean }>(`/api/todos/${id}/toggle`, { method: "POST" }),
  build: (fresh = false) => request<BuildResponse>(`/api/build${fresh ? "?fresh=1" : ""}`),
  providers: () => request<{ providers: ProviderStat[] }>("/api/providers"),
  config: () => request<AppConfig>("/api/config"),
  putConfig: (changes: ConfigWrite) =>
    request<AppConfig>("/api/config", { method: "PUT", body: JSON.stringify(changes) }),
  backup: () => request<BackupResult>("/api/backup", { method: "POST" }),
  backupStatus: () => request<BackupStatus>("/api/backup"),
  selfcheck: () => request<SelfCheckResponse>("/api/selfcheck"),

  // ---- Resource OS (Pass 6) ----
  resources: (params: {
    category?: string;
    status?: string;
    q?: string;
    has_insight?: boolean;
    sort?: string;
  } = {}) => {
    const qs = new URLSearchParams();
    if (params.category) qs.set("category", params.category);
    if (params.status) qs.set("status", params.status);
    if (params.q) qs.set("q", params.q);
    if (params.has_insight) qs.set("has_insight", "true");
    if (params.sort) qs.set("sort", params.sort);
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<{ items: Resource[] }>(`/api/resources${suffix}`);
  },
  resource: (id: string) => request<ResourceDetail>(`/api/resources/${encodeURIComponent(id)}`),
  setResourceStatus: (id: string, status: ResourceStatus) =>
    request<Resource>(`/api/resources/${encodeURIComponent(id)}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),
  setResourceInsight: (id: string, text: string) =>
    request<Resource>(`/api/resources/${encodeURIComponent(id)}/insight`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  sampleCount: (olderThan: SampleScope) =>
    request<SampleCount>(`/api/resources/sample/count?older_than=${olderThan}`),
  deleteSample: (olderThan: SampleScope) =>
    request<SamplePurgeResult>(`/api/resources/sample?older_than=${olderThan}`, {
      method: "DELETE",
    }),
};
