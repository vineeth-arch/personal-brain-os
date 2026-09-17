import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { HeldItem, LintResult, OutTouchType, PersonDetailV2, WarmthStage } from "../api/types";
import { OUT_TYPES } from "../api/types";
import { channelLabel, channelLink } from "../lib/channels";
import { GreenePanel } from "./GreenePanel";
import { LintList } from "./LintList";
import { toast } from "../components/Toast";

interface Props {
  detail: PersonDetailV2;
  queue?: string;
  sourceKey?: string;
  held?: HeldItem;
}

// The doctrine's rule for the queue a message came from → the touch type it
// naturally carries (patterns doc, T11). Reconnect has no fixed type — it
// takes the shape of whichever next action put the person in the queue.
const QUEUE_PRESET: Partial<Record<string, OutTouchType>> = {
  owe_reply: "remember",
  promises: "keep_promise",
  ask_about: "remember",
  celebrate: "celebrate",
  follow_up: "remember",
};

// Mirrors pipeline/queue.py's Next action → touch_type classification so a
// reconnect payload carries the type its own line implies.
function naturalTouchType(text: string): OutTouchType {
  if (text.startsWith("I promised:")) return "keep_promise";
  if (text.startsWith("Congratulate:") || text.startsWith("Remember date:")) return "celebrate";
  if (text.startsWith("Thank:") || text.startsWith("Report outcome:")) return "thank";
  if (text.startsWith("Intro check-in:")) return "give_who";
  return "remember";
}

const EMPTY_LINTS: LintResult = { lints: [], seducer: [] };

