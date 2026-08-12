/**
 * People — the relationship OS surface.
 *
 * The screen is built around one question: who is about to slip? Going-cold
 * people come first and carry the screen's SINGLE accent (the nudge card at the
 * top). Everything else — group headers, cards, the warmth ladder, the day
 * counts — stays tonal. Marking a relationship "cold" with the accent color
 * would light up half the screen and turn the accent into decoration.
 *
 * Dormant and not-set-up notes collapse into <details>, the house disclosure:
 * they're real, they're reachable, and they're not what you opened this for.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { WARMTH_STAGES } from "../api/types";
import type { ErrorEnvelope, Person, PersonDetail, WarmthStage } from "../api/types";
import { ErrorState } from "../components/ErrorState";
import { toast } from "../components/Toast";
import { usePolling } from "../hooks/usePolling";

function toastError(err: unknown, fallback: string) {
  const envelope = (err as { envelope?: ErrorEnvelope }).envelope;
  toast(envelope ? `${envelope.what} ${envelope.todo}` : fallback, "error");
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

/** "12 days since contact" — or the setup prompt when there's nothing to count. */
function contactLine(person: Person): string {
  if (person.unset || person.days_since_contact === null) {
    return "No cadence set yet";
  }
  const n = person.days_since_contact;
  const since = n === 0 ? "Contacted today" : `${n} day${n === 1 ? "" : "s"} since contact`;
  if (person.days_overdue && person.days_overdue > 0) {
    return `${since} · ${person.days_overdue} past your cadence`;
  }
  return since;
}

function personDeepLink(vault: string | undefined, file: string): string | null {
  if (!vault) return null;
  return `obsidian://open?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(
    file.replace(/\.md$/, ""),
  )}`;
}

function PersonCard({
  person,
  busy,
  onLogContact,
  onOpen,
}: {
  person: Person;
  busy: boolean;
  onLogContact: (person: Person) => void;
  onOpen: (person: Person) => void;
}) {
  return (
    <li className="bg-subtle border-subtle flex items-center gap-3 rounded-xl border p-3">
      <button
        type="button"
        onClick={() => onOpen(person)}
        className="flex min-w-0 flex-1 items-center gap-3 text-left"
      >
        <span
          aria-hidden="true"
          className="bg-emphasis text-emphasis font-cal flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-sm font-extrabold"
        >
          {initials(person.name)}
        </span>
        <span className="min-w-0 flex-1">
          <span className="text-emphasis block truncate text-sm font-bold">{person.name}</span>
          <span className="text-subtle block truncate text-xs">
            {contactLine(person)}
            {person.company ? ` · ${person.company}` : ""}
          </span>
        </span>
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() => onLogContact(person)}
        className="border-emphasis text-emphasis min-h-11 shrink-0 rounded-xl border px-3 text-xs font-bold disabled:opacity-50"
      >
        {busy ? "Logging…" : "Log contact"}
      </button>
    </li>
  );
}

function Group({
  label,
  people,
  busyId,
  onLogContact,
  onOpen,
  collapsed = false,
  note,
}: {
  label: string;
  people: Person[];
  busyId: string | null;
  onLogContact: (person: Person) => void;
  onOpen: (person: Person) => void;
  collapsed?: boolean;
  note?: string;
}) {
  if (people.length === 0) return null;
  const list = (
    <ul className="mt-3 space-y-2">
      {people.map((person) => (
        <PersonCard
          key={person.id}
          person={person}
          busy={busyId === person.id}
          onLogContact={onLogContact}
          onOpen={onOpen}
        />
      ))}
    </ul>
  );
  if (collapsed) {
    return (
      <details className="mt-6">
        <summary className="text-subtle min-h-11 cursor-pointer list-none py-2 text-sm font-semibold">
          {label} ({people.length})
        </summary>
        {note && <p className="text-muted mt-1 text-xs">{note}</p>}
        {list}
      </details>
    );
  }
  return (
    <section className="mt-6">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
        {label} ({people.length})
      </p>
      {note && <p className="text-muted mt-1 text-xs">{note}</p>}
      {list}
    </section>
  );
}

