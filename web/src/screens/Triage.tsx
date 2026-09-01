import { useEffect, useRef, useState } from "react";
import { api, getTriageTimerEnabled } from "../api/client";
import type { NoteType, ReviewItem, SuggestedAttendee } from "../api/types";
import { NOTE_TYPES } from "../api/types";
import { ErrorState } from "../components/ErrorState";
import { StreakDots } from "../components/StreakDots";
import { toast } from "../components/Toast";
import { usePolling } from "../hooks/usePolling";

const reducedMotion = () =>
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// The queue is bounded on purpose: five decisions is a sitting, not a backlog
// to scroll. PAGE is both the first slice and the size of each [n more] step.
const PAGE = 5;
const TIMER_SECONDS = 300;

function confidenceSentence(item: ReviewItem): string {
  // A conversation's type isn't a guess — two-or-more speakers decided it
  // deterministically (pipeline/watcher.py), so "100% sure" would be honest
  // but beside the point. The real ask on this card is confirming who's in it.
  if (item.suggested_type === "conversation") {
    return item.suggested_attendees.length > 0
      ? "More than one voice — confirm who's in it below."
      : "More than one voice in this recording.";
  }
  const pct = Math.round(item.confidence * 100);
  return `I think this is a ${item.suggested_type} — ${pct}% sure.`;
}

interface CardProps {
  item: ReviewItem;
  isTop: boolean;
  onDecide: (item: ReviewItem, type: NoteType, attendeeIds: string[]) => void;
  leaving: boolean;
}