export function Composer({ detail, queue, sourceKey, held }: Props) {
  const openActions = detail.next_actions.filter((a) => !a.closed);
  const chipAction =
    (sourceKey && detail.next_actions.find((a) => a.key === sourceKey)) || openActions[0];

  const initialTouchType: OutTouchType =
    (held?.touch_type as OutTouchType) ||
    (queue === "reconnect"
      ? chipAction
        ? naturalTouchType(chipAction.text)
        : "remember"
      : (queue && QUEUE_PRESET[queue]) || "remember");

  const firstChannel =
    detail.preferred_channel ||
    (Object.keys(detail.channels)[0] as string | undefined) ||
    "whatsapp";

  const [text, setText] = useState(held?.text ?? "");
  const [channel, setChannel] = useState(held?.channel || firstChannel);
  const [touchType, setTouchType] = useState<OutTouchType>(initialTouchType);
  const [includeSensitive, setIncludeSensitive] = useState(false);
  const [requested, setRequested] = useState(false);
  const [outcome, setOutcome] = useState("");
  const [panelExpanded, setPanelExpanded] = useState(false);
  const [chosenCode, setChosenCode] = useState("");
  const [prideInserted, setPrideInserted] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [emailBusy, setEmailBusy] = useState(false);
  const [subject, setSubject] = useState("");
  const [lintResult, setLintResult] = useState<LintResult>(EMPTY_LINTS);
  const [suggestStage, setSuggestStage] = useState<WarmthStage | null>(null);
  const [heldDiscarded, setHeldDiscarded] = useState(false);
  const [blockedVoice, setBlockedVoice] = useState<string | null>(null);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const requestCounter = useRef(0);

  // Debounced lint requests (P21) — a stale response from an earlier keystroke
  // must never overwrite a newer one.
  useEffect(() => {
    if (!text.trim()) {
      setLintResult(EMPTY_LINTS);
      return;
    }
    const id = ++requestCounter.current;
    const timer = setTimeout(() => {
      void api
        .lintDraft({ text, channel, person_id: detail.id, touch_type: touchType })
        .then((r) => {
          if (id === requestCounter.current) setLintResult(r);
        })
        .catch(() => {});
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, channel, touchType, detail.id]);

  const insertAtCursor = (line: string) => {
    const el = textareaRef.current;
    const current = text;
    const start = el?.selectionStart ?? current.length;
    const end = el?.selectionEnd ?? current.length;
    const next = current.slice(0, start) + line + current.slice(end);
    setText(next);
    requestAnimationFrame(() => {
      const pos = start + line.length;
      el?.focus();
      el?.setSelectionRange(pos, pos);
    });
  };

  const draftWithAI = async () => {
    if (!chipAction) return;
    setDrafting(true);
    setBlockedVoice(null);
    try {
      const draft = await api.personDraft(detail.id, {
        channel,
        touch_type: touchType,
        payload: chipAction.text,
        outcome,
        situation: chosenCode,
        include_sensitive: includeSensitive,
      });
      setText(draft.text);
      setSubject(draft.subject);
      setLintResult(draft.lints);
    } catch (err) {
      const envelope = (err as { envelope?: { what: string } }).envelope;
      setBlockedVoice(envelope?.what ?? "Drafts need your own voice on file first.");
    } finally {
      setDrafting(false);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast("✅ Copied");
    } catch {
      toast("Couldn't copy — select the text and copy it by hand.", "error");
    }
  };

  const saveGmailDraft = async () => {
    const email = detail.channels.email;
    if (!email) return;
    setEmailBusy(true);
    try {
      await api.googleDraft({ to: email, subject, text });
      toast("✅ Draft saved — open it in Gmail.");
    } catch (err) {
      const envelope = (err as { envelope?: { what: string } }).envelope;
      toast(envelope?.what ?? "Couldn't save a Gmail draft — opening your mail app instead.", "error");
      const fallback = channelLink("email", email, text);
      if (fallback) window.location.href = fallback;
    } finally {
      setEmailBusy(false);
    }
  };

  const channelValue = detail.channels[channel as keyof typeof detail.channels];
  const link = channelValue ? channelLink(channel, channelValue, text) : null;

  const iSentIt = async () => {
    try {
      const result = await api.logContact(detail.id, {
        direction: "out",
        touch_type: touchType,
        greene: chosenCode || "",
        note: text.slice(0, 120),
        channel,
        requested,
      });
      if (
        (touchType === "keep_promise" || touchType === "keep_promise_late") &&
        chipAction?.text.startsWith("I promised:")
      ) {
        await api.closePromise(detail.id, {
          key: chipAction.key,
          result: touchType === "keep_promise" ? "kept" : "late",
          side: "mine",
        });
      }
      toast("✅ Logged");
      setSuggestStage(result.suggest_stage);
    } catch (err) {
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(envelope ? `${envelope.what} ${envelope.todo}` : "Couldn't log that.", "error");
    }
  };

  const theyReplied = async () => {
    try {
      await api.logContact(detail.id, {
        direction: "in",
        touch_type: "reply",
        note: "",
        channel,
      });
      toast("✅ Logged");
    } catch (err) {
      const envelope = (err as { envelope?: { what: string; todo: string } }).envelope;
      toast(envelope ? `${envelope.what} ${envelope.todo}` : "Couldn't log that.", "error");
    }
  };

  const advance = async (stage: WarmthStage) => {
    try {
      await api.setWarmth(detail.id, stage);
      toast(`✅ ${stage}`);
    } catch {
      toast("Couldn't change the stage.", "error");
    } finally {
      setSuggestStage(null);
    }
  };

  const discardHeld = async () => {
    try {
      await api.unhold(detail.id);
      setHeldDiscarded(true);
      toast("✅ Discarded");
    } catch {
      toast("Couldn't discard the held draft.", "error");
    }
  };

  return (
    <section className="bg-default border-subtle rounded-xl border p-4">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">Composer</p>

      {chipAction && (
        <p className="bg-subtle text-default mt-2 inline-block rounded-full px-3 py-1 text-sm font-semibold">
          {touchType} · {chipAction.text}
        </p>
      )}

      {held && !heldDiscarded && (
        <div className="border-subtle mt-3 rounded-xl border p-3">
          <p className="text-subtle text-sm italic">Still want to send this?</p>
          <button
            type="button"
            onClick={() => void discardHeld()}
            className="border-emphasis text-emphasis mt-2 min-h-11 rounded-xl border px-4 text-sm font-bold"
          >
            Discard held draft
          </button>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <label className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]" htmlFor="touch-type">
          Touch type
        </label>
        <select
          id="touch-type"
          aria-label="Touch type"
          value={touchType}
          onChange={(e) => setTouchType(e.target.value as OutTouchType)}
          className="bg-subtle border-subtle text-emphasis min-h-11 rounded-xl border px-3 text-sm"
        >
          {OUT_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>

        {Object.keys(detail.channels).length > 0 && (
          <div className="flex flex-wrap gap-2" role="group" aria-label="Channel">
            {Object.keys(detail.channels).map((c) => (
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
      </div>

      <label className="mt-3 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={requested}
          onChange={(e) => setRequested(e.target.checked)}
          className="h-5 w-5"
        />
        They asked for this
      </label>
      <label className="mt-2 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={includeSensitive}
          onChange={(e) => setIncludeSensitive(e.target.checked)}
          className="h-5 w-5"
        />
        Include sensitive details
      </label>

      <div className="mt-3">
        <GreenePanel
          personId={detail.id}
          expanded={panelExpanded}
          onToggle={() => setPanelExpanded((v) => !v)}
          reads={detail.reads}
          outcome={outcome}
          onOutcomeChange={setOutcome}
          presets={detail.presets}
          seducer={lintResult.seducer}
          onInsertLine={(line, code) => {
            insertAtCursor(line);
            setChosenCode(code);
          }}
          onInsertPride={(prideText) => {
            insertAtCursor(prideText);
            setPrideInserted(true);
          }}
          onHeld={(until) => {
            if (until) toast(`✅ Held until ${until}`);
            else toast("Couldn't hold that draft.", "error");
          }}
          text={text}
          channel={channel}
          touchType={touchType}
        />
      </div>

      {blockedVoice && (
        <p className="text-subtle mt-3 text-sm">{blockedVoice}</p>
      )}

      <label className="text-subtle mt-4 block text-[11px] font-bold uppercase tracking-[0.08em]">
        Draft — yours to edit
      </label>
      <textarea
        ref={textareaRef}
        aria-label="Draft message"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={6}
        className="bg-subtle border-subtle text-emphasis mt-2 w-full rounded-xl border p-3 text-base"
      />
      <LintList lints={lintResult.lints} />
      {prideInserted && (
        <p className="text-muted mt-2 text-[11px]">This is your read, not something they said.</p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {chipAction && (
          <button
            type="button"
            onClick={() => void draftWithAI()}
            disabled={drafting}
            className="bg-inverted text-inverted min-h-11 rounded-xl px-5 text-sm font-bold disabled:opacity-60"
          >
            {drafting ? "Drafting…" : "Draft with AI"}
          </button>
        )}
        <button
          type="button"
          onClick={() => void copy()}
          className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold"
        >
          Copy
        </button>
        {channel === "email" && (
          <button
            type="button"
            onClick={() => void saveGmailDraft()}
            disabled={emailBusy}
            className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold disabled:opacity-60"
          >
            {emailBusy ? "Saving…" : "Save Gmail draft"}
          </button>
        )}
        {link && (
          <a
            href={link}
            target="_blank"
            rel="noreferrer"
            className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold leading-[2.75rem]"
          >
            {channelLabel(channel)}
          </a>
        )}
        <button
          type="button"
          onClick={() => void iSentIt()}
          className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold"
        >
          I sent it
        </button>
        <button
          type="button"
          onClick={() => void theyReplied()}
          className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold"
        >
          They replied
        </button>
      </div>

      {suggestStage && (
        <div className="border-subtle mt-4 rounded-xl border p-4">
          <p className="text-emphasis text-sm font-bold">Logged. Move them to "{suggestStage}"?</p>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => void advance(suggestStage)}
              className="bg-inverted text-inverted min-h-11 rounded-xl px-5 text-sm font-bold"
            >
              Yes, {suggestStage}
            </button>
            <button
              type="button"
              onClick={() => setSuggestStage(null)}
              className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold"
            >
              Not yet
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
