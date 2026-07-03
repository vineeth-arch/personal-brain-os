import { Component, type ReactNode } from "react";
import { toast } from "./Toast";

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

// The single app-wide boundary. Plain-English fallback; the stack stays
// behind the copy button — never rendered on screen (CLAUDE.md §5).
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  private copyDetails = async () => {
    const { error } = this.state;
    const text = `${error?.name}: ${error?.message}\n${error?.stack ?? ""}`;
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied. Paste it wherever you keep bugs.");
    } catch {
      // clipboard can be unavailable (http, permissions) — show it as a
      // last resort so the user can still select-copy manually
      window.prompt("Copy the technical details below:", text);
    }
  };

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center p-6">
        <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
          Brain Cockpit
        </p>
        <h1 className="font-cal text-emphasis mt-2 text-3xl font-extrabold leading-[0.95] -tracking-[0.02em]">
          The cockpit hit an unexpected error.
        </h1>
        <p className="text-default mt-4 text-sm">
          <span className="text-subtle font-bold">Likely cause:</span> a bug in the app
          itself, not your pipeline or your notes.
        </p>
        <p className="text-default mt-2 text-sm">
          <span className="text-subtle font-bold">What to do:</span> reload the app. If it
          happens again, copy the technical details and file them.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="bg-inverted text-inverted min-h-11 rounded-xl px-5 text-sm font-bold"
          >
            Reload
          </button>
          <button
            type="button"
            onClick={this.copyDetails}
            className="border-emphasis text-emphasis min-h-11 rounded-xl border px-5 text-sm font-bold"
          >
            Copy technical details
          </button>
        </div>
      </main>
    );
  }
}
