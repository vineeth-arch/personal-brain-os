// The person page — the record (this file) beside the composer (Composer.tsx).
// DOM order is record then composer; on `lg` they sit side by side via a
// two-column grid, per DESIGNSYSTEM.md's flush-left, no-centered-text rule.
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { HeldItem, NextActionRow, PersonDetailV2 } from "../api/types";
import { Composer } from "../components/Composer";
import { ErrorState } from "../components/ErrorState";
import { toast } from "../components/Toast";

const TIERS = ["", "inner", "core", "active", "wide"] as const;
const ENERGIES = ["", "gives", "neutral", "drains"] as const;
const FITS = ["", "ideal", "good", "poor", "unknown"] as const;
const BUYER_ROLES = ["", "economic", "influencer", "user", "gatekeeper", "unknown"] as const;
const CONVERSATION_STAGES = [
  "",
  "none",
  "probative",
  "qualifying",
  "value",
  "closing",
  "delivering",
  "past",
] as const;

const ENERGY_GLYPH: Record<string, string> = { gives: "↑", neutral: "–", drains: "↓" };

function parseHashParams(): { id: string; queue?: string; key?: string } {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const [path, qs] = hash.split("?");
  const id = path.replace(/^people\//, "");
  const params = new URLSearchParams(qs ?? "");
  return { id, queue: params.get("queue") ?? undefined, key: params.get("key") ?? undefined };
}

function firstTwoLines(text: string): string {
  return text.split("\n").filter((l) => l.trim()).slice(0, 2).join("\n");
}

function EmptySection() {
  return <p className="text-muted text-sm">Nothing here yet.</p>;
}

function KnownFor({ detail, onSaved }: { detail: PersonDetailV2; onSaved: (d: PersonDetailV2) => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(detail.known_for);

  const save = async () => {
    try {
      const result = await api.ownerEdit(detail.id, { field: "known_for", value });
      onSaved(result);
      setEditing(false);
    } catch {
      toast("Couldn't save that.", "error");
    }
  };

  return (
    <section>
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">Known for</p>
      {editing ? (
        <div className="mt-2 flex flex-wrap gap-2">
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="bg-subtle border-subtle text-emphasis min-h-11 flex-1 rounded-xl border p-3 text-sm"
          />
          <button
            type="button"
            onClick={() => void save()}
            className="bg-inverted text-inverted min-h-11 rounded-xl px-4 text-sm font-bold"
          >
            Save
          </button>
          <button
            type="button"
            onClick={() => {
              setValue(detail.known_for);
              setEditing(false);
            }}
            className="border-emphasis text-emphasis min-h-11 rounded-xl border px-4 text-sm font-bold"
          >
            Cancel
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="text-default mt-1 block text-left text-sm"
        >
          {detail.known_for || "Not set — tap to add"}
        </button>
      )}
      {detail.recall_trigger && (
        <p className="text-muted mt-1 text-sm">{detail.recall_trigger}</p>
      )}
    </section>
  );
}

function PromiseList({
  title,
  items,
  actions,
  detail,
  onSaved,
  side,
}: {
  title: string;
  items: NextActionRow[];
  actions: { label: string; result: string }[];
  detail: PersonDetailV2;
  onSaved: (d: PersonDetailV2) => void;
  side: "mine" | "theirs";
}) {
  const close = async (item: NextActionRow, result: string) => {
    try {
      const updated = await api.closePromise(detail.id, { key: item.key, result, side });
      onSaved(updated);
    } catch {
      toast("That promise isn't open anymore.", "error");
    }
  };
  return (
    <section>
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">{title}</p>
      {items.length === 0 ? (
        <div className="mt-2">
          <EmptySection />
        </div>
      ) : (
        <ul className="mt-2 space-y-2">
          {items.map((item) => (
            <li key={item.key} className="border-subtle rounded-xl border p-3">
              <p className="text-default text-sm">{item.text}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {actions.map((a) => (
                  <button
                    key={a.label}
                    type="button"
                    onClick={() => void close(item, a.result)}
                    className="border-emphasis text-emphasis min-h-11 rounded-xl border px-3 text-sm font-bold"
                  >
                    {a.label}
                  </button>
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ActionList({ title, items }: { title: string; items: NextActionRow[] }) {
  return (
    <section>
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">{title}</p>
      {items.length === 0 ? (
        <div className="mt-2">
          <EmptySection />
        </div>
      ) : (
        <ul className="mt-2 space-y-1">
          {items.map((item) => (
            <li key={item.key} className="text-default text-sm">
              {item.text}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function TextSection({ title, text }: { title: string; text: string }) {
  return (
    <section>
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">{title}</p>
      {text ? (
        <p className="text-default mt-1 whitespace-pre-wrap text-sm">{text}</p>
      ) : (
        <div className="mt-2">
          <EmptySection />
        </div>
      )}
    </section>
  );
}

function EditRecordSheet({
  detail,
  onSaved,
  onClose,
}: {
  detail: PersonDetailV2;
  onSaved: (d: PersonDetailV2) => void;
  onClose: () => void;
}) {
  const [warning, setWarning] = useState<string | null>(null);
  const [birthday, setBirthday] = useState(detail.dates.birthday);
  const [anniversary, setAnniversary] = useState(detail.dates.anniversary);

  const save = async (field: string, value: string) => {
    try {
      const result = await api.ownerEdit(detail.id, { field, value });
      onSaved(result);
      setWarning(result.warning);
    } catch {
      toast("Couldn't save that field.", "error");
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Edit record for ${detail.name}`}
      className="fixed inset-0 z-40 flex items-end justify-center sm:items-center"
    >
      <button aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/50" />
      <div className="bg-default border-subtle relative max-h-[92dvh] w-full max-w-lg overflow-y-auto rounded-t-2xl border p-5 sm:rounded-2xl">
        <div className="flex items-start justify-between gap-4">
          <h2 className="font-cal text-emphasis text-xl font-extrabold -tracking-[0.02em]">
            Edit record
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="border-subtle text-default flex h-11 w-11 shrink-0 items-center justify-center rounded-full border"
          >
            ✕
          </button>
        </div>

        {warning && <p className="text-subtle mt-3 text-sm">{warning}</p>}

        <label className="text-subtle mt-4 block text-[11px] font-bold uppercase tracking-[0.08em]">
          Tier
        </label>
        <select
          value={detail.tier}
          onChange={(e) => void save("tier", e.target.value)}
          className="bg-subtle border-subtle text-emphasis mt-1 min-h-11 w-full rounded-xl border p-3 text-sm"
        >
          {TIERS.map((t) => (
            <option key={t} value={t}>
              {t || "untiered"}
            </option>
          ))}
        </select>

        <label className="text-subtle mt-4 block text-[11px] font-bold uppercase tracking-[0.08em]">
          Energy
        </label>
        <select
          value={detail.energy ?? ""}
          onChange={(e) => void save("energy", e.target.value)}
          className="bg-subtle border-subtle text-emphasis mt-1 min-h-11 w-full rounded-xl border p-3 text-sm"
        >
          {ENERGIES.map((e) => (
            <option key={e} value={e}>
              {e || "unset"}
            </option>
          ))}
        </select>

        <label className="mt-4 flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={detail.list_of_20}
            onChange={(e) => void save("list_of_20", e.target.checked ? "true" : "false")}
            className="h-5 w-5"
          />
          List of 20
        </label>

        {detail.commercial && (
          <>
            <label className="text-subtle mt-4 block text-[11px] font-bold uppercase tracking-[0.08em]">
              Conversation stage
            </label>
            <select
              value={detail.working_together?.conversation_stage ?? ""}
              onChange={(e) => void save("conversation_stage", e.target.value)}
              className="bg-subtle border-subtle text-emphasis mt-1 min-h-11 w-full rounded-xl border p-3 text-sm"
            >
              {CONVERSATION_STAGES.map((s) => (
                <option key={s} value={s}>
                  {s || "unset"}
                </option>
              ))}
            </select>

            <label className="text-subtle mt-4 block text-[11px] font-bold uppercase tracking-[0.08em]">
              Buyer role
            </label>
            <select
              value={detail.working_together?.buyer_role ?? ""}
              onChange={(e) => void save("buyer_role", e.target.value)}
              className="bg-subtle border-subtle text-emphasis mt-1 min-h-11 w-full rounded-xl border p-3 text-sm"
            >
              {BUYER_ROLES.map((r) => (
                <option key={r} value={r}>
                  {r || "unset"}
                </option>
              ))}
            </select>

            <label className="text-subtle mt-4 block text-[11px] font-bold uppercase tracking-[0.08em]">
              Fit
            </label>
            <select
              value={detail.working_together?.fit ?? ""}
              onChange={(e) => void save("fit", e.target.value)}
              className="bg-subtle border-subtle text-emphasis mt-1 min-h-11 w-full rounded-xl border p-3 text-sm"
            >
              {FITS.map((f) => (
                <option key={f} value={f}>
                  {f || "unset"}
                </option>
              ))}
            </select>
          </>
        )}

        <label className="text-subtle mt-4 block text-[11px] font-bold uppercase tracking-[0.08em]">
          Birthday (MM-DD)
        </label>
        <input
          value={birthday}
          onChange={(e) => setBirthday(e.target.value)}
          onBlur={() =>
            void save("dates", JSON.stringify({ birthday, anniversary }))
          }
          className="bg-subtle border-subtle text-emphasis mt-1 w-full rounded-xl border p-3 text-sm"
        />
        <label className="text-subtle mt-4 block text-[11px] font-bold uppercase tracking-[0.08em]">
          Anniversary (MM-DD)
        </label>
        <input
          value={anniversary}
          onChange={(e) => setAnniversary(e.target.value)}
          onBlur={() =>
            void save("dates", JSON.stringify({ birthday, anniversary }))
          }
          className="bg-subtle border-subtle text-emphasis mt-1 w-full rounded-xl border p-3 text-sm"
        />
      </div>
    </div>
  );
}

export function Person() {
  const { id, queue, key } = parseHashParams();
  const [detail, setDetail] = useState<PersonDetailV2 | null>(null);
  const [held, setHeld] = useState<HeldItem | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ what: string; cause: string; todo: string } | null>(null);
  const [editingRecord, setEditingRecord] = useState(false);

  useEffect(() => {
    let live = true;
    setLoading(true);
    const desk = window.matchMedia("(min-width: 1024px)").matches;
    void Promise.all([api.person(id, { desk }), api.held()])
      .then(([d, h]) => {
        if (!live) return;
        setDetail(d);
        setHeld(h.items.find((i) => i.person_id === id));
      })
      .catch((err) => {
        const envelope = (err as { envelope?: { what: string; cause: string; todo: string } })
          .envelope;
        if (live) setError(envelope ?? { what: "Couldn't load this person.", cause: "The request failed.", todo: "Reload the page." });
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  if (loading && !detail) {
    return <div aria-hidden="true" className="bg-subtle h-40 animate-pulse rounded-xl" />;
  }
  if (error && !detail) {
    return <ErrorState envelope={error} />;
  }
  if (!detail) return null;

  const openActions = detail.next_actions.filter((a) => !a.closed);
  const iPromised = openActions.filter((a) => a.text.startsWith("I promised:"));
  const theyPromised = openActions.filter((a) => a.text.startsWith("Check in — they promised:"));
  const askAbout = openActions.filter(
    (a) => a.view === "ask_about" && !a.text.startsWith("Check in — they promised:"),
  );
  const followUps = openActions.filter((a) => a.view === "follow_up");

  const relationships = detail.relationships.length ? detail.relationships : detail.relationship ? [detail.relationship] : [];

  return (
    <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,28rem)] lg:gap-8">
      <div className="space-y-6">
        {/* 1. Header */}
        <header>
          <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
            {detail.tier || "untiered"} · {detail.status_computed}
          </p>
          <div className="flex items-start justify-between gap-3">
            <h1 className="font-cal text-emphasis mt-1 text-3xl font-extrabold leading-[0.95] -tracking-[0.02em]">
              {detail.name}
            </h1>
            {detail.energy && (
              <span aria-label="energy" className="text-emphasis hidden text-2xl lg:inline">
                {ENERGY_GLYPH[detail.energy] ?? ""}
              </span>
            )}
          </div>
          {relationships.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {relationships.map((r) => (
                <span
                  key={r}
                  className="bg-subtle text-default rounded-full px-3 py-1 text-xs font-semibold"
                >
                  {r}
                </span>
              ))}
            </div>
          )}
          <p className="text-default mt-2 text-sm">
            gives 90d {detail.ledger.gives90} · asks {detail.ledger.asks90} · received{" "}
            {detail.ledger.received90}
          </p>
          {detail.reliability_line && (
            <p className="text-subtle mt-1 text-sm">{detail.reliability_line}</p>
          )}
          <p className="text-subtle mt-1 text-sm">
            {detail.last_contact ? `Last contact ${detail.last_contact}` : "Never contacted"}
          </p>
          <p className="text-subtle mt-1 text-sm">
            {detail.preferred_channel || "No preferred channel"}
            {detail.language ? ` · ${detail.language}` : ""}
          </p>
        </header>

        {/* 2. Known for */}
        <KnownFor detail={detail} onSaved={setDetail} />

        {/* 3. Quiet line */}
        {detail.quiet && <p className="text-muted text-sm">{detail.quiet.line}</p>}

        {/* 4. Before you talk */}
        <details className="border-subtle rounded-xl border p-4">
          <summary className="text-subtle cursor-pointer text-[11px] font-bold uppercase tracking-[0.08em]">
            Before you talk
          </summary>
          <div className="mt-3 space-y-2 text-sm">
            <p className="text-default">
              Calm? · Pride: {detail.reads.pride || "–"} · Record: {detail.reads.record || "–"}
            </p>
            {detail.current_state && (
              <p className="text-default whitespace-pre-wrap">{firstTwoLines(detail.current_state)}</p>
            )}
            {iPromised[0] && <p className="text-default">{iPromised[0].text}</p>}
            {theyPromised[0] && <p className="text-default">{theyPromised[0].text}</p>}
            {askAbout[0] && <p className="text-default">{askAbout[0].text}</p>}
            {detail.how_they_communicate && (
              <p className="text-default whitespace-pre-wrap">{detail.how_they_communicate}</p>
            )}
            {openActions.find((a) => a.text.startsWith("Kind truth:")) && (
              <p className="text-default">
                {openActions.find((a) => a.text.startsWith("Kind truth:"))?.text}
              </p>
            )}
          </div>
        </details>

        {/* 5. Working together */}
        {detail.working_together && (
          <section>
            <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
              Working together
            </p>
            {detail.working_together.no_economic_buyer && (
              <p className="text-default mt-1 text-sm">You have not met the economic buyer.</p>
            )}
            <p className="text-default mt-1 text-sm">
              {detail.working_together.conversation_stage || "unset"} ·{" "}
              {detail.working_together.buyer_role || "unset"} ·{" "}
              {detail.working_together.fit || "unset"}
            </p>
          </section>
        )}

        {/* 6. Sections */}
        <PromiseList
          title="I promised"
          items={iPromised}
          actions={[
            { label: "Kept", result: "kept" },
            { label: "Kept late", result: "late" },
            { label: "Dropped", result: "dropped" },
          ]}
          detail={detail}
          onSaved={setDetail}
          side="mine"
        />
        <PromiseList
          title="They promised"
          items={theyPromised}
          actions={[
            { label: "Kept", result: "kept" },
            { label: "Late", result: "late" },
            { label: "Dropped", result: "dropped" },
          ]}
          detail={detail}
          onSaved={setDetail}
          side="theirs"
        />
        <ActionList title="Open follow-ups" items={followUps} />
        <ActionList title="Ask them about" items={askAbout} />
        <TextSection title="What's going on for them" text={detail.current_state} />
        <TextSection title="Where they're headed" text={detail.future_state} />
        <TextSection title="They're looking for" text={detail.needs} />
        <TextSection title="They can help with" text={detail.can_help} />
        <TextSection title="How they communicate (my read)" text={detail.how_they_communicate} />
        <TextSection title="About them" text={detail.facts} />
        <TextSection title="Remember" text={detail.updates} />
        <TextSection title="My interpretations, not things they said" text={detail.interpretations} />

        <section>
          <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
            Interaction log
          </p>
          {detail.touches.length === 0 ? (
            <div className="mt-2">
              <EmptySection />
            </div>
          ) : (
            <ul className="mt-2 space-y-1">
              {detail.touches.map((t, i) => (
                <li key={i} className="text-default text-sm">
                  {t.day} · {t.direction} · {t.channel} · {t.touch_type} · {t.summary}
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* 7. Edit record */}
        <button
          type="button"
          onClick={() => setEditingRecord(true)}
          className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold"
        >
          Edit record
        </button>
        {editingRecord && (
          <EditRecordSheet
            detail={detail}
            onSaved={setDetail}
            onClose={() => setEditingRecord(false)}
          />
        )}

        {/* 8. Push profile */}
        <a href="#/people" className="text-subtle inline-block text-sm underline">
          Push profile
        </a>
      </div>

      <div className="mt-6 lg:mt-0">
        <Composer detail={detail} queue={queue} sourceKey={key} held={held} />
      </div>
    </div>
  );
}
