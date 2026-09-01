import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { SearchHit } from "../api/types";
import { ErrorState } from "../components/ErrorState";
import { usePolling } from "../hooks/usePolling";

const DEBOUNCE_MS = 250;
const MIN_QUERY_LEN = 2;

function useDebounced(value: string, delayMs: number): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}

/** Wraps every case-insensitive occurrence of `q` in the text with a tonal
 * <mark> — never the accent color on small body text (DESIGNSYSTEM.md §6:
 * the accent is reserved for large text/single "lit" elements, not search
 * highlighting scattered across a results list). */
function Highlighted({ text, q }: { text: string; q: string }) {
  if (!q.trim()) return <>{text}</>;
  const escaped = q.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const parts = text.split(new RegExp(`(${escaped})`, "ig"));
  return (
    <>
      {parts.map((part, i) =>
        part.toLowerCase() === q.trim().toLowerCase() ? (
          <mark key={i} className="bg-emphasis text-emphasis rounded px-0.5">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

function typeLabel(type: string): string {
  return type ? type.charAt(0).toUpperCase() + type.slice(1) : "Note";
}

function ResultRow({ hit, vault, q }: { hit: SearchHit; vault: string | null; q: string }) {
  const obsidianLink = vault
    ? `obsidian://open?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(
        hit.file.replace(/\.md$/, ""),
      )}`
    : null;
  return (
    <li className="bg-subtle border-subtle rounded-xl border p-4">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
        {typeLabel(hit.type)}
        {hit.match === "semantic" ? " · semantic" : ""}
      </p>
      <h3 className="font-cal text-emphasis mt-1 text-lg font-bold leading-tight -tracking-[0.01em]">
        <Highlighted text={hit.title} q={q} />
      </h3>
      {hit.excerpt && hit.excerpt !== hit.title && (
        <p className="text-default mt-1 text-sm">
          <Highlighted text={hit.excerpt} q={q} />
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-3">
        {obsidianLink && (
          <a href={obsidianLink} className="text-subtle hover:text-emphasis text-sm font-semibold underline">
            Open in Obsidian
          </a>
        )}
        {hit.type === "resource" && (
          <a
            href={`#/resources?q=${encodeURIComponent(hit.title)}`}
            className="text-subtle hover:text-emphasis text-sm font-semibold underline"
          >
            View in Resources
          </a>
        )}
        {hit.type === "person" && (
          <a href="#/people" className="text-subtle hover:text-emphasis text-sm font-semibold underline">
            View in People
          </a>
        )}
      </div>
    </li>
  );
}

export function Search() {
  const [query, setQuery] = useState("");
  const debounced = useDebounced(query, DEBOUNCE_MS);
  const status = usePolling(api.status, 60_000); // vault name for the obsidian link
  const inputRef = useRef<HTMLInputElement | null>(null);

  const [results, setResults] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const q = debounced.trim();
    if (q.length < MIN_QUERY_LEN) {
      setResults([]);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .search(q)
      .then((res) => {
        if (cancelled) return;
        setResults(res.items);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        const envelope = (err as { envelope?: { what: string } }).envelope;
        setError(envelope?.what ?? "The search didn't complete.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [debounced]);

  const trimmed = query.trim();
  const searched = debounced.trim().length >= MIN_QUERY_LEN;

  return (
    <div className="space-y-6">
      <section>
        <input
          ref={inputRef}
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search everything in the vault…"
          aria-label="Search everything in the vault"
          className="bg-subtle border-subtle text-emphasis min-h-14 w-full rounded-xl border px-4 text-lg"
        />
      </section>

      {!trimmed && (
        <p className="text-subtle text-sm">Type to search everything in the vault.</p>
      )}

      {trimmed && trimmed.length < MIN_QUERY_LEN && (
        <p className="text-subtle text-sm">Keep typing — at least {MIN_QUERY_LEN} characters.</p>
      )}

      {error && (
        <ErrorState
          envelope={{
            what: error,
            cause: "The server couldn't complete the search.",
            todo: "Try again in a moment.",
          }}
          detail=""
          onRetry={() => setQuery((q) => q)}
        />
      )}

      {searched && !error && !loading && results.length === 0 && (
        <p className="text-subtle text-sm">Nothing matches — try fewer words.</p>
      )}

      {searched && !error && results.length > 0 && (
        <>
          {/* the ONE accent on this screen: the result count, styled as an
              eyebrow — DESIGNSYSTEM.md §6 reserves the accent for large text
              or eyebrow-scale accents, never small body copy */}
          <p className="text-brand-default text-[11px] font-bold uppercase tracking-[0.08em]">
            {results.length} {results.length === 1 ? "result" : "results"}
          </p>
          <ul className="space-y-3">
            {results.map((hit) => (
              <ResultRow key={hit.id || hit.file} hit={hit} vault={status.data?.vault ?? null} q={debounced} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
