import { useEffect, useRef, useState } from "react";
import { api, getTriageTimerEnabled, trustLine } from "../api/client";
import type {
  NoteType,
  PersonProposal,
  ReviewItem,
  ReviewTrust,
  SplitProposal,
  SuggestedAttendee,
} from "../api/types";
import { NOTE_TYPES, PROPOSAL_TOPICS } from "../api/types";
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
      {item.related_title && (
        <p className="text-subtle mt-1 text-sm">Past-you thought this too: {item.related_title}.</p>
      )}

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

// A split proposal tracks a DIFFERENT population than the review queue above
// — an already-filed journal/musing note, not an inbox item — so it renders
// as its own section, always tonal (Triage's one accent stays on the top
// review card's Approve button).
function SplitProposalCard({
  proposal,
  onDecide,
}: {
  proposal: SplitProposal;
  onDecide: (id: string, decision: "keep" | "split") => void;
}) {
  const [busy, setBusy] = useState(false);
  const decide = async (decision: "keep" | "split") => {
    if (busy) return;
    setBusy(true);
    try {
      await api.reviewSplitDecision(proposal.id, decision);
      onDecide(proposal.id, decision);
      toast(decision === "split" ? "Split into separate notes." : "Kept as one.");
    } catch (err) {
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(envelope ? `${envelope.what} ${envelope.todo}` : "That didn't reach the server.", "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className="bg-subtle border-subtle rounded-xl border p-5">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
        {proposal.title}
      </p>
      <h3 className="font-cal text-emphasis mt-2 text-xl font-bold leading-tight -tracking-[0.01em]">
        This sounds like {proposal.segment_titles.length} topics — split it?
      </h3>
      <ul className="text-default mt-2 space-y-1 text-sm">
        {proposal.segment_titles.map((t, i) => (
          <li key={i}>• {t}</li>
        ))}
      </ul>
      <div className="mt-4 flex gap-2">
        <button type="button" disabled={busy} onClick={() => decide("keep")}
          className="border-subtle text-subtle hover:border-emphasis min-h-11 flex-1 rounded-xl border text-sm font-bold disabled:opacity-60">
          Keep as one
        </button>
        <button type="button" disabled={busy} onClick={() => decide("split")}
          className="border-subtle text-subtle hover:border-emphasis min-h-11 flex-1 rounded-xl border text-sm font-bold disabled:opacity-60">
          Split
        </button>
      </div>
    </article>
  );
}

const PROPOSAL_LABEL: Record<PersonProposal["type"], string> = {
  fact: "They said",
  interpretation: "My reading — not something they said",
  commitment_mine: "I promised",
  commitment_theirs: "They promised",
  follow_up: "Follow up",
  personal_detail: "About them",
  upcoming: "Coming up for them",
  milestone: "Milestone",
  need: "They're looking for",
  company_knowledge: "About their company",
  person_update: "Update",
  problem: "Problem",
  goal: "Goal",
  offer: "Can help with",
  intro: "Intro",
  give_mine: "I gave",
  give_theirs: "They gave",
  important_date: "Date",
  reputation_signal: "What they say about me",
};

const DATED: PersonProposal["type"][] = [
  "upcoming", "commitment_theirs", "commitment_mine", "follow_up", "intro", "important_date",
];

function shortDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function plusDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function plusOneDay(iso: string): string {
  return plusDays(iso, 1);
}

// What Remember will do, said before the tap ("→ Ask about on 13 Oct").
function outcomeSentence(p: PersonProposal, date: string, topic: string): string {
  if (p.type === "give_mine") return "→ Log · counts as a give";
  if (p.type === "reputation_signal") return "→ Reputation page · log";
  if (p.type === "give_theirs") {
    const reportDue = p.due ? shortDate(plusDays(p.due, 30)) : "";
    return `→ Log · thank today · report outcome ${reportDue}`;
  }
  if (p.type === "intro") {
    const due = date ? plusDays(date, 14) : p.due;
    return due ? `→ Log · check in on ${shortDate(due)}` : "→ Log · check in";
  }
  if (p.section !== "Next action") {
    if (p.type === "personal_detail") return `→ About them${topic ? ` · ${topic}` : ""}`;
    if (p.type === "company_knowledge") return "→ Their company's facts";
    return `→ ${p.section}`;
  }
  const due = p.type === "upcoming" && date ? plusOneDay(date)
    : p.type === "commitment_theirs" ? (date ? plusOneDay(date) : null)
    : p.type === "milestone" ? p.due
    : p.type === "important_date" ? (date ? plusDays(date, -7) : p.due)
    : (date || p.due);
  const verb = p.type === "upcoming" ? "Ask about" : p.type === "milestone" ? "Congratulate"
    : p.type === "commitment_theirs" ? "Check in" : p.type === "important_date" ? "Remind"
    : "Follow up";
  return due ? `→ ${verb} on ${shortDate(due)}` : `→ ${verb} — no date, stays open`;
}

// Task 12: a muted flag for a proposal that touches sensitive ground — the
// owner's health topic, or a capture that opted in with #sensitive — so the
// reviewer knows before they read the line, without the chip itself reading
// as alarming (it's a note to be careful, not a warning).
function isSensitiveProposal(proposal: PersonProposal): boolean {
  return proposal.topic === "health" || proposal.text.includes("#sensitive");
}

function SensitiveChip() {
  return (
    <span className="bg-cal-muted text-subtle mt-2 inline-block rounded-full px-2.5 py-0.5 text-[11px] font-bold uppercase tracking-[0.08em]">
      sensitive
    </span>
  );
}

// A proposal is a decision about memory, not a sentence to save: the card
// says where it lands and when it comes back, with the date/topic editable
// before Remember. Tonal like the split card — the one accent stays on the
// review queue's top Approve button.
function PersonProposalCard({
  proposal,
  onDecide,
}: {
  proposal: PersonProposal;
  onDecide: (id: number) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [date, setDate] = useState(proposal.date ?? "");
  const [topic, setTopic] = useState(proposal.topic);
  const decide = async (decision: "remember" | "skip") => {
    if (busy) return;
    setBusy(true);
    try {
      const edits: { date?: string; topic?: string } = {};
      if (date && date !== proposal.date) edits.date = date;
      if (proposal.type === "personal_detail") edits.topic = topic;
      await api.reviewPersonProposal(proposal.id, decision, edits);
      onDecide(proposal.id);
      toast(decision === "remember" ? `Remembered for ${proposal.person_name}.` : "Skipped.");
    } catch (err) {
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(envelope ? `${envelope.what} ${envelope.todo}` : "That didn't reach the server.", "error");
    } finally {
      setBusy(false);
    }
  };
  const interpretation = proposal.type === "interpretation";
  return (
    <article className="bg-subtle border-subtle rounded-xl border p-5">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
        {proposal.person_name} · {PROPOSAL_LABEL[proposal.type]}
      </p>
      <h3
        className={`font-cal text-emphasis mt-2 text-xl font-bold leading-tight -tracking-[0.01em] ${
          interpretation ? "italic" : ""
        }`}
      >
        {proposal.text}
      </h3>
      {isSensitiveProposal(proposal) && <SensitiveChip />}
      <p className="text-default mt-2 text-sm font-semibold">{outcomeSentence(proposal, date, topic)}</p>
      <p className="text-subtle mt-1 text-sm">From “{proposal.note_title}” · AI-suggested</p>

      {DATED.includes(proposal.type) && (
        <label className="text-subtle mt-3 block text-sm">
          <span className="text-[11px] font-bold uppercase tracking-[0.08em]">Date</span>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="bg-default border-subtle text-default mt-1 block min-h-11 w-full rounded-lg border px-3"
          />
        </label>
      )}
      {proposal.type === "personal_detail" && (
        <label className="text-subtle mt-3 block text-sm">
          <span className="text-[11px] font-bold uppercase tracking-[0.08em]">Topic</span>
          <select
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            className="bg-default border-subtle text-default mt-1 block min-h-11 w-full rounded-lg border px-3"
          >
            <option value="">No topic</option>
            {PROPOSAL_TOPICS.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </label>
      )}

      <div className="mt-4 flex gap-2">
        <button type="button" disabled={busy} onClick={() => decide("skip")}
          className="border-subtle text-subtle hover:border-emphasis min-h-11 flex-1 rounded-xl border text-sm font-bold disabled:opacity-60">
          Skip
        </button>
        <button type="button" disabled={busy || (proposal.type === "upcoming" && !date)}
          onClick={() => decide("remember")}
          className="bg-inverted text-inverted min-h-11 flex-1 rounded-xl text-sm font-bold disabled:opacity-60">
          Remember
        </button>
      </div>
    </article>
  );
}

function EmptyState({ trust }: { trust: ReviewTrust | undefined }) {
  const streak = usePolling(api.streak);
  return (
    <div className="pt-8">
      <h2 className="font-cal text-emphasis text-5xl font-extrabold leading-[0.95] -tracking-[0.02em]">
        Inbox zero.
      </h2>
      <p className="text-default mt-3 text-base">Nothing needs you.</p>
      {trust && <p className="text-subtle mt-2 text-sm">{trustLine(trust)}</p>}
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

  // Split proposals track a DIFFERENT population (already-filed notes, not
  // the review queue) — same optimistic-removal shape as `items`/`decided`
  // above, kept separate so a decided proposal disappears immediately
  // without waiting for the next poll, regardless of queue state.
  const [proposals, setProposals] = useState<SplitProposal[] | null>(null);
  const decidedProposals = useRef<Set<string>>(new Set());
  const [memory, setMemory] = useState<PersonProposal[]>([]);
  const decidedMemory = useRef<Set<number>>(new Set());

  useEffect(() => {
    if (review.data) {
      // Oldest first: the note that has waited longest is the one to decide.
      setItems(
        review.data.items
          .filter((i) => !decided.current.has(i.id))
          .sort((a, b) => a.created.localeCompare(b.created)),
      );
      setProposals(
        review.data.split_proposals.filter((p) => !decidedProposals.current.has(p.id)),
      );
      setMemory(
        (review.data.person_proposals ?? []).filter((p) => !decidedMemory.current.has(p.id)),
      );
    }
  }, [review.data]);

  const decideProposal = (id: string, _decision: "keep" | "split") => {
    decidedProposals.current.add(id);
    setProposals((cur) => (cur ? cur.filter((p) => p.id !== id) : cur));
  };

  const decideMemory = (id: number) => {
    decidedMemory.current.add(id);
    setMemory((cur) => cur.filter((p) => p.id !== id));
  };

  const memoryCards = memory.length > 0 && (
    <section className="space-y-4">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
        Worth remembering about people · {memory.length}
      </p>
      {memory.slice(0, PAGE).map((p) => (
        <PersonProposalCard key={p.id} proposal={p} onDecide={decideMemory} />
      ))}
    </section>
  );

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
  const splitProposals = proposals ?? [];
  // Split proposals track an already-filed note, a completely different
  // population from the needs-review queue above — so they render below the
  // queue's own empty state too, not only when there's something to triage.
  if (queue.length === 0) {
    return (
      <div className="space-y-4">
        <EmptyState trust={review.data?.trust} />
        {memoryCards}
        {splitProposals.map((p) => (
          <SplitProposalCard key={p.id} proposal={p} onDecide={decideProposal} />
        ))}
      </div>
    );
  }

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
      {memoryCards}
      {splitProposals.map((p) => (
        <SplitProposalCard key={p.id} proposal={p} onDecide={decideProposal} />
      ))}
    </div>
  );
}
