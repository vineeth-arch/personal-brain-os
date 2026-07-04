import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { Resource, ResourceDetail, ResourceStatus, SampleScope } from "../api/types";
import { RESOURCE_STATUSES } from "../api/types";
import { ErrorState } from "../components/ErrorState";
import { toast } from "../components/Toast";
import { usePolling } from "../hooks/usePolling";

// The pink spark (#FF006C via bg-spark/text-spark) is reserved on this screen
// for exactly ONE thing: the insight indicator dot. Teal lives only on the
// RESOURCE OS eyebrow. Everything selected/active is tonal, never accent —
// matching the rest of the cockpit (see Today.tsx / QuickCapture).

const SCOPE_LABELS: Record<SampleScope, string> = {
  "1d": "older than 1 day",
  "1w": "older than 1 week",
  "1m": "older than 1 month",
  all: "of any age",
};

const STATUS_ADVANCE_LABEL: Record<ResourceStatus, string> = {
  inbox: "Start consuming", // → to-consume
  "to-consume": "Mark consumed", // → consumed
  consumed: "Mark referenced", // → referenced
  referenced: "Archive", // → archived
  archived: "", // end of lifecycle
};

function titleCase(s: string): string {
  return s.replace(/(^|[\s-])([a-z])/g, (_, sep, ch) => sep + ch.toUpperCase());
}

function nextStatus(status: ResourceStatus): ResourceStatus | null {
  const i = RESOURCE_STATUSES.indexOf(status);
  return i >= 0 && i < RESOURCE_STATUSES.length - 1 ? RESOURCE_STATUSES[i + 1] : null;
}

function ageDays(created: string): number {
  const d = new Date(`${created}T00:00:00`);
  if (Number.isNaN(d.getTime())) return Infinity;
  return (Date.now() - d.getTime()) / 86_400_000;
}

// ---- URL-backed filter state ------------------------------------------------
// Filters live in the hash query (#/resources?category=book&status=…&q=…&
// insight=1) so the view is shareable and survives refresh. Internal changes
// use history.replaceState (no back-button spam); the base route stays
// "resources" so App's router never re-evaluates.

interface Filters {
  category: string;
  status: string;
  q: string;
  insight: boolean;
}

function readFilters(): Filters {
  const p = new URLSearchParams(window.location.hash.split("?")[1] || "");
  return {
    category: p.get("category") || "",
    status: p.get("status") || "",
    q: p.get("q") || "",
    insight: p.get("insight") === "1",
  };
}

function writeFilters(f: Filters): void {
  const p = new URLSearchParams();
  if (f.category) p.set("category", f.category);
  if (f.status) p.set("status", f.status);
  if (f.q) p.set("q", f.q);
  if (f.insight) p.set("insight", "1");
  const qs = p.toString();
  history.replaceState(null, "", qs ? `#/resources?${qs}` : "#/resources");
}

const EMPTY_FILTERS: Filters = { category: "", status: "", q: "", insight: false };

