// The Relationship OS surface. The screen's ONE accent-lit element is the
// counter on the person who is furthest past their cadence — the single "call
// this one today", not a wall of teal (DESIGNSYSTEM.md §2).
//
// CLAUDE.md §4: nothing here sends. The API hands back the person's raw phone
// number / email; the deep links below are built in the browser and opened by
// a human tap. There is no send button anywhere in this file.
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type {
  ContactResult,
  Person,
  PersonDetail,
  PersonDraft,
  PushAvailability,
  PushPreview,
  PushQueueItem,
  PushTarget,
  WarmthStage,
} from "../api/types";
import { WARMTH_STAGES } from "../api/types";
import { ErrorState } from "../components/ErrorState";
import { toast } from "../components/Toast";
import { usePolling } from "../hooks/usePolling";

type Filter = "attention" | "warmup" | "all";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "attention", label: "Needs you" },
  { key: "warmup", label: "Warm-up due" },
  { key: "all", label: "Everyone" },
];

function matches(person: Person, filter: Filter): boolean {
  if (filter === "all") return true;
  if (filter === "warmup") return person.warmup_due;
  return person.going_cold || person.warmup_due || person.commitment_due;
}

function quietLabel(person: Person): string {
  const days = person.days_since_contact;
  if (days === null) return "Never";
  if (days === 0) return "Today";
  return `${days}d`;
}

function reason(person: Person): string {
  if (person.commitment_due) return "You owe them a step";
  if (person.going_cold) return `Past a ${person.cadence_days}-day cadence`;
  if (person.warmup_due) return `Warm-up due · ${person.warmth_stage}`;
  return person.warmth_stage ? `Stage · ${person.warmth_stage}` : "In touch";
}

// Deep links are assembled HERE, in the browser, and opened by a human tap.
function channelLink(channel: string, value: string, text: string): string | null {
  const encoded = encodeURIComponent(text);
  if (channel === "whatsapp") {
    const digits = value.replace(/[^\d]/g, "");
    return `https://${"wa"}.me/${digits}?text=${encoded}`;
  }
  if (channel === "email") return `mailto:${value}?body=${encoded}`;
  if (channel === "linkedin") {
    return value.startsWith("http") ? value : `https://www.linkedin.com/in/${value}`;
  }
  return null;
}

function channelLabel(channel: string): string {
  if (channel === "whatsapp") return "Open WhatsApp";
  if (channel === "email") return "Open mail draft";
  if (channel === "linkedin") return "Open LinkedIn";
  return "Open";
}

// Pass D — pushing a profile summary OUT to the owner's own CRM / address book.
// This is not a send: no message reaches another person, and the human reads
// the exact text before anything leaves (CLAUDE.md §3, §4).
const PUSH_LABELS: Record<PushTarget, string> = {
  dex: "Push to Dex",
  contacts: "Push to Contacts",
};

function pushUnavailableReason(
  target: PushTarget,
  person: Person,
  available: PushAvailability | null,
): string | null {
  if (!available) return null;
  if (target === "dex") {
    if (!available.dex) return "Dex not configured";
    if (!person.dex_id) return "No dex_id on the note";
    return null;
  }
  if (!available.contacts_scope) return "Reconnect Google for contacts";
  if (!person.channels.email && !person.channels.whatsapp) return "No email or phone on the note";
  return null;
}

