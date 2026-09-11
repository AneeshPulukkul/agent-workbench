import type { AgUiEvent } from "../lib/types";

export function FindingsPanel({ events }: { events: AgUiEvent[] }) {
  const findings = events.filter((e) => e.type === "STATE_SNAPSHOT");
  return (
    <section aria-label="findings" className="aow-card">
      <h2>Findings &amp; evidence</h2>
      {findings.length === 0 ? (
        <p className="aow-muted">No findings yet.</p>
      ) : (
        <ul className="aow-findings">
          {findings.map((e, i) => {
            const refs = Array.isArray(e.data.evidence_refs) ? (e.data.evidence_refs as string[]) : [];
            const confidence = typeof e.data.confidence === "number" ? e.data.confidence : null;
            const pct = confidence === null ? 0 : Math.round(Math.min(1, Math.max(0, confidence)) * 100);
            return (
              <li key={`${e.sequence}-${i}`} data-testid="finding-card" className="aow-finding-card">
                <strong>{String(e.data.title ?? "Finding")}</strong>
                <p>{String(e.data.summary ?? "")}</p>
                <p className="aow-muted">
                  confidence: {String(e.data.confidence ?? "n/a")}
                  {e.data.severity ? ` · severity: ${String(e.data.severity)}` : ""}
                </p>
                {confidence !== null && (
                  <div
                    className="aow-confidence"
                    role="progressbar"
                    aria-label={`confidence ${pct} percent`}
                    aria-valuenow={pct}
                    aria-valuemin={0}
                    aria-valuemax={100}
                  >
                    <span style={{ width: `${pct}%` }} />
                  </div>
                )}
                {refs.length > 0 && (
                  <ul className="aow-chips">
                    {refs.map((r) => (
                      <li key={r} className="aow-chip">
                        <a href={citationHref(r)} data-testid="evidence-link">
                          {r}
                        </a>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function citationHref(ref: string): string {
  // Citations link to evidence_refs; keep opaque refs navigable without leaking internals.
  if (/^https?:\/\//.test(ref)) return ref;
  return `#evidence/${encodeURIComponent(ref)}`;
}
