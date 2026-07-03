import type {
  CaptureTag,
  EngineName,
  ErrorEnvelope,
  EventRow,
  FailedItem,
  IntegrationsResponse,
  NoteType,
  ResurfacedNote,
  ReviewItem,
  Status,
  Streak,
} from "./types";

const BASE_KEY = "cockpit.apiBase";
const TOKEN_KEY = "cockpit.token";
export const DEFAULT_API_BASE = "http://127.0.0.1:8000";

// Fired when the server rejects the token so App can swap to the connect screen.
export const UNAUTHORIZED_EVENT = "cockpit:unauthorized";

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
    cause: `It answered with an unexpected error (HTTP ${status}).`,
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
    throw new ApiError(NETWORK_ENVELOPE, 0, String(e));
  }

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
};