function PushRow({
  person,
  available,
  onPushed,
}: {
  person: Person;
  available: PushAvailability | null;
  onPushed: () => void;
}) {
  const [preview, setPreview] = useState<PushPreview | null>(null);
  const [busy, setBusy] = useState<PushTarget | null>(null);
  const [confirming, setConfirming] = useState(false);

  const envelopeOf = (err: unknown) =>
    (err as { envelope?: { what: string; cause: string; todo: string } }).envelope;

  const startPreview = async (target: PushTarget) => {
    setBusy(target);
    setPreview(null);
    try {
      setPreview(await api.pushPreview(person.id, target));
    } catch (err) {
      const envelope = envelopeOf(err);
      toast(
        envelope ? `${envelope.what} ${envelope.todo}` : "Couldn't prepare that push.",
        "error",
      );
    } finally {
      setBusy(null);
    }
  };

  const confirm = async () => {
    if (!preview) return;
    setConfirming(true);
    try {
      // the CONFIRMED text goes back — what was read is what is written
      const result = await api.push(person.id, preview.target, preview.summary);
      toast(`✅ Updated ${result.changed}`);
      setPreview(null);
      onPushed();
    } catch (err) {
      const envelope = envelopeOf(err);
      toast(envelope ? `${envelope.what} ${envelope.todo}` : "The push didn't go through.", "error");
    } finally {
      setConfirming(false);
    }
  };

  return (
    <div className="border-subtle mt-5 border-t pt-4">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
        Profile · push out
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        {(Object.keys(PUSH_LABELS) as PushTarget[]).map((target) => {
          const blockedReason = pushUnavailableReason(target, person, available);
          if (blockedReason) {
            return (
              <span
                key={target}
                className="bg-subtle text-muted border-subtle inline-flex min-h-11 items-center rounded-full border px-4 text-xs font-semibold"
              >
                {blockedReason}
              </span>
            );
          }
          return (
            <button
              key={target}
              type="button"
              disabled={busy !== null}
              onClick={() => void startPreview(target)}
              className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold disabled:opacity-60"
            >
              {busy === target ? "Preparing…" : PUSH_LABELS[target]}
            </button>
          );
        })}
      </div>

      {preview && (
        <div className="bg-cal-muted border-emphasis mt-3 rounded-xl border p-4">
          <p className="text-emphasis text-sm font-bold">Read it before it goes</p>
          <p className="text-subtle mt-1 text-sm">
            This replaces only the Brain OS section of {preview.destination}. Anything you
            typed there yourself is left exactly as it is.
          </p>
          <pre className="text-default bg-subtle border-subtle mt-3 max-h-56 overflow-y-auto whitespace-pre-wrap rounded-xl border p-3 text-sm">
            {preview.block}
          </pre>
          {preview.replaced && (
            <details className="mt-2">
              <summary className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
                What it replaces
              </summary>
              <pre className="text-muted mt-2 whitespace-pre-wrap text-sm">{preview.replaced}</pre>
            </details>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={confirming}
              onClick={() => void confirm()}
              className="bg-inverted text-inverted min-h-11 rounded-xl px-5 text-sm font-bold disabled:opacity-60"
            >
              {confirming ? "Pushing…" : "Confirm push"}
            </button>
            <button
              type="button"
              onClick={() => setPreview(null)}
              className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function PersonCard({
  person,
  lit,
  onOpen,
}: {
  person: Person;
  lit: boolean;
  onOpen: () => void;
}) {
  return (
    <article className="bg-default border-subtle rounded-xl border p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
            {person.relationship || "Person"}
            {person.company ? ` · ${person.company}` : ""}
          </p>
          <h3 className="font-cal text-emphasis mt-1 text-xl font-bold leading-tight -tracking-[0.01em]">
            {person.name}
          </h3>
          <p className="text-default mt-1 text-sm">{reason(person)}</p>
          {person.next_action && (
            <p className="text-subtle mt-1 truncate text-sm">Next: {person.next_action}</p>
          )}
        </div>
        <div className="shrink-0 text-right">
          <p
            className={`font-cal text-4xl font-extrabold leading-[0.9] -tracking-[0.04em] ${
              lit ? "text-brand-default" : "text-emphasis"
            }`}
          >
            {quietLabel(person)}
          </p>
          <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">quiet</p>
        </div>
      </div>
      <button
        type="button"
        onClick={onOpen}
        className="border-emphasis text-emphasis mt-4 min-h-11 w-full rounded-xl border text-sm font-bold"
      >
        Draft a message
      </button>
    </article>
  );
}

function DraftDrawer({
  person,
  onClose,
  onChanged,
  pushAvailable,
  onPushed,
}: {
  person: Person;
  onClose: () => void;
  onChanged: (updated: Person) => void;
  pushAvailable: PushAvailability | null;
  onPushed: () => void;
}) {
  const [detail, setDetail] = useState<PersonDetail | null>(null);
  const [draft, setDraft] = useState<PersonDraft | null>(null);
  const [text, setText] = useState("");
  const [channel, setChannel] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [blocked, setBlocked] = useState<{ what: string; cause: string; todo: string } | null>(null);
  const [suggest, setSuggest] = useState<WarmthStage | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let live = true;
    void api.person(person.id).then((d) => live && setDetail(d)).catch(() => {});
    void api
      .personDraft(person.id)
      .then((d) => {
        if (!live) return;
        setDraft(d);
        setText(d.text);
        setChannel(d.channel);
      })
      .catch((err) => {
        const envelope = (err as { envelope?: { what: string; cause: string; todo: string } })
          .envelope;
        if (live && envelope) setBlocked(envelope);
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [person.id]);

  const channels = draft?.channels ?? person.channels;
  const value = channels[channel as keyof typeof channels];
  const link = value ? channelLink(channel, value, text) : null;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast("✅ Copied");
    } catch {
      toast("Couldn't copy — select the text and copy it by hand.", "error");
    }
  };

  const logContact = async () => {
    try {
      const updated: ContactResult = await api.logContact(person.id, text.slice(0, 120), channel);
      onChanged(updated);
      toast("✅ Logged");
      setSuggest(updated.suggest_stage);
      if (!updated.suggest_stage) onClose();
    } catch (err) {
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(envelope ? `${envelope.what} ${envelope.todo}` : "Couldn't log that.", "error");
    }
  };

  const advance = async (stage: WarmthStage) => {
    try {
      onChanged(await api.setWarmth(person.id, stage));
      toast(`✅ ${stage}`);
    } catch {
      toast("Couldn't change the stage.", "error");
    } finally {
      onClose();
    }
  };

  return (
    <div
      className="fixed inset-0 z-40 flex items-end justify-center sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-label={`Draft a message to ${person.name}`}
    >
      <button aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/50" />
      <div className="bg-default border-subtle relative max-h-[92dvh] w-full max-w-lg overflow-y-auto rounded-t-2xl border p-5 sm:rounded-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
              {quietLabel(person)} quiet · {reason(person)}
            </p>
            <h2 className="font-cal text-emphasis mt-1 text-2xl font-extrabold -tracking-[0.02em]">
              {person.name}
            </h2>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="border-subtle text-default flex h-11 w-11 shrink-0 items-center justify-center rounded-full border"
          >
            ✕
          </button>
        </div>

        {loading && (
          <div aria-hidden="true" className="bg-subtle mt-4 h-32 animate-pulse rounded-xl" />
        )}

        {blocked && (
          <div className="border-subtle mt-4 rounded-xl border p-4">
            <p className="text-emphasis text-sm font-bold">{blocked.what}</p>
            <p className="text-subtle mt-1 text-sm">{blocked.cause}</p>
            <p className="text-default mt-1 text-sm">{blocked.todo}</p>
            <a
              href="#/settings"
              className="border-emphasis text-emphasis mt-3 inline-flex min-h-11 items-center rounded-xl border px-4 text-sm font-bold"
            >
              Open Settings
            </a>
          </div>
        )}

        {draft && (
          <>
            {Object.keys(channels).length > 1 && (
              <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="Channel">
                {Object.keys(channels).map((c) => (
                  <button
                    key={c}
                    type="button"
                    aria-pressed={channel === c}
                    onClick={() => setChannel(c)}
                    className={`min-h-11 rounded-full border px-4 text-sm font-semibold ${
                      channel === c
                        ? "bg-emphasis border-emphasis text-emphasis"
                        : "bg-subtle border-subtle text-default"
                    }`}
                  >
                    {c}
                  </button>
                ))}
              </div>
            )}

            <label className="text-subtle mt-4 block text-[11px] font-bold uppercase tracking-[0.08em]">
              Draft — yours to edit
            </label>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={6}
              aria-label="Draft message"
              className="bg-subtle border-subtle text-emphasis mt-2 w-full rounded-xl border p-3 text-base"
            />

            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void copy()}
                className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold"
              >
                Copy
              </button>
              {link && (
                <a
                  href={link}
                  target="_blank"
                  rel="noreferrer"
                  className="bg-inverted text-inverted min-h-11 rounded-xl px-5 text-sm font-bold leading-[2.75rem]"
                >
                  {channelLabel(channel)}
                </a>
              )}
              <button
                type="button"
                onClick={() => void logContact()}
                className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold"
              >
                Log contact
              </button>
            </div>
            <p className="text-muted mt-2 text-[11px]">
              Nothing is sent from here — the button opens the app with the draft in it.
            </p>

            {suggest && (
              <div className="border-subtle mt-4 rounded-xl border p-4">
                <p className="text-emphasis text-sm font-bold">
                  Logged. Move them to “{suggest}”?
                </p>
                <div className="mt-3 flex gap-2">
                  <button
                    type="button"
                    onClick={() => void advance(suggest)}
                    className="bg-inverted text-inverted min-h-11 rounded-xl px-5 text-sm font-bold"
                  >
                    Yes, {suggest}
                  </button>
                  <button
                    type="button"
                    onClick={onClose}
                    className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold"
                  >
                    Not yet
                  </button>
                </div>
              </div>
            )}
          </>
        )}

        <PushRow person={person} available={pushAvailable} onPushed={onPushed} />

        {detail?.interaction_log && (
          <details className="mt-5">
            <summary className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
              Interaction log
            </summary>
            <pre className="text-default mt-2 whitespace-pre-wrap text-sm">
              {detail.interaction_log}
            </pre>
          </details>
        )}
      </div>
    </div>
  );
}

function StageRow({ person, onChanged }: { person: Person; onChanged: (p: Person) => void }) {
  return (
    <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Warmth stage">
      {WARMTH_STAGES.map((stage) => (
        <button
          key={stage}
          type="button"
          aria-pressed={person.warmth_stage === stage}
          onClick={async () => {
            try {
              onChanged(await api.setWarmth(person.id, stage));
            } catch {
              toast("Couldn't change the stage.", "error");
            }
          }}
          className={`min-h-11 rounded-full border px-3 text-xs font-semibold ${
            person.warmth_stage === stage
              ? "bg-emphasis border-emphasis text-emphasis"
              : "bg-subtle border-subtle text-default"
          }`}
        >
          {stage}
        </button>
      ))}
    </div>
  );
}

// The staged half of the nightly batch: the server works out who has moved on
// since their last push, and this lists them. Nothing pushes itself — each row
// opens the same preview → confirm as the drawer (CLAUDE.md §3).
function PushQueue({
  items,
  onOpen,
}: {
  items: PushQueueItem[];
  onOpen: (person: PushQueueItem) => void;
}) {
  if (items.length === 0) return null;
  return (
    <section className="border-subtle rounded-xl border p-4">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
        Ready to push · {items.length}
      </p>
      <p className="text-default mt-1 text-sm">
        These have moved on since their profile last went out. Each one still shows you the
        text before anything is written.
      </p>
      <ul className="mt-3 space-y-2">
        {items.map((item) => (
          <li key={item.id} className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-emphasis text-sm font-semibold">
              {item.name}
              <span className="text-subtle font-normal"> · {item.targets.join(" + ")}</span>
            </span>
            <button
              type="button"
              onClick={() => onOpen(item)}
              className="border-emphasis text-emphasis min-h-11 rounded-xl border px-4 text-sm font-bold"
            >
              Review
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function People() {
  const { data, error, loading, refetch } = usePolling(api.people, 60_000);
  const [filter, setFilter] = useState<Filter>("attention");
  const [open, setOpen] = useState<Person | null>(null);
  const [overrides, setOverrides] = useState<Record<string, Person>>({});
  const [pushAvailable, setPushAvailable] = useState<PushAvailability | null>(null);
  const [queue, setQueue] = useState<PushQueueItem[]>([]);

  const loadQueue = () => {
    void api
      .pushQueue()
      .then((q) => {
        setQueue(q.items);
        setPushAvailable(q.available);
      })
      // a cockpit with no push target configured is a normal state, not an error
      .catch(() => setPushAvailable({ dex: false, contacts_scope: false }));
  };

  useEffect(loadQueue, []);

  if (loading && !data) {
    return <div aria-hidden="true" className="bg-subtle h-40 animate-pulse rounded-xl" />;
  }
  if (error && !data) {
    return <ErrorState envelope={error.envelope} detail={error.detail} onRetry={refetch} />;
  }

  const people = (data?.items ?? []).map((p) => overrides[p.id] ?? p);
  const shown = people.filter((p) => matches(p, filter));
  const record = (updated: Person) => setOverrides((o) => ({ ...o, [updated.id]: updated }));

  return (
    <div className="space-y-6">
      <section>
        <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">People</p>
        <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Filter">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              aria-pressed={filter === f.key}
              onClick={() => setFilter(f.key)}
              className={`min-h-11 rounded-full border px-4 text-sm font-semibold ${
                filter === f.key
                  ? "bg-emphasis border-emphasis text-emphasis"
                  : "bg-subtle border-subtle text-default"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </section>

      <PushQueue items={queue} onOpen={(item) => setOpen(item)} />

      {shown.length === 0 ? (
        <section className="bg-subtle border-subtle rounded-xl border p-5">
          <h2 className="font-cal text-emphasis text-2xl font-extrabold -tracking-[0.02em]">
            {people.length === 0 ? "No people yet" : "Nobody's slipping"}
          </h2>
          <p className="text-default mt-2 text-sm">
            {people.length === 0
              ? "Notes of type person in 07-People show up here, ranked by who has gone quiet longest."
              : "Everyone is inside their cadence. This screen stays quiet until someone isn't."}
          </p>
        </section>
      ) : (
        <div className="space-y-4">
          {shown.map((person, i) => (
            <div key={person.id}>
              <PersonCard
                person={person}
                lit={i === 0 && filter !== "all"}
                onOpen={() => setOpen(person)}
              />
              <StageRow person={person} onChanged={record} />
            </div>
          ))}
        </div>
      )}

      {open && (
        <DraftDrawer
          person={overrides[open.id] ?? open}
          onClose={() => setOpen(null)}
          onChanged={record}
          pushAvailable={pushAvailable}
          onPushed={loadQueue}
        />
      )}
    </div>
  );
}
