import type { AgUiEvent } from "../lib/types";

export function ToolTimeline({ events }: { events: AgUiEvent[] }) {
  const tools = events.filter((e) => e.type === "TOOL_CALL_START" || e.type === "TOOL_CALL_FINISH");
  return (
    <section aria-label="tool-timeline" aria-live="polite" className="aow-card">
      <h2>Tool timeline</h2>
      {tools.length === 0 ? (
        <p className="aow-muted">No tool calls yet.</p>
      ) : (
        <ul className="aow-timeline">
          {tools.map((e, i) => (
            <li key={`${e.sequence}-${i}`} data-testid="tool-entry" className="aow-timeline-item">
              <code>{String(e.data.tool_name ?? "unknown")}</code> — {e.type}
              {e.data.status ? ` (${String(e.data.status)})` : ""}
              {typeof e.data.latency_s === "number" ? ` · ${e.data.latency_s}s` : ""}
              {e.data.error ? <span> · error: {String(e.data.error)}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