function Drawer({
  person,
  vault,
  onClose,
  onChanged,
}: {
  person: Person;
  vault: string | undefined;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<PersonDetail | null>(null);
  const [cadence, setCadence] = useState(String(person.cadence_days ?? ""));
  const [saving, setSaving] = useState(false);
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  useEffect(() => {
    let alive = true;
    // The summary already renders; the body sections are best-effort extras.
    api
      .person(person.id)
      .then((d) => {
        if (alive) {
          setDetail(d);
          setCadence(String(d.cadence_days ?? ""));
        }
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [person.id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const save = async (changes: { cadence_days?: number; warmth_stage?: WarmthStage }) => {
    setSaving(true);
    try {
      await api.patchPerson(person.id, changes);
      const fresh = await api.person(person.id);
      setDetail(fresh);
      setCadence(String(fresh.cadence_days ?? ""));
      onChanged();
      toast("Saved.");
    } catch (err) {
      toastError(err, "That change didn't reach the server.");
    } finally {
      setSaving(false);
    }
  };

  const saveCadence = () => {
    const n = Number(cadence);
    if (!Number.isInteger(n) || n <= 0) {
      toast("A cadence is a whole number of days above zero.", "error");
      return;
    }
    if (n !== person.cadence_days) void save({ cadence_days: n });
  };

  const deepLink = personDeepLink(vault, person.file);
  const sections = detail?.sections ?? {};
  const warmth = (detail?.warmth_stage || person.warmth_stage) as string;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={person.name}
      className="fixed inset-0 z-40 flex items-end justify-center sm:items-center"
    >
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-black/50"
      />
      <div className="bg-default border-subtle relative max-h-[92dvh] w-full max-w-lg overflow-y-auto rounded-t-2xl border p-5 sm:rounded-2xl">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
              {person.relationship || "Person"}
              {person.company ? ` · ${person.company}` : ""}
            </p>
            <h2 className="font-cal text-emphasis mt-1 text-2xl font-extrabold leading-[0.95] -tracking-[0.02em]">
              {person.name}
            </h2>
            <p className="text-subtle mt-1 text-sm">{contactLine(person)}</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="border-subtle text-subtle hover:border-emphasis flex h-11 w-11 shrink-0 items-center justify-center rounded-full border"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        {person.unset && (
          <div className="bg-cal-muted border-emphasis mt-4 rounded-xl border p-4">
            <p className="text-emphasis text-sm font-bold">Set this person up</p>
            <p className="text-default mt-1.5 text-sm">
              This note was written before cadences existed, so there's nothing to count
              from yet. Give it a cadence and log a contact — after that it tracks itself.
            </p>
          </div>
        )}

        <div className="mt-5">
          <label
            htmlFor="cadence"
            className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]"
          >
            Cadence
          </label>
          <div className="mt-2 flex items-center gap-2">
            <input
              id="cadence"
              type="number"
              min={1}
              inputMode="numeric"
              value={cadence}
              onChange={(e) => setCadence(e.target.value)}
              className="bg-subtle border-subtle text-emphasis min-h-11 w-24 rounded-xl border px-3 text-sm"
            />
            <span className="text-subtle text-sm">days between contact</span>
            <button
              type="button"
              disabled={saving}
              onClick={saveCadence}
              className="border-emphasis text-emphasis ml-auto min-h-11 rounded-xl border px-4 text-sm font-bold disabled:opacity-50"
            >
              Save
            </button>
          </div>
        </div>

        <div className="mt-5">
          <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
            Warmth
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {WARMTH_STAGES.map((stage) => {
              const active = warmth === stage;
              return (
                <button
                  key={stage}
                  type="button"
                  aria-pressed={active}
                  disabled={saving}
                  onClick={() => !active && void save({ warmth_stage: stage })}
                  className={`min-h-11 rounded-full border px-4 text-sm font-semibold capitalize disabled:opacity-50 ${
                    active
                      ? "bg-emphasis border-emphasis text-emphasis"
                      : "border-subtle text-subtle"
                  }`}
                >
                  {stage}
                </button>
              );
            })}
          </div>
        </div>

        {["Context", "Needs", "Next action"].map((heading) =>
          sections[heading] ? (
            <div key={heading} className="mt-5">
              <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
                {heading}
              </p>
              <p className="text-default mt-2 whitespace-pre-line text-sm">{sections[heading]}</p>
            </div>
          ) : null,
        )}

        <div className="mt-5">
          <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
            Interaction log
          </p>
          {detail && detail.interactions.length > 0 ? (
            <ul className="border-subtle mt-2 space-y-1.5 border-l-2 pl-3">
              {[...detail.interactions].reverse().map((entry, i) => (
                <li key={i} className="text-default text-sm">
                  {entry}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted mt-2 text-sm">
              Nothing logged yet. Say “Person: {person.name}…” into a capture and it lands here.
            </p>
          )}
        </div>

        <div className="mt-6 flex flex-wrap gap-2">
          {deepLink && (
            <a
              href={deepLink}
              className="border-emphasis text-emphasis inline-flex min-h-11 items-center rounded-xl border px-5 text-sm font-bold"
            >
              Open in Obsidian ↗
            </a>
          )}
          {person.dex_deeplink && (
            <a
              href={person.dex_deeplink}
              target="_blank"
              rel="noreferrer"
              className="border-subtle text-subtle inline-flex min-h-11 items-center rounded-xl border px-5 text-sm font-bold"
            >
              Open in Dex ↗
            </a>
          )}
        </div>
      </div>
    </div>
  );
}

export function People() {
  const status = usePolling(api.status, 60_000);
  const [people, setPeople] = useState<Person[] | null>(null);
  const [error, setError] = useState<ErrorEnvelope | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [open, setOpen] = useState<Person | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.people("all");
      setPeople(res.items);
      setError(null);
    } catch (err) {
      const envelope = (err as { envelope?: ErrorEnvelope }).envelope;
      setError(
        envelope ?? {
          what: "Couldn't load your people.",
          cause: "The server didn't answer.",
          todo: "Check the connection on the Settings screen, then try again.",
        },
      );
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const logContact = async (person: Person) => {
    setBusyId(person.id);
    try {
      await api.logContact(person.id);
      await load();
      toast(`Logged contact with ${person.name}.`);
    } catch (err) {
      toastError(err, "That didn't reach the server.");
    } finally {
      setBusyId(null);
    }
  };

  const sync = async () => {
    setSyncing(true);
    try {
      const result = await api.syncDex();
      await load();
      toast(result.message);
    } catch (err) {
      toastError(err, "The Dex sync didn't reach the server.");
    } finally {
      setSyncing(false);
    }
  };

  if (!people && error) return <ErrorState envelope={error} onRetry={() => void load()} />;
  if (!people) {
    return <div aria-hidden="true" className="bg-subtle h-40 animate-pulse rounded-xl" />;
  }

  const cold = people.filter((p) => p.status === "cold");
  const active = people.filter((p) => p.status === "active");
  const dormant = people.filter((p) => p.status === "dormant");
  const unset = people.filter((p) => p.unset);
  const vault = status.data?.vault;

  return (
    <div className="space-y-2">
      <div className="flex items-start justify-between gap-4">
        <p className="text-brand-default text-[11px] font-bold uppercase tracking-[0.18em]">
          Relationship OS
        </p>
        <button
          type="button"
          disabled={syncing}
          onClick={() => void sync()}
          className="border-subtle text-subtle min-h-11 shrink-0 rounded-xl border px-4 text-xs font-bold disabled:opacity-50"
        >
          {syncing ? "Syncing…" : "Sync from Dex"}
        </button>
      </div>

      {people.length === 0 ? (
        <div className="border-subtle mt-8 rounded-xl border border-dashed p-8">
          <p className="font-cal text-emphasis text-xl font-bold">No people tracked yet.</p>
          <p className="text-subtle mt-2 max-w-sm text-sm">
            Say “Person: [name]…” into a capture to start one, or pull your contacts across
            with Sync from Dex.
          </p>
        </div>
      ) : (
        <>
          {/* The one accent-lit element on this screen: who is slipping. */}
          {cold.length > 0 ? (
            <section className="bg-brand-default text-brand rounded-xl p-5">
              <p className="text-[11px] font-bold uppercase tracking-[0.18em] opacity-80">
                Going cold
              </p>
              <h2 className="font-cal mt-2 text-3xl font-extrabold leading-[0.95] -tracking-[0.02em]">
                {cold.length} {cold.length === 1 ? "person is" : "people are"} past their cadence
              </h2>
              <p className="mt-2 text-base font-semibold">
                {cold
                  .slice(0, 3)
                  .map((p) => p.name)
                  .join(", ")}
                {cold.length > 3 ? ` and ${cold.length - 3} more` : ""}.
              </p>
            </section>
          ) : (
            <section className="bg-subtle border-subtle rounded-xl border p-5">
              <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
                Going cold
              </p>
              <h2 className="font-cal text-emphasis mt-2 text-2xl font-extrabold leading-[0.95] -tracking-[0.02em]">
                Nobody's slipping
              </h2>
              <p className="text-default mt-2 text-sm">
                Everyone with a cadence is inside it.
              </p>
            </section>
          )}

          <Group
            label="Going cold"
            people={cold}
            busyId={busyId}
            onLogContact={logContact}
            onOpen={setOpen}
          />
          <Group
            label="Active"
            people={active}
            busyId={busyId}
            onLogContact={logContact}
            onOpen={setOpen}
          />
          <Group
            label="Dormant"
            people={dormant}
            busyId={busyId}
            onLogContact={logContact}
            onOpen={setOpen}
            collapsed
            note="Past three times their cadence. Still here whenever you want them back."
          />
          <Group
            label="Not set up"
            people={unset}
            busyId={busyId}
            onLogContact={logContact}
            onOpen={setOpen}
            collapsed
            note="These notes predate cadences. Open one to give it a cadence."
          />
        </>
      )}

      {open && (
        <Drawer
          person={people.find((p) => p.id === open.id) ?? open}
          vault={vault}
          onClose={() => setOpen(null)}
          onChanged={() => void load()}
        />
      )}
    </div>
  );
}