function useResourceList() {
  const [data, setData] = useState<Resource[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .resources({ sort: "created" })
      .then((r) => {
        if (alive) {
          setData(r.items);
          setError(null);
        }
      })
      .catch((e) => {
        if (alive) setError(e instanceof ApiError ? e : null);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [nonce]);
  return { data, error, loading, refetch: () => setNonce((n) => n + 1) };
}

// ---- cards ------------------------------------------------------------------

function CoverFallback({ category }: { category: string }) {
  return (
    <div className="bg-emphasis flex h-full w-full items-center justify-center" aria-hidden="true">
      <span className="font-cal text-emphasis text-4xl font-extrabold opacity-40">
        {(category[0] || "?").toUpperCase()}
      </span>
    </div>
  );
}

function ResourceCard({ item, onOpen }: { item: Resource; onOpen: (r: Resource) => void }) {
  const [failed, setFailed] = useState(false);
  const showImage = item.cover && !failed;
  return (
    <button
      type="button"
      onClick={() => onOpen(item)}
      className="group focus-visible:outline-brand-default block text-left"
      aria-label={`${titleCase(item.category)}: ${item.title}${item.has_insight ? ", has an insight" : ""}`}
    >
      <div className="bg-subtle border-subtle relative aspect-[5/7] overflow-hidden rounded-xl border">
        {showImage ? (
          <img
            src={item.cover!}
            alt={item.title}
            loading="lazy"
            onError={() => setFailed(true)}
            className="h-full w-full object-cover transition-opacity group-hover:opacity-90 motion-reduce:transition-none"
          />
        ) : (
          <CoverFallback category={item.category} />
        )}
        {item.has_insight && (
          // the ONE reserved use of the pink spark on this screen
          <span
            className="bg-spark absolute right-2 top-2 h-3 w-3 rounded-full ring-2 ring-black/20"
            role="img"
            aria-label="Has an insight"
          />
        )}
      </div>
      <p className="text-subtle mt-2 text-[10px] font-bold uppercase tracking-[0.12em]">
        {titleCase(item.category)}
      </p>
      <p className="text-emphasis mt-0.5 line-clamp-2 text-sm font-semibold leading-tight">
        {item.title}
      </p>
      <span className="bg-subtle text-subtle mt-1.5 inline-block rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide">
        {titleCase(item.status)}
      </span>
    </button>
  );
}

function CardSkeleton() {
  return (
    <div aria-hidden="true">
      <div className="bg-subtle aspect-[5/7] animate-pulse rounded-xl" />
      <div className="bg-subtle mt-2 h-3 w-2/3 animate-pulse rounded" />
      <div className="bg-subtle mt-1.5 h-3 w-full animate-pulse rounded" />
    </div>
  );
}

// ---- detail drawer ----------------------------------------------------------

function Drawer({
  item,
  vault,
  onClose,
  onChanged,
}: {
  item: Resource;
  vault: string | null;
  onClose: () => void;
  onChanged: (updated: Resource) => void;
}) {
  const [current, setCurrent] = useState<Resource>(item);
  const [detail, setDetail] = useState<ResourceDetail | null>(null);
  const [coverFailed, setCoverFailed] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const insightRef = useRef<HTMLTextAreaElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => setCurrent(item), [item]);

  useEffect(() => {
    let alive = true;
    api
      .resource(item.id)
      .then((d) => alive && setDetail(d))
      .catch(() => {
        /* the summary already renders; extras are best-effort */
      });
    return () => {
      alive = false;
    };
  }, [item.id]);

  // Escape closes; focus the close button on open (basic focus management).
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const obsidianLink =
    vault &&
    `obsidian://open?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(
      current.file.replace(/\.md$/, ""),
    )}`;

  const advance = async () => {
    const target = nextStatus(current.status);
    if (!target || busy) return;
    setBusy(true);
    try {
      const updated = await api.setResourceStatus(current.id, target);
      setCurrent(updated);
      onChanged(updated);
      toast(`Marked ${titleCase(target).toLowerCase()}.`);
      // consumption and insight are one gesture apart
      if (target === "consumed" && !updated.has_insight) {
        setTimeout(() => insightRef.current?.focus(), 0);
      }
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(false);
    }
  };

  const saveInsight = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    setBusy(true);
    try {
      const updated = await api.setResourceInsight(current.id, text);
      setCurrent(updated);
      onChanged(updated);
      setDraft("");
      toast("Takeaway saved.");
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(false);
    }
  };

  const showCover = current.cover && !coverFailed;
  const advanceLabel = STATUS_ADVANCE_LABEL[current.status];

  return (
    <div
      className="fixed inset-0 z-40 flex items-end justify-center sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-label={current.title}
    >
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-black/50"
      />
      <div className="bg-default border-subtle relative max-h-[92dvh] w-full max-w-lg overflow-y-auto rounded-t-2xl border sm:rounded-2xl">
        <div className="flex items-start gap-4 p-5">
          <div className="bg-subtle border-subtle h-40 w-28 shrink-0 overflow-hidden rounded-xl border">
            {showCover ? (
              <img
                src={current.cover!}
                alt={current.title}
                onError={() => setCoverFailed(true)}
                className="h-full w-full object-cover"
              />
            ) : (
              <CoverFallback category={current.category} />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-subtle text-[10px] font-bold uppercase tracking-[0.14em]">
              {titleCase(current.category)}
            </p>
            <h2 className="font-cal text-emphasis mt-1 text-2xl font-extrabold leading-[0.98] -tracking-[0.02em]">
              {current.title}
            </h2>
            <p className="text-subtle mt-2 text-xs">
              <span className="bg-subtle text-emphasis rounded px-1.5 py-0.5 font-bold uppercase tracking-wide">
                {titleCase(current.status)}
              </span>
              {current.created && <span className="ml-2">Added {current.created}</span>}
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="border-subtle text-subtle hover:border-emphasis flex h-11 w-11 shrink-0 items-center justify-center rounded-full border"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        {detail?.description && (
          <p className="text-default px-5 text-sm">{detail.description}</p>
        )}

        <div className="flex flex-wrap gap-2 p-5">
          {current.url && (
            <a
              href={current.url}
              target="_blank"
              rel="noopener noreferrer"
              className="border-emphasis text-emphasis flex min-h-11 items-center rounded-xl border px-4 text-sm font-bold"
            >
              Open source ↗
            </a>
          )}
          {obsidianLink && (
            <a
              href={obsidianLink}
              className="border-emphasis text-emphasis flex min-h-11 items-center rounded-xl border px-4 text-sm font-bold"
            >
              Open in Obsidian
            </a>
          )}
        </div>

        {/* Insight block: present → shown with the spark accent; absent → a
            single quiet takeaway input. */}
        <div className="px-5 pb-5">
          {current.insight ? (
            <div className="bg-cal-stamp border-subtle rounded-xl border p-4">
              <p className="text-spark flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.14em]">
                <span aria-hidden="true" className="bg-spark h-2 w-2 rounded-full" />
                Insight
              </p>
              <p className="text-emphasis mt-2 text-sm">{current.insight}</p>
            </div>
          ) : (
            <div>
              <label
                htmlFor="takeaway"
                className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]"
              >
                One takeaway?
              </label>
              <div className="mt-2 flex gap-2">
                <textarea
                  id="takeaway"
                  ref={insightRef}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  rows={2}
                  placeholder="What's the one thing worth keeping?"
                  className="bg-subtle border-subtle text-emphasis min-h-12 w-full resize-none rounded-xl border px-4 py-2 text-sm"
                />
                <button
                  type="button"
                  onClick={saveInsight}
                  disabled={busy || !draft.trim()}
                  className="bg-inverted text-inverted min-h-12 shrink-0 self-start rounded-xl px-4 text-sm font-bold disabled:opacity-40"
                >
                  Save
                </button>
              </div>
            </div>
          )}
        </div>

        {advanceLabel && (
          <div className="border-subtle border-t p-5">
            <button
              type="button"
              onClick={advance}
              disabled={busy}
              className="bg-emphasis text-emphasis min-h-12 w-full rounded-xl text-sm font-bold disabled:opacity-40"
            >
              {advanceLabel}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function toastError(err: unknown): void {
  const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
  toast(envelope ? `${envelope.what} ${envelope.todo}` : "That didn't reach the server.", "error");
}

// ---- sample-data control ----------------------------------------------------

function SampleControl({ items, onPurged }: { items: Resource[]; onPurged: () => void }) {
  const [scope, setScope] = useState<SampleScope>("1m");
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const inScope = useMemo(() => {
    const min = { "1d": 1, "1w": 7, "1m": 30, all: 0 }[scope];
    return items.filter((i) => i.sample && (scope === "all" || ageDays(i.created) >= min)).length;
  }, [items, scope]);

  const totalSample = items.filter((i) => i.sample).length;
  if (totalSample === 0) return null; // clean UI once real notes are all that remain

  const remove = async () => {
    setBusy(true);
    try {
      const result = await api.deleteSample(scope);
      toast(result.message);
      onPurged();
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(false);
      setConfirming(null);
    }
  };

  return (
    <section className="bg-cal-muted border-subtle mt-10 rounded-xl border p-5">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">Sample data</p>
      <p className="text-default mt-2 text-sm">
        {totalSample} seeded sample note{totalSample === 1 ? "" : "s"} in this vault. These are the
        only notes cleanup can ever touch.
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <label htmlFor="scope" className="text-subtle text-xs font-semibold">
          Remove sample notes
        </label>
        <select
          id="scope"
          value={scope}
          onChange={(e) => setScope(e.target.value as SampleScope)}
          className="bg-subtle border-subtle text-emphasis min-h-11 rounded-xl border px-3 text-sm"
        >
          <option value="1d">older than 1 day</option>
          <option value="1w">older than 1 week</option>
          <option value="1m">older than 1 month</option>
          <option value="all">of any age</option>
        </select>
        <button
          type="button"
          onClick={() => setConfirming(inScope)}
          disabled={busy}
          className="border-emphasis text-emphasis min-h-11 rounded-xl border px-4 text-sm font-bold disabled:opacity-40"
        >
          Remove sample data
        </button>
        <span className="text-muted text-xs">{inScope} match this scope</span>
      </div>

      {confirming !== null && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-5"
          role="alertdialog"
          aria-modal="true"
          aria-label="Confirm sample data removal"
        >
          <button
            type="button"
            aria-label="Cancel"
            onClick={() => setConfirming(null)}
            className="absolute inset-0 bg-black/50"
          />
          <div className="bg-default border-emphasis relative w-full max-w-sm rounded-2xl border p-5">
            <h3 className="font-cal text-emphasis text-xl font-bold leading-tight">
              Remove sample data?
            </h3>
            <p className="text-default mt-2 text-sm">
              {confirming === 0 ? (
                <>No sample notes are {SCOPE_LABELS[scope]}. Nothing will be removed.</>
              ) : (
                <>
                  This removes {confirming} sample note{confirming === 1 ? "" : "s"} {SCOPE_LABELS[scope]},
                  git-committed first — your real notes cannot be touched.
                </>
              )}
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirming(null)}
                className="border-subtle text-emphasis min-h-11 rounded-xl border px-4 text-sm font-bold"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={remove}
                disabled={busy || confirming === 0}
                className="bg-inverted text-inverted min-h-11 rounded-xl px-4 text-sm font-bold disabled:opacity-40"
              >
                Remove
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

// ---- screen -----------------------------------------------------------------

export function Resources() {
  const { data, error, loading, refetch } = useResourceList();
  const status = usePolling(api.status, 60_000); // vault name for the obsidian link
  const [filters, setFilters] = useState<Filters>(readFilters);
  const [selected, setSelected] = useState<Resource | null>(null);

  const update = useCallback((patch: Partial<Filters>) => {
    setFilters((f) => {
      const next = { ...f, ...patch };
      writeFilters(next);
      return next;
    });
  }, []);

  const categories = useMemo(
    () => Array.from(new Set((data || []).map((r) => r.category))).sort(),
    [data],
  );

  const filtered = useMemo(() => {
    const q = filters.q.trim().toLowerCase();
    return (data || []).filter((r) => {
      if (filters.category && r.category !== filters.category) return false;
      if (filters.status && r.status !== filters.status) return false;
      if (filters.insight && !r.has_insight) return false;
      if (q && !r.title.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [data, filters]);

  const total = data?.length ?? 0;
  const withInsight = (data || []).filter((r) => r.has_insight).length;
  const anyFilter = Boolean(filters.category || filters.status || filters.q || filters.insight);

  if (loading && !data) {
    return (
      <div>
        <Header total={0} withInsight={0} skeleton />
        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5">
          {Array.from({ length: 10 }).map((_, i) => (
            <CardSkeleton key={i} />
          ))}
        </div>
      </div>
    );
  }

  if (error && !data) {
    return <ErrorState envelope={error.envelope} detail={error.detail} onRetry={refetch} />;
  }

  return (
    <div>
      <Header total={total} withInsight={withInsight} />

      {/* Sticky filter row */}
      <div className="bg-default sticky top-0 z-10 -mx-5 mt-5 px-5 py-3">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Category">
          <FilterChip active={!filters.category} onClick={() => update({ category: "" })}>
            All
          </FilterChip>
          {categories.map((c) => (
            <FilterChip
              key={c}
              active={filters.category === c}
              onClick={() => update({ category: filters.category === c ? "" : c })}
            >
              {titleCase(c)}
            </FilterChip>
          ))}
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <div
            className="border-subtle flex overflow-hidden rounded-full border"
            role="group"
            aria-label="Status"
          >
            <Segment active={!filters.status} onClick={() => update({ status: "" })}>
              All
            </Segment>
            {RESOURCE_STATUSES.map((s) => (
              <Segment
                key={s}
                active={filters.status === s}
                onClick={() => update({ status: filters.status === s ? "" : s })}
              >
                {titleCase(s)}
              </Segment>
            ))}
          </div>
        </div>

        <div className="mt-2 flex items-center gap-2">
          <input
            type="search"
            value={filters.q}
            onChange={(e) => update({ q: e.target.value })}
            placeholder="Search titles…"
            aria-label="Search resources"
            className="bg-subtle border-subtle text-emphasis min-h-11 w-full rounded-xl border px-4 text-sm"
          />
          <button
            type="button"
            aria-pressed={filters.insight}
            onClick={() => update({ insight: !filters.insight })}
            className={`min-h-11 shrink-0 rounded-full border px-4 text-sm font-semibold ${
              filters.insight
                ? "bg-emphasis border-emphasis text-emphasis"
                : "bg-subtle border-subtle text-subtle"
            }`}
          >
            With insight
          </button>
        </div>
      </div>

      {/* Gallery */}
      {filtered.length === 0 ? (
        total === 0 ? (
          <EmptyState
            title="No resources yet"
            body="Say “Resource: …” into a capture, or run the seed script."
          />
        ) : (
          <EmptyState
            title="Nothing matches"
            body="No resource fits these filters."
            action={
              <button
                type="button"
                onClick={() => update(EMPTY_FILTERS)}
                className="border-emphasis text-emphasis mt-4 min-h-11 rounded-xl border px-5 text-sm font-bold"
              >
                Clear filters
              </button>
            }
          />
        )
      ) : (
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5">
          {filtered.map((r) => (
            <ResourceCard key={r.id} item={r} onOpen={setSelected} />
          ))}
        </div>
      )}

      {!anyFilter && data && <SampleControl items={data} onPurged={refetch} />}

      {selected && (
        <Drawer
          item={selected}
          vault={status.data?.vault ?? null}
          onClose={() => setSelected(null)}
          onChanged={() => refetch()}
        />
      )}
    </div>
  );
}

function Header({
  total,
  withInsight,
  skeleton,
}: {
  total: number;
  withInsight: number;
  skeleton?: boolean;
}) {
  return (
    <header>
      <p className="text-brand-default text-[11px] font-bold uppercase tracking-[0.18em]">
        Resource OS
      </p>
      <h1 className="font-cal text-emphasis mt-1 text-4xl font-extrabold leading-[0.95] -tracking-[0.03em]">
        Your library
      </h1>
      <p className="text-subtle mt-2 text-sm">
        {skeleton ? (
          <span aria-hidden="true">Loading…</span>
        ) : (
          <>
            {total} resource{total === 1 ? "" : "s"} · {withInsight} with insight
            {withInsight === 1 ? "" : "s"}
          </>
        )}
      </p>
    </header>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`min-h-11 rounded-full border px-4 text-sm font-semibold ${
        active
          ? "bg-emphasis border-emphasis text-emphasis"
          : "bg-subtle border-subtle text-subtle"
      }`}
    >
      {children}
    </button>
  );
}

function Segment({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`min-h-11 px-3 text-xs font-bold ${
        active ? "bg-emphasis text-emphasis" : "text-subtle"
      }`}
    >
      {children}
    </button>
  );
}

function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="border-subtle mt-8 rounded-xl border border-dashed p-8">
      <p className="font-cal text-emphasis text-xl font-bold">{title}</p>
      <p className="text-subtle mt-2 max-w-sm text-sm">{body}</p>
      {action}
    </div>
  );
}
