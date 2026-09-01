// Task F4: PWA offline outbox (page-side only — see sw.js's header comment
// for why the service worker never touches this). A capture that fails with
// a genuine network error (ApiError.status === 0) gets queued here under a
// generated id; drain() replays it later using that same id as the
// X-Capture-Key header, so a request that silently landed on the server
// before the client saw the network error never becomes a duplicate note
// (Task F3 built the server-side idempotency key this relies on).
import { api } from "./api/client";
import type { CaptureTag } from "./api/types";

export type QueuedCapture =
  | { id: string; kind: "text"; body: { text?: string; tag?: string; url?: string; insight?: string } }
  | { id: string; kind: "audio"; blob: Blob; tag: string; name: string }
  | { id: string; kind: "image"; blob: Blob; tag: string; name: string; insight?: string };

const DB_NAME = "cockpit-outbox";
const DB_VERSION = 1;
const STORE = "queue";
const AUDIO_MAX_BYTES = 25 * 1024 * 1024;

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      req.result.createObjectStore(STORE, { keyPath: "id" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function withStore<T>(
  mode: IDBTransactionMode,
  fn: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await openDb();
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const req = fn(tx.objectStore(STORE));
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  } finally {
    db.close();
  }
}

// ---- count store, mirrors client.ts's offline-store idiom exactly ----

let cachedCount = 0;
const countListeners = new Set<() => void>();

function notifyCount(n: number) {
  cachedCount = n;
  countListeners.forEach((l) => l());
}

export function subscribeOutboxCount(cb: () => void): () => void {
  countListeners.add(cb);
  return () => countListeners.delete(cb);
}

export function getOutboxCount(): number {
  return cachedCount;
}

async function refreshCount(): Promise<void> {
  try {
    const all = await withStore("readonly", (s) => s.getAllKeys());
    notifyCount(all.length);
  } catch {
    // IndexedDB unavailable (private browsing, disabled, etc.) — the count
    // just stays at whatever it last was; enqueue/drain below degrade the
    // same way, never throwing out to the caller
  }
}
// best-effort initial sync so a reload with items already queued shows the
// right count immediately, not just after the next enqueue/drain
void refreshCount();

// ---- enqueue / drain ----

// Plain union parameter type matching QueuedCapture minus `id` — reads
// clearly and avoids the conditional-type indirection. A plain `Omit` over
// a union collapses to the union's common keys only (losing the per-branch
// discrimination), so this distributes it: apply Omit to each member of
// the union separately, then re-union the results.
type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;
export type NewQueuedCapture = DistributiveOmit<QueuedCapture, "id">;

export async function enqueue(item: NewQueuedCapture): Promise<void> {
  if (item.kind === "audio" && item.blob.size > AUDIO_MAX_BYTES) {
    throw new Error("audio too large to queue"); // caller keeps existing manual-retry UI instead
  }
  const id = crypto.randomUUID();
  const queued = { id, ...item } as QueuedCapture;
  try {
    await withStore("readwrite", (s) => s.put(queued));
    await refreshCount();
  } catch {
    // IndexedDB write failed — nothing queued, caller's existing catch-block
    // error toast is what the user sees; this is a silent degrade to
    // "today's behavior" (capture is lost from the outbox's perspective,
    // exactly as it already is today without this feature)
  }
}

async function sendQueued(item: QueuedCapture): Promise<void> {
  if (item.kind === "text") {
    await api.capture(item.body.text ?? "", (item.body.tag as CaptureTag) ?? null, item.id);
  } else if (item.kind === "audio") {
    await api.captureAudio(item.blob, (item.tag as CaptureTag) || null, item.name, item.id);
  } else {
    await api.captureImage(item.blob, (item.tag as CaptureTag) || null, item.name,
                          item.insight ?? "", item.id);
  }
}

let draining = false;

export async function drain(): Promise<{ sent: number; left: number }> {
  if (draining) return { sent: 0, left: getOutboxCount() }; // one drain at a time
  draining = true;
  try {
    let items: QueuedCapture[];
    try {
      items = await withStore("readonly", (s) => s.getAll());
    } catch {
      return { sent: 0, left: 0 };
    }
    let sent = 0;
    for (const item of items) {
      try {
        await sendQueued(item);
        await withStore("readwrite", (s) => s.delete(item.id));
        sent++;
      } catch {
        break; // stop at the first failure — remaining items retry next trigger
      }
    }
    await refreshCount();
    return { sent, left: getOutboxCount() };
  } finally {
    draining = false;
  }
}
