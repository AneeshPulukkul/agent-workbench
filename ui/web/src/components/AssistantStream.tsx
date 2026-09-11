import type { AgUiEvent } from "../lib/types";

export function AssistantStream({ events }: { events: AgUiEvent[] }) {
  const chunks = events.filter((e) => e.type === "TEXT_MESSAGE_CONTENT");
  const text = chunks
    .map((e) => String((e.data.delta as string) ?? ""))
    .filter(Boolean)
    .join("\n");
  return (
    <section aria-label="assistant-stream" aria-live="polite" className="aow-card">
      <h2>Assistant</h2>
      {chunks.length === 0 ? (
        <p data-testid="stream-empty" className="aow-muted">Waiting for assistant output…</p>
      ) : (
        <div data-testid="stream-text" className="aow-stream-text">
          {text}
        </div>
      )}
    </section>
  );
}