// Toggleable chips for the pipeline's attendee suggestions. Start all
// confirmed — pipeline.plaud.match_people never guesses between two people
// sharing a name, so what it did suggest is already conservative; unchecking
// a false positive is the one tap that should matter, not building the list
// up from nothing.
function AttendeeChips({
  attendees,
  confirmed,
  onToggle,
}: {
  attendees: SuggestedAttendee[];
  confirmed: Set<string>;
  onToggle: (id: string) => void;
}) {
  if (attendees.length === 0) return null;
  return (
    <div className="mt-3">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
        Who was in this conversation?
      </p>
      <div className="mt-1.5 flex flex-wrap gap-2" role="group" aria-label="Confirm attendees">
        {attendees.map((a) => {
          const on = confirmed.has(a.id);
          return (
            <button
              key={a.id}
              type="button"
              aria-pressed={on}
              onClick={() => onToggle(a.id)}
              className={`min-h-11 rounded-full border px-4 text-sm font-semibold ${
                on
                  ? "bg-emphasis border-emphasis text-emphasis"
                  : "bg-subtle border-subtle text-subtle"
              }`}
            >
              {a.name}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ReviewCard({ item, isTop, onDecide, leaving }: CardProps) {
  const [confirmed, setConfirmed] = useState<Set<string>>(
    () => new Set(item.suggested_attendees.map((a) => a.id)),
  );
  const toggle = (id: string) =>
    setConfirmed((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const confirmedIds = () =>
    item.suggested_attendees.filter((a) => confirmed.has(a.id)).map((a) => a.id);

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
      {/* What the guess was made of. Null whenever the classifier didn't say
          (older notes, deterministic routes) — a missing reason is honest,
          an invented one wouldn't be. */}
      {item.evidence && <p className="text-subtle mt-1 text-sm">Why: {item.evidence}.</p>}

      <AttendeeChips attendees={item.suggested_attendees} confirmed={confirmed} onToggle={toggle} />

      <button
        type="button"
        onClick={() => onDecide(item, item.suggested_type, confirmedIds())}
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
              onClick={() => onDecide(item, t, confirmedIds())}
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

// Opt-in from Settings, off by default. A sitting has a length: five minutes,
// drawn as a shrinking tonal ring — no sound, no red, no countdown you have to
// beat. When it runs out the queue simply stops offering more.
function PieTimer({ onExpire }: { onExpire: () => void }) {
  // Both read once on mount: the preference can't change while this screen is
  // up, and the rest of this file checks reducedMotion() the same way.
  const [enabled] = useState(getTriageTimerEnabled);
  const [plain] = useState(reducedMotion);
  const [left, setLeft] = useState(TIMER_SECONDS);
  const tick = useRef<number | null>(null);
  const fired = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    tick.current = window.setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => {
      if (tick.current !== null) window.clearInterval(tick.current);
    };
  }, [enabled]);

  useEffect(() => {
    if (!enabled || left > 0 || fired.current) return;
    fired.current = true;
    if (tick.current !== null) window.clearInterval(tick.current);
    onExpire();
  }, [enabled, left, onExpire]);

  if (!enabled) return null;

  if (left === 0) {
    return <p className="text-subtle text-sm">Time's up — the rest keeps till next visit.</p>;
  }

  if (plain) {
    return (
      <p
        role="timer"
        aria-label="Triage timer"
        className="text-subtle text-sm font-semibold tabular-nums"
      >
        {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")}
      </p>
    );
  }

  // Hand-rolled, like every other icon here (no charting dependency): one
  // circumference of dashes, offset by however much of the sitting is spent.
  const r = 20;
  const circumference = 2 * Math.PI * r;
  return (
    <div role="timer" aria-label="Triage timer">
      <svg viewBox="0 0 48 48" className="h-8 w-8 -rotate-90" fill="none" aria-hidden="true">
        <circle
          className="text-muted"
          cx="24"
          cy="24"
          r={r}
          stroke="currentColor"
          strokeWidth="3"
        />
        <circle
          className="text-subtle"
          cx="24"
          cy="24"
          r={r}
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - left / TIMER_SECONDS)}
        />
      </svg>
    </div>
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
  // How many of the queue this sitting has asked for. Never reset by a poll —
  // a refetch mid-sitting must not collapse an expansion under the user.
  const [visibleCount, setVisibleCount] = useState(PAGE);
  const [expired, setExpired] = useState(false);
  // Optimistically decided ids — a refetch must not resurrect their cards
  // while the POST is still in flight.
  const decided = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (review.data) {
      // Oldest first: the note that has waited longest is the one to decide.
      setItems(
        review.data.items
          .filter((i) => !decided.current.has(i.id))
          .sort((a, b) => a.created.localeCompare(b.created)),
      );
    }
  }, [review.data]);

  const decide = async (item: ReviewItem, type: NoteType, attendeeIds: string[] = []) => {
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
      await api.approve(item.id, type, attendeeIds);
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

  // visibleCount outruns the queue as items are decided — the count on screen
  // and the [n more] label both work off what is actually left.
  const shown = Math.min(visibleCount, queue.length);
  const rest = queue.length - shown;
  const accuracy = review.data?.accuracy;

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        {/* A sentence, not a chart: how often the last batch of guesses stood
            as-is. Absent until there's enough history to say. */}
        {accuracy && (
          <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
            {accuracy.unchanged} of the last {accuracy.total} approvals needed no correction.
          </p>
        )}
        <PieTimer onExpire={() => setExpired(true)} />
        <p className="text-subtle text-sm font-semibold">
          {queue.length > PAGE
            ? `Showing ${shown} of ${queue.length} — one decision each.`
            : `${queue.length} capture${queue.length === 1 ? "" : "s"} to triage — one decision each.`}
        </p>
      </div>
      {queue.slice(0, visibleCount).map((item, i) => (
        <ReviewCard
          key={item.id}
          item={item}
          isTop={i === 0}
          onDecide={decide}
          leaving={leaving.has(item.id)}
        />
      ))}
      {/* Asked for, never scrolled into. Gone for good once the timer runs
          out — the rest of the queue is next visit's problem. */}
      {rest > 0 && !expired && (
        <button
          type="button"
          onClick={() => setVisibleCount((n) => n + PAGE)}
          className="border-subtle text-subtle hover:border-emphasis hover:text-emphasis min-h-11 w-full rounded-xl border text-sm font-bold"
        >
          {Math.min(PAGE, rest)} more
        </button>
      )}
    </div>
  );
}
