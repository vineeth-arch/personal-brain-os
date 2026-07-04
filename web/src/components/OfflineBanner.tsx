import { useSyncExternalStore } from "react";
import { isOffline, subscribeOffline } from "../api/client";

// The offline strip (Pass 5): shown when requests can't REACH the server —
// deliberately distinct from error states (a server that answers with an
// error is online; this banner is only for "nothing answered at all").
// Screens keep showing their last data underneath (usePolling holds stale
// data through background failures). Neutral tonal styling — the banner must
// never read as a second accent.
export function OfflineBanner() {
  const offline = useSyncExternalStore(subscribeOffline, isOffline);
  if (!offline) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      className="bg-cal-muted border-emphasis border-b px-5 py-2.5"
    >
      <p className="text-emphasis text-sm font-bold">
        Cockpit can't reach your server.
      </p>
      <p className="text-subtle text-xs">
        Same network as the server? Tunnel up? Showing the last data that loaded — it
        reconnects by itself.
      </p>
    </div>
  );
}
