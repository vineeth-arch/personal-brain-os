import { useEffect, useRef, useState } from "react";
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

// Recording state lives beside the text field: tap to start, tap to stop
// (never a held gesture — a hold is exactly what an ADHD hand lets go of).
type MicState =
  | { kind: "idle" }
  | { kind: "recording" }
  | { kind: "pending"; blob: Blob }   // recorded, waiting for the upload to land
  | { kind: "blocked"; what: string; cause: string; todo: string };

function mimeForRecording(): string {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  const supported = candidates.find(
    (m) => typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported?.(m),
  );
  return supported ?? "";
}

function elapsedLabel(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function MicButton({
  tag,
  name,
  onCaptured,
}: {
  tag: CaptureTag | null;
  name: string;
  onCaptured: () => void;
}) {
  const [state, setState] = useState<MicState>({ kind: "idle" });
  const [seconds, setSeconds] = useState(0);
  const [level, setLevel] = useState(0);
  const recorder = useRef<MediaRecorder | null>(null);
  const stopAudio = useRef<(() => void) | null>(null);
  const stream = useRef<MediaStream | null>(null);
  // read at the moment recording stops, not at the moment it started — the
  // quick-capture box is still editable while a recording is in progress
  const nameRef = useRef(name);
  nameRef.current = name;

  // Navigating away mid-recording used to leave the MediaStream open, so the
  // browser's recording indicator stayed lit with nothing listening. Release
  // the tracks and the audio graph on unmount, whatever state we are in.
  useEffect(() => {
    return () => {
      stopAudio.current?.();
      try {
        recorder.current?.state !== "inactive" && recorder.current?.stop();
      } catch {
        // already stopped — nothing to release
      }
      stream.current?.getTracks().forEach((t) => t.stop());
      stream.current = null;
    };
  }, []);

  useEffect(() => {
    if (state.kind !== "recording") return;
    const started = Date.now();
    const id = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - started) / 1000));
    }, 250);
    return () => window.clearInterval(id);
  }, [state.kind]);

  // Everything the browser holds open (tracks, audio graph, timers) is released
  // here, so a half-finished recording can never keep the mic light on.
  const teardown = () => {
    stopAudio.current?.();
    stopAudio.current = null;
    recorder.current = null;
    stream.current = null;
    setSeconds(0);
    setLevel(0);
  };

  const upload = async (blob: Blob, sentTag: CaptureTag | null, sentName: string) => {
    // Optimistic, like the text capture: the trust signal comes first.
    setState({ kind: "idle" });
    toast("✅ Captured");
    try {
      await api.captureAudio(blob, sentTag, sentName);
      onCaptured();
    } catch (err) {
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(
        envelope ? `${envelope.what} ${envelope.todo}` : "The recording didn't reach the server.",
        "error",
      );
      setState({ kind: "pending", blob }); // nothing is lost — one tap retries
    }
  };

  const start = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setState({
        kind: "blocked",
        what: "The mic needs the secure (tunnel) address.",
        cause: "Browsers only allow recording over HTTPS or on localhost, and this page is on plain http.",
        todo: "Open the cockpit on your tunnel address, or use the Action Button flow on your phone.",
      });
      return;
    }
    let media: MediaStream;
    try {
      media = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setState({
        kind: "blocked",
        what: "Mic access was blocked.",
        cause: "The browser or the phone's settings denied the microphone permission for this site.",
        todo: "Allow the microphone for this address in your browser settings, then tap the mic again.",
      });
      return;
    }

    stream.current = media;
    const mime = mimeForRecording();
    const rec = new MediaRecorder(media, mime ? { mimeType: mime } : undefined);
    const parts: BlobPart[] = [];
    rec.ondataavailable = (e) => {
      if (e.data.size > 0) parts.push(e.data);
    };
    rec.onstop = () => {
      const blob = new Blob(parts, { type: rec.mimeType || mime || "audio/webm" });
      teardown();
      media.getTracks().forEach((t) => t.stop());
      if (blob.size > 0) void upload(blob, tag, nameRef.current);
      else setState({ kind: "idle" });
    };

    // A quiet level indicator so it's visibly listening, not just timing.
    try {
      const ctx = new AudioContext();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      ctx.createMediaStreamSource(media).connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      let raf = 0;
      const sample = () => {
        analyser.getByteTimeDomainData(data);
        let peak = 0;
        for (const v of data) peak = Math.max(peak, Math.abs(v - 128) / 128);
        setLevel(peak);
        raf = requestAnimationFrame(sample);
      };
      raf = requestAnimationFrame(sample);
      stopAudio.current = () => {
        cancelAnimationFrame(raf);
        void ctx.close();
      };
    } catch {
      stopAudio.current = null; // the meter is decoration; recording matters
    }

    recorder.current = rec;
    rec.start();
    setSeconds(0);
    setState({ kind: "recording" });
  };

  const stop = () => recorder.current?.stop();

  const recording = state.kind === "recording";
  const bars = [0, 1, 2, 3];

  return (
    <div>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={recording ? stop : () => void start()}
          aria-pressed={recording}
          aria-label={recording ? "Stop recording" : "Record a voice capture"}
          className={`flex min-h-12 w-12 shrink-0 items-center justify-center rounded-full border transition-colors motion-reduce:transition-none ${
            recording
              ? "bg-emphasis border-emphasis text-emphasis"
              : "bg-subtle border-subtle text-default"
          }`}
        >
          {recording ? (
            <span aria-hidden="true" className="bg-spark h-3.5 w-3.5 rounded-[3px]" />
          ) : (
            <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none"
                 stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
              <rect x="9" y="3" width="6" height="11" rx="3" />
              <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
            </svg>
          )}
        </button>

        {recording && (
          <div className="flex items-center gap-3" role="status" aria-live="polite">
            <span className="font-cal text-emphasis text-2xl font-extrabold -tracking-[0.02em] tabular-nums">
              {elapsedLabel(seconds)}
            </span>
            <span aria-hidden="true" className="flex items-end gap-1">
              {bars.map((i) => (
                <span
                  key={i}
                  className="bg-cal-muted w-1 rounded-full"
                  style={{ height: `${6 + Math.min(1, level * (1 + i * 0.4)) * 16}px` }}
                />
              ))}
            </span>
            <span className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
              Tap to stop
            </span>
          </div>
        )}

        {state.kind === "pending" && (
          <button
            type="button"
            onClick={() => void upload(state.blob, tag, nameRef.current)}
            className="border-emphasis text-emphasis min-h-11 rounded-xl border px-4 text-sm font-bold"
          >
            Retry upload
          </button>
        )}
      </div>

      {state.kind === "blocked" && (
        <div className="border-subtle mt-3 rounded-xl border p-3">
          <p className="text-emphasis text-sm font-bold">{state.what}</p>
          <p className="text-subtle mt-1 text-sm">{state.cause}</p>
          <p className="text-default mt-1 text-sm">{state.todo}</p>
        </div>
      )}

      <p className="text-muted mt-2 text-[11px]">
        For capture that survives a locked screen, use the Action Button flow.
      </p>
    </div>
  );
}

function QuickCapture() {
  const [text, setText] = useState("");
  const [tag, setTag] = useState<CaptureTag | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const body = text.trim();
    if (!body) return;
    const sentTag = tag;
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
        <div className="flex gap-2">
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="What's on your mind?"
            aria-label="Quick capture"
            className="bg-subtle border-subtle text-emphasis min-h-12 w-full rounded-xl border px-4 text-base"
          />
          <button
            type="submit"
            className="bg-inverted text-inverted min-h-12 shrink-0 rounded-xl px-5 text-sm font-bold"
          >
            Capture
          </button>
        </div>
        <div className="mt-3">
          <MicButton tag={tag} name={text} onCaptured={() => setTag(null)} />
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
