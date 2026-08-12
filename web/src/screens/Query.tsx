/**
 * Ask your notes — search, then an answer that can only say what your notes say.
 *
 * "I couldn't find that" is a first-class result here, not an error state: it
 * gets the same three-part explanation as any other, plus whatever the search
 * did turn up so you can go read it yourself. The alternative — a confident
 * paragraph assembled from the model's own memory, wearing your notes' name —
 * is the failure this screen exists to avoid.
 *
 * The single accent is the answer card. Citation chips, sources and the search
 * button are tonal.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ErrorEnvelope, QueryAnswer, QuerySource } from "../api/types";
import { toast } from "../components/Toast";
import { usePolling } from "../hooks/usePolling";

function readQuestionFromHash(): string {
  const query = window.location.hash.split("?")[1] ?? "";
  const params = new URLSearchParams(query);
  return params.get("q") ?? "";
}

function writeQuestionToHash(question: string) {
  const base = window.location.hash.replace(/^#\/?/, "").split("?")[0] || "query";
  const suffix = question ? `?q=${encodeURIComponent(question)}` : "";
  // replaceState — asking three questions shouldn't mean three back presses
  history.replaceState(null, "", `#/${base}${suffix}`);
}

function deepLink(vault: string | undefined, file: string): string | null {
  if (!vault) return null;
  return `obsidian://open?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(
    file.replace(/\.md$/, ""),
  )}`;
}

/**
 * Renders the answer with its inline [[id]] markers turned into chips that
 * open the real note. An id the answer cites is always one of the sources —
 * the server rejects any answer where it isn't — so an unmatched marker here
 * would be a bug, and it's left as plain text rather than silently dropped.
 */
function AnswerText({
  answer,
  sources,
  vault,
}: {
  answer: string;
  sources: QuerySource[];
  vault: string | undefined;
}) {
  const byId = new Map(sources.map((s) => [s.id, s]));
  const parts = answer.split(/(\[\[\d{14}\]\])/g);
  return (
    <p className="text-base leading-relaxed">
      {parts.map((part, i) => {
        const match = /^\[\[(\d{14})\]\]$/.exec(part);
        if (!match) return <span key={i}>{part}</span>;
        const source = byId.get(match[1]);
        if (!source) return <span key={i}>{part}</span>;
        const href = deepLink(vault, source.file);
        const label = source.title || source.id;
        return href ? (
          <a
            key={i}
            href={href}
            title={`Open “${label}” in Obsidian`}
            className="mx-0.5 inline-flex items-center rounded-full border border-current px-2 py-0.5 align-baseline text-xs font-bold"
          >
            {label} ↗
          </a>
        ) : (
          <span key={i} className="mx-0.5 text-xs font-bold">
            {label}
          </span>
        );
      })}
    </p>
  );
}

