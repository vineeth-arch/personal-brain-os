import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";

interface PollState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  refetch: () => void;
}

// One hook for one-shot fetches (no interval) and polling (intervalMs set).
// setTimeout chain instead of setInterval so slow responses never stack;
// pauses while the tab is hidden and refetches on focus/visibility. Stale
// data is kept through background errors so a blip doesn't blank the screen.
//
// `deps` re-runs the fetch when the CALLER's inputs change. Without it, `fn`
// lived only in a ref: a screen whose fetcher closes over state (the Pipeline
// event filter) kept showing the previous result until the next poll fired, so
// clicking a filter looked like it did nothing for twenty seconds.
export function usePolling<T>(
  fn: () => Promise<T>,
  intervalMs?: number,
  deps: unknown[] = [],
): PollState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const alive = useRef(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const tick = useCallback(async () => {
    clearTimeout(timer.current);
    try {
      const result = await fnRef.current();
      if (!alive.current) return;
      setData(result);
      setError(null);
    } catch (e) {
      if (!alive.current) return;
      setError(
        e instanceof ApiError
          ? e
          : new ApiError(
              {
                what: "Something unexpected went wrong while loading.",
                cause: "A bug in the app, not your pipeline.",
                todo: "Reload the app; if it repeats, check the browser console.",
              },
              0,
              String(e),
            ),
      );
    } finally {
      if (alive.current) {
        setLoading(false);
        if (intervalMs) {
          timer.current = setTimeout(() => {
            if (!document.hidden) void tick();
          }, intervalMs);
        }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, ...deps]);

  useEffect(() => {
    alive.current = true;
    void tick();
    const onVisible = () => {
      if (!document.hidden) void tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      alive.current = false;
      clearTimeout(timer.current);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [tick]);

  return { data, error, loading, refetch: () => void tick() };
}
