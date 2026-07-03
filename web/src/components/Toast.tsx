import { useSyncExternalStore } from "react";

export interface ToastItem {
  id: number;
  message: string;
  kind: "ok" | "error";
}

// Module-level store so toast() is callable from anywhere (screens, client)
// without context plumbing. ToastRegion subscribes via useSyncExternalStore.
let toasts: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

export function toast(message: string, kind: "ok" | "error" = "ok"): void {
  const item: ToastItem = { id: nextId++, message, kind };
  toasts = [...toasts.slice(-2), item]; // max 3 visible
  emit();
  setTimeout(() => {
    toasts = toasts.filter((t) => t.id !== item.id);
    emit();
  }, kind === "error" ? 8000 : 4000);
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function ToastRegion() {
  const items = useSyncExternalStore(subscribe, () => toasts);
  return (
    // Mounted permanently so screen readers announce content changes.
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 bottom-20 z-50 flex flex-col items-center gap-2 px-4"
    >
      {items.map((t) => (
        <div
          key={t.id}
          className={`w-full max-w-sm rounded-xl border px-4 py-3 text-sm font-semibold shadow-lg ${
            t.kind === "error"
              ? "bg-cal-muted border-emphasis text-emphasis"
              : "bg-inverted text-inverted border-transparent"
          }`}
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}