function SourceList({ sources, vault }: { sources: QuerySource[]; vault: string | undefined }) {
  if (sources.length === 0) return null;
  return (
    <section className="mt-6">
      <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
        From your notes ({sources.length})
      </p>
      <ul className="mt-2 space-y-2">
        {sources.map((source) => {
          const href = deepLink(vault, source.file);
          return (
            <li key={source.id} className="bg-subtle border-subtle rounded-xl border p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-emphasis truncate text-sm font-bold">{source.title}</p>
                  <p className="text-subtle mt-1 text-xs">
                    {source.type || "note"} · {source.file}
                  </p>
                  {source.snippet && (
                    <p className="text-default mt-2 text-sm">{source.snippet}</p>
                  )}
                </div>
                {href && (
                  <a
                    href={href}
                    aria-label={`Open ${source.title} in Obsidian`}
                    className="border-subtle text-subtle hover:border-emphasis flex min-h-11 shrink-0 items-center rounded-xl border px-3 text-xs font-bold"
                  >
                    Open ↗
                  </a>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function Query() {
  const status = usePolling(api.status, 60_000);
  const [question, setQuestion] = useState(readQuestionFromHash);
  const [asking, setAsking] = useState(false);
  const [result, setResult] = useState<QueryAnswer | null>(null);
  const [asked, setAsked] = useState("");
  const [reindexing, setReindexing] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const ask = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setAsking(true);
    setResult(null);
    setAsked(trimmed);
    writeQuestionToHash(trimmed);
    try {
      setResult(await api.ask(trimmed));
    } catch (err) {
      const envelope = (err as { envelope?: ErrorEnvelope }).envelope;
      toast(
        envelope ? `${envelope.what} ${envelope.todo}` : "That didn't reach the server.",
        "error",
      );
    } finally {
      setAsking(false);
    }
  }, []);

  const reindex = async () => {
    setReindexing(true);
    try {
      const res = await api.reindex();
      toast(`Reindexed ${res.indexed} note${res.indexed === 1 ? "" : "s"}.`);
      if (asked) void ask(asked);
    } catch (err) {
      const envelope = (err as { envelope?: ErrorEnvelope }).envelope;
      toast(envelope ? `${envelope.what} ${envelope.todo}` : "The reindex didn't run.", "error");
    } finally {
      setReindexing(false);
    }
  };

  const vault = status.data?.vault;

  return (
    <div className="space-y-2">
      <div className="flex items-start justify-between gap-4">
        <p className="text-brand-default text-[11px] font-bold uppercase tracking-[0.18em]">
          Ask your notes
        </p>
        <button
          type="button"
          disabled={reindexing}
          onClick={() => void reindex()}
          className="border-subtle text-subtle min-h-11 shrink-0 rounded-xl border px-4 text-xs font-bold disabled:opacity-50"
        >
          {reindexing ? "Reindexing…" : "Rebuild index"}
        </button>
      </div>

      <form
        className="bg-default sticky top-0 z-10 -mx-5 mt-3 flex gap-2 px-5 py-3"
        onSubmit={(e) => {
          e.preventDefault();
          void ask(question);
        }}
      >
        <input
          ref={inputRef}
          type="search"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="What did I decide about…?"
          aria-label="Ask your notes a question"
          className="bg-subtle border-subtle text-emphasis min-h-12 flex-1 rounded-xl border px-4 text-sm"
        />
        <button
          type="submit"
          disabled={asking || !question.trim()}
          className="border-emphasis text-emphasis min-h-12 shrink-0 rounded-xl border px-5 text-sm font-bold disabled:opacity-40"
        >
          {asking ? "Asking…" : "Ask"}
        </button>
      </form>

      {asking && (
        <div className="mt-4 space-y-3" aria-live="polite">
          <p className="text-subtle text-sm">Searching your notes, then reading what it found…</p>
          <div aria-hidden="true" className="bg-subtle h-28 animate-pulse rounded-xl" />
        </div>
      )}

      {!asking && !result && (
        <div className="border-subtle mt-8 rounded-xl border border-dashed p-8">
          <p className="font-cal text-emphasis text-xl font-bold">Ask anything.</p>
          <p className="text-subtle mt-2 max-w-sm text-sm">
            I'll search your notes and cite what I find — or tell you when I can't. Answers
            only ever come from what's in the vault.
          </p>
        </div>
      )}

      {!asking && result && (
        <div className="mt-4">
          {result.found && result.answer ? (
            /* the one accent-lit element on this screen */
            <section className="bg-brand-default text-brand rounded-xl p-5">
              <p className="text-[11px] font-bold uppercase tracking-[0.18em] opacity-80">
                Answer
              </p>
              <div className="mt-3">
                <AnswerText answer={result.answer} sources={result.sources} vault={vault} />
              </div>
              {result.provider && (
                <p className="mt-4 text-xs font-semibold opacity-70">
                  Answered by {result.provider}, from the notes below only.
                </p>
              )}
            </section>
          ) : (
            result.message && (
              <section
                className="bg-cal-muted border-emphasis rounded-xl border p-5"
                aria-live="polite"
              >
                <p className="text-emphasis font-cal text-xl font-bold">{result.message.what}</p>
                <p className="text-default mt-2 text-sm">
                  <span className="text-subtle font-bold">Likely cause:</span>{" "}
                  {result.message.cause}
                </p>
                <p className="text-default mt-1.5 text-sm">
                  <span className="text-subtle font-bold">What to do:</span>{" "}
                  {result.message.todo}
                </p>
              </section>
            )
          )}
          <SourceList sources={result.sources} vault={vault} />
        </div>
      )}
    </div>
  );
}
