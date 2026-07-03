import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { NoteType, ReviewItem } from "../api/types";
import { NOTE_TYPES } from "../api/types";
import { ErrorState } from "../components/ErrorState";
import { StreakDots } from "../components/StreakDots";
import { toast } from "../components/Toast";
import { usePolling } from "../hooks/usePolling";

const reducedMotion = () =>
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function confidenceSentence(item: ReviewItem): string {
  const pct = Math.round(item.confidence * 100);
  return `I think this is a ${item.suggested_type} — ${pct}% sure.`;
}

interface CardProps {
  item: ReviewItem;
  isTop: boolean;
  onDecide: (item: ReviewItem, type: NoteType) => void;
  leaving: boolean;
}

function ReviewCard({ item, isTop, onDecide, leaving }: CardProps) {
  return (
    <article
      data-review-card={item.id}
      className={`bg-subtle border-subtle overflow-hidden rounded-xl border p-5 transition-all duration-250 motion-reduce:transition-none ${
        leaving ? "max-h-0 translate-x-8 py-0 opacity-0" : "max-h-[40rem] opacity-100"
      }`}
    >
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
        {item.created} · needs review
      </p>
      <h3 className="font-cal text-emphasis mt-2 text-xl font-bold leading-tight -tracking-[0.01em]">
        {item.title}
      </h3>
      <p className="text-default mt-2 line-clamp-3 text-sm">{item.excerpt}</p>
      <p className="text-emphasis mt-3 text-sm font-semibold">{confidenceSentence(item)}</p>

      <button
        type="button"
        onClick={() => onDecide(item, item.suggested_type)}
        className={`mt-4 min-h-12 w-full rounded-xl text-base font-bold ${
          isTop ? "bg-brand-default text-brand" : "bg-inverted text-inverted"
        }`}
      >
        Approve as {item.suggested_type}
      </button>

      <details className="mt-3">
        <summary className="text-subtle min-h-11 cursor-pointer list-none py-2 text-sm font-semibold">
          It's something else…
        </summary>
        <div className="mt-1 flex flex-wrap gap-2" role="group" aria-label="Pick a type">
          {NOTE_TYPES.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => onDecide(item, t)}
              className={`bg-subtle border-subtle text-default hover:border-emphasis min-h-11 rounded-full border px-4 text-sm font-semibold ${
                t === item.suggested_type ? "border-emphasis text-emphasis" : ""
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </details>
    </article>
  );
}

function EmptyState() {
  const streak = usePolling(api.streak);
  return (
    <div className="pt-8">
      <h2 className="font-cal text-emphasis text-5xl font-extrabold leading-[0.95] -tracking-[0.02em]">
        Inbox zero.
      </h2>
      <p className="text-default mt-3 text-base">Nothing needs you.</p>
      {streak.data && (
        <div className="mt-10">
          <StreakDots streak={streak.data} />
        </div>
      )}
    </div>
  );
}

export function Triage() {
  const review = usePolling(api.review);
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [leaving, setLeaving] = useState<Set<string>>(new Set());
  // Optimistically decided ids — a refetch must not resurrect their cards
  // while the POST is still in flight.
  const decided = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (review.data) {
      setItems(review.data.items.filter((i) => !decided.current.has(i.id)));
    }
  }, [review.data]);

  const decide = async (item: ReviewItem, type: NoteType) => {
    // One tap = one decision. Animate out (instant under reduced motion), then POST.
    decided.current.add(item.id);
    const remove = () => setItems((cur) => (cur ? cur.filter((i) => i.id !== item.id) : cur));
    if (reducedMotion()) {
      remove();
    } else {
      setLeaving((s) => new Set(s).add(item.id));
      setTimeout(() => {
        remove();
        setLeaving((s) => {
          const next = new Set(s);
          next.delete(item.id);
          return next;
        });
      }, 250);
    }
    try {
      await api.approve(item.id, type);
      toast(type === item.suggested_type ? `Approved as ${type}` : `Filed as ${type}`);
    } catch (err) {
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(
        envelope ? `${envelope.what} ${envelope.todo}` : "That decision didn't reach the server.",
        "error",
      );
      decided.current.delete(item.id);
      setItems((cur) => (cur && !cur.some((i) => i.id === item.id) ? [item, ...cur] : cur));
    }
  };

  if (review.loading && items === null) {
    return (
      <div className="space-y-4" aria-hidden="true">
        <div className="bg-subtle h-56 animate-pulse rounded-xl" />
        <div className="bg-subtle h-56 animate-pulse rounded-xl" />
      </div>
    );
  }

  if (review.error && items === null) {
    return (
      <ErrorState
        envelope={review.error.envelope}
        detail={review.error.detail}
        onRetry={review.refetch}
      />
    );
  }

  const queue = items ?? [];
  if (queue.length === 0) return <EmptyState />;

  return (
    <div className="space-y-4">
      <p className="text-subtle text-sm font-semibold">
        {queue.length} capture{queue.length === 1 ? "" : "s"} to triage — one decision each.
      </p>
      {queue.map((item, i) => (
        <ReviewCard
          key={item.id}
          item={item}
          isTop={i === 0}
          onDecide={decide}
          leaving={leaving.has(item.id)}
        />
      ))}
    </div>
  );
}
