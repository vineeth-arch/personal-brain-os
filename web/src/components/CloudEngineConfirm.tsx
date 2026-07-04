// The cloud-transcription caution + confirm step, shared by Settings and
// Integrations so the wording can never drift between the two screens.
// The rule it protects: sensitive audio stays on this machine; the cloud
// engine is a deliberate, confirmed fallback — never a silent default.
export function CloudEngineConfirm({
  busy,
  onConfirm,
  onCancel,
}: {
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="w-full">
      <div className="bg-cal-muted border-emphasis rounded-xl border p-3 text-sm">
        <p className="text-emphasis font-bold">Cloud transcription sends your audio to OpenAI.</p>
        <p className="text-default mt-1">
          Your rule: sensitive data stays local. Use as fallback only.
        </p>
      </div>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={onConfirm}
          disabled={busy}
          className="bg-inverted text-inverted min-h-11 rounded-xl px-4 text-sm font-bold disabled:opacity-60"
        >
          Use cloud anyway
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="border-subtle text-subtle min-h-11 rounded-xl border px-4 text-sm font-bold"
        >
          Keep local
        </button>
      </div>
    </div>
  );
}
