import type { AgUiEvent } from "../lib/types";

export function DelegationTimeline({ events }: { events: AgUiEvent[] }) {
  const acts = events.filter((e) => e.type === "ACTIVITY");
  return (
    <section aria-label="delegation-timeline" aria-live="polite" className="aow-card">
      <h2>A2A delegation</h2>
      {acts.length === 0 ? (
        <p className="aow-muted">No delegations yet.</p>
      ) : (
        <ul className="aow-timeline">
          {acts.map((e, i) => (
            <li key={`${e.sequence}-${i}`} data-testid="delegation-entry" className="aow-timeline-item">
              <code>{String(e.data.agent ?? "agent")}</code>
              {e.data.skill_id ? ` · ${String(e.data.skill_id)}` : ""}
              {e.data.task_id ? ` · task ${String(e.data.task_id)}` : ""} ·{" "}
              {String(e.data.status ?? "delegated")}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
