import { useRef, useState } from "react";
import { api } from "../api/client";
import type { CaptureTag, Status, TodoItem } from "../api/types";
import { CAPTURE_TAGS } from "../api/types";
import { ErrorState } from "../components/ErrorState";
import { StreakDots } from "../components/StreakDots";
import { toast } from "../components/Toast";
import { usePolling } from "../hooks/usePolling";

const HEARTBEAT_LIMIT_MIN = 20;

type Health = "ok" | "attention" | "problem";

function heartbeatAgeMin(status: Status): number | null {
  if (!status.heartbeat) return null;
  const age = (Date.now() - new Date(status.heartbeat).getTime()) / 60000;
  return Number.isFinite(age) ? Math.round(age) : null;
}

function computeHealth(status: Status): { health: Health; sentence: string } {
  const age = heartbeatAgeMin(status);
  if (status.counts.failed > 0) {
    const n = status.counts.failed;
    return {
      health: "problem",
      sentence: `${n} capture${n === 1 ? "" : "s"} failed and ${n === 1 ? "needs" : "need"} a look.`,
    };
  }
  if (age === null || age > HEARTBEAT_LIMIT_MIN) {
    return {
      health: "problem",
      sentence:
        age === null
          ? "The pipeline has never checked in."
          : `The pipeline hasn't checked in for ${age} minutes.`,
    };
  }
  if (status.counts.needs_review > 0) {
    const n = status.counts.needs_review;
    return {
      health: "attention",
      sentence: `${n} capture${n === 1 ? "" : "s"} waiting for your call.`,
    };
  }
  return { health: "ok", sentence: "Everything processed. Nothing needs you." };
}

// The ONE accent-lit element on this screen. OK = calm tonal with a small
// accent dot; ATTENTION = full accent block; PROBLEM = spark pink.
function HeroCard({ status }: { status: Status }) {
  const { health, sentence } = computeHealth(status);

  if (health === "ok") {
    return (
      <section className="bg-subtle border-subtle rounded-xl border p-5">
        <p className="text-brand-default flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.18em]">
          <span aria-hidden="true" className="bg-brand-default h-2 w-2 rounded-full" />
          Systems · OK
        </p>
        <h2 className="font-cal text-emphasis mt-3 text-4xl font-extrabold leading-[0.95] -tracking-[0.02em]">
          All clear
        </h2>
        <p className="text-default mt-2 text-sm">{sentence}</p>
      </section>
    );
  }

  if (health === "attention") {
    return (
      <a href="#/triage" className="bg-brand-default block rounded-xl p-5">
        <p className="text-brand text-[11px] font-bold uppercase tracking-[0.18em] opacity-80">
          Systems · Attention
        </p>
        <h2 className="font-cal text-brand mt-3 text-6xl font-extrabold leading-[0.9] -tracking-[0.04em]">
          {status.counts.needs_review}
        </h2>
        <p className="text-brand mt-2 text-base font-semibold">{sentence}</p>
      </a>
    );
  }

  return (
    <a href="#/pipeline" className="bg-spark block rounded-xl p-5">
      <p className="text-spark-text text-[11px] font-bold uppercase tracking-[0.18em] opacity-80">
        Systems · Problem
      </p>
      <h2 className="font-cal text-spark-text mt-3 text-4xl font-extrabold leading-[0.95] -tracking-[0.02em]">
        Needs a look
      </h2>
      <p className="text-spark-text mt-2 text-base font-semibold">{sentence}</p>
    </a>
  );
}

function Resurfaced({ vault }: { vault: string }) {
  const { data, error, loading } = usePolling(api.resurfaced);
  if (loading || error) return null; // quiet card — the hero owns error surfacing
  const note = data?.note;
  if (!note) return null;
  const deepLink = `obsidian://open?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(
    note.file.replace(/\.md$/, ""),
  )}`;
  return (
    <section className="bg-cal-stamp border-subtle rounded-xl border p-5">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
        Resurfaced · {note.type}
      </p>
      <h3 className="font-cal text-emphasis mt-2 text-xl font-bold leading-tight -tracking-[0.01em]">
        {note.title}
      </h3>
      <p className="text-default mt-2 text-sm">{note.excerpt}</p>
      <a
        href={deepLink}
        className="border-emphasis text-emphasis mt-4 inline-flex min-h-11 items-center rounded-xl border px-5 text-sm font-bold"
      >
        Open in Obsidian ↗
      </a>
    </section>
  );
}


interface AgendaData {
  today: TodoItem[];
  tomorrow: TodoItem[];
  week: TodoItem[];
}

async function fetchAgenda(): Promise<AgendaData> {
  const [today, overdue, tomorrow, week] = await Promise.all([
    api.todos("today"),
    api.todos("overdue"),
    api.todos("tomorrow"),
    api.todos("week"),
  ]);
  // ADHD rule: today is always fully visible — open + overdue together.
  return {
    today: [...overdue.items, ...today.items],
    tomorrow: tomorrow.items,
    week: week.items,
  };
}

function fmtDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function AgendaRow({
  item,
  onToggle,
}: {
  item: TodoItem;
  onToggle: (item: TodoItem) => void;
}) {
  return (
    <li className="flex min-h-11 items-center gap-3 py-1">
      <button
        type="button"
        aria-label={item.done ? `Reopen: ${item.task}` : `Mark done: ${item.task}`}
        onClick={() => onToggle(item)}
        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2 transition-colors motion-reduce:transition-none ${
          item.done ? "bg-inverted border-transparent" : "border-emphasis"
        }`}
      >
        {item.done && (
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
            <path d="M2 6.5 4.8 9 10 3.5" stroke="var(--cal-text-inverted)" strokeWidth="2" fill="none" strokeLinecap="round" />
          </svg>
        )}
      </button>
      <div className="min-w-0 flex-1">
        <p
          className={`truncate text-sm transition-opacity motion-reduce:transition-none ${
            item.done ? "text-subtle line-through opacity-60" : "text-emphasis font-semibold"
          }`}
        >
          {item.task}
        </p>
        {(item.overdue || item.time) && (
          <p className="text-subtle text-xs">
            {item.overdue && !item.done ? (
              // overdue is marked tonally, never with the accent
              <span className="bg-cal-muted text-emphasis rounded px-1.5 py-0.5 font-bold">
                Overdue · {item.due ? fmtDay(item.due) : ""}
              </span>
            ) : (
              item.time && `at ${item.time}`
            )}
          </p>
        )}
      </div>
    </li>
  );
}

function Agenda() {
  const { data, error, loading, refetch } = usePolling(fetchAgenda, 60_000);
  const [flipped, setFlipped] = useState<Record<string, boolean>>({});

  if (loading && !data) {
    return <div aria-hidden="true" className="bg-subtle h-20 animate-pulse rounded-xl" />;
  }
  if (error && !data) return null; // the hero card owns whole-screen error surfacing

  const withFlips = (items: TodoItem[]) =>
    items.map((i) => (i.id in flipped ? { ...i, done: flipped[i.id] } : i));

  const toggle = async (item: TodoItem) => {
    const next = !item.done;
    setFlipped((f) => ({ ...f, [item.id]: next })); // optimistic
    try {
      await api.toggleTodo(item.id);
      toast(next ? "Done." : "Reopened.");
      refetch();
    } catch (err) {
      setFlipped((f) => ({ ...f, [item.id]: item.done }));
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(envelope ? `${envelope.what} ${envelope.todo}` : "That didn't reach the server.", "error");
    }
  };

  const today = withFlips(data!.today);
  const { tomorrow, week } = data!;

  return (
    <section>
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">Agenda</p>
      {today.length === 0 && tomorrow.length === 0 && week.length === 0 ? (
        <p className="text-default mt-2 text-sm">
          Nothing due. Capture a todo by voice — say the day and time.
        </p>
      ) : (
        <>
          {today.length > 0 ? (
            <ul className="mt-2">
              {today.map((t) => (
                <AgendaRow key={t.id} item={t} onToggle={toggle} />
              ))}
            </ul>
          ) : (
            <p className="text-default mt-2 text-sm">Nothing due today.</p>
          )}
          {/* the future is never more than a count until asked */}
          {tomorrow.length > 0 && (
            <details className="mt-1">
              <summary className="text-subtle min-h-11 cursor-pointer list-none py-2 text-sm font-semibold">
                Tomorrow ({tomorrow.length})
              </summary>
              <ul>
                {tomorrow.map((t) => (
                  <AgendaRow key={t.id} item={t} onToggle={toggle} />
                ))}
              </ul>
            </details>
          )}
          {week.length > 0 && (
            <details>
              <summary className="text-subtle min-h-11 cursor-pointer list-none py-2 text-sm font-semibold">
                This week ({week.length})
              </summary>
              <ul>
                {week.map((t) => (
                  <li key={t.id} className="flex items-baseline gap-2 py-1">
                    <span className="text-muted text-xs">{t.due ? fmtDay(t.due) : ""}</span>
                    <span className="text-default truncate text-sm">{t.task}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}

// Photos can arrive several MB straight off a phone/Mac camera; downscaling
// in-browser before upload keeps this well under the server's 15MB cap and
// makes the upload fast on mobile data. Native canvas API, no dependency
// (CLAUDE.md §7). If decoding fails (e.g. a format the browser can't
// rasterize, like some HEIC variants), the ORIGINAL file is sent as-is — the
// server's own magic-byte check gives a clear error if that too is
// unsupported. A resize must never be the reason a capture is lost.
const MAX_PHOTO_EDGE = 2048;

async function downscaleForUpload(file: File): Promise<Blob> {
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, MAX_PHOTO_EDGE / Math.max(bitmap.width, bitmap.height));
    const w = Math.round(bitmap.width * scale);
    const h = Math.round(bitmap.height * scale);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return file;
    ctx.drawImage(bitmap, 0, 0, w, h);
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.85),
    );
    return blob ?? file;
  } catch {
    return file;
  }
}

function QuickCapture() {
  const [text, setText] = useState("");
  const [tag, setTag] = useState<CaptureTag | null>(null);
  const [photo, setPhoto] = useState<File | null>(null);
  const [photoPreview, setPhotoPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const pickPhoto = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // lets picking the same file twice re-fire onChange
    if (!file) return;
    setPhoto(file);
    setPhotoPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(file);
    });
  };

  const clearPhoto = () => {
    setPhoto(null);
    setPhotoPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const body = text.trim();
    const sentTag = tag;

    if (photo) {
      setBusy(true);
      const sentPhoto = photo;
      try {
        const downscaled = await downscaleForUpload(sentPhoto);
        await api.captureImage(downscaled, body, sentTag);
        toast("✅ Captured");
        setText("");
        setTag(null);
        clearPhoto();
      } catch (err) {
        const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
        toast(
          envelope ? `${envelope.what} ${envelope.todo}` : "Photo capture didn't reach the server.",
          "error",
        );
      } finally {
        setBusy(false);
      }
      return;
    }

    if (!body) return;
    // Optimistic: trust signal first, network second (SCHEMA-REFERENCE.md §8).
    setText("");
    setTag(null);
    toast("✅ Captured");
    try {
      await api.capture(body, sentTag);
    } catch (err) {
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(
        envelope ? `${envelope.what} ${envelope.todo}` : "Capture didn't reach the server.",
        "error",
      );
      setText(body); // nothing is lost
      setTag(sentTag);
    }
  };

  return (
    <section>
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
        Quick capture
      </p>
      <form onSubmit={submit} className="mt-2">
        {photoPreview && (
          <div className="border-subtle bg-subtle relative mb-3 h-32 w-24 overflow-hidden rounded-xl border">
            <img src={photoPreview} alt="Selected photo" className="h-full w-full object-cover" />
            <button
              type="button"
              onClick={clearPhoto}
              aria-label="Remove photo"
              className="bg-inverted text-inverted absolute right-1 top-1 flex h-7 w-7 items-center justify-center rounded-full"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </button>
          </div>
        )}
        <div className="flex gap-2">
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={photo ? "Add a thought about this photo (optional)" : "What's on your mind?"}
            aria-label="Quick capture"
            className="bg-subtle border-subtle text-emphasis min-h-12 w-full rounded-xl border px-4 text-base"
          />
          <input
            ref={fileInput}
            type="file"
            accept="image/*"
            onChange={pickPhoto}
            className="hidden"
            aria-hidden="true"
            tabIndex={-1}
          />
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            aria-label="Attach a photo"
            className="border-subtle text-default min-h-12 shrink-0 rounded-xl border px-4"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M4 8a2 2 0 0 1 2-2h1.2a1 1 0 0 0 .83-.45l.94-1.4A1 1 0 0 1 9.8 3.6h4.4a1 1 0 0 1 .83.55l.94 1.4A1 1 0 0 0 16.8 6H18a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8Z"
                stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round"
              />
              <circle cx="12" cy="13" r="3.2" stroke="currentColor" strokeWidth="1.6" />
            </svg>
          </button>
          <button
            type="submit"
            disabled={busy || (!text.trim() && !photo)}
            className="bg-inverted text-inverted min-h-12 shrink-0 rounded-xl px-5 text-sm font-bold disabled:opacity-60"
          >
            {busy ? "Saving…" : "Capture"}
          </button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Capture tag (optional)">
          {CAPTURE_TAGS.map((t) => {
            const selected = tag === t;
            return (
              <button
                key={t}
                type="button"
                aria-pressed={selected}
                onClick={() => setTag(selected ? null : t)}
                className={`min-h-11 rounded-full border px-4 text-sm font-semibold ${
                  selected
                    ? "bg-emphasis border-emphasis text-emphasis"
                    : "bg-subtle border-subtle text-default"
                }`}
              >
                #{t}
              </button>
            );
          })}
        </div>
      </form>
    </section>
  );
}

function Skeleton() {
  return (
    <div className="space-y-6" aria-hidden="true">
      <div className="bg-subtle h-36 animate-pulse rounded-xl" />
      <div className="bg-subtle h-24 w-2/3 animate-pulse rounded-xl" />
      <div className="bg-subtle h-40 animate-pulse rounded-xl" />
    </div>
  );
}

export function Today() {
  const status = usePolling(api.status, 30_000);
  const streak = usePolling(api.streak);

  if (status.loading && !status.data) return <Skeleton />;
  if (status.error && !status.data) {
    return (
      <ErrorState
        envelope={status.error.envelope}
        detail={status.error.detail}
        onRetry={status.refetch}
      />
    );
  }

  return (
    <div className="space-y-8">
      {status.data && <HeroCard status={status.data} />}
      <Agenda />
      {streak.data && <StreakDots streak={streak.data} />}
      {status.data && <Resurfaced vault={status.data.vault} />}
      <QuickCapture />
    </div>
  );
}
