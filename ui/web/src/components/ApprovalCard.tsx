import { useState } from "react";
import type { AgUiEvent } from "../lib/types";
import { DEFAULT_APPROVER } from "../lib/api";

interface Action {
  action_id: string;
  tool_name: string;
  reason?: string;
  risk?: string;
  rollback?: string | null;
  dry_run?: boolean;
}

interface ApprovalRef {
  approval_id: string;
  action_id: string;
  decision: string;
}

export type DecideHandler = (
  approvalId: string,
  decision: "approved" | "rejected",
  opts?: { reason?: string | null; approver?: string },
) => void;

export function ApprovalCard({
  events,
  onDecide,
  busy,
  decidingId = null,
}: {
  events: AgUiEvent[];
  onDecide: DecideHandler;
  busy?: boolean;
  decidingId?: string | null;
}) {
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [approver, setApprover] = useState(DEFAULT_APPROVER);
  const cards = events.filter((e) => e.type === "FRONTEND_INTERACTION");
  const decisions = events.filter((e) => e.type === "STATE_UPDATE");
  if (cards.length === 0) return null;

  const decisionByApproval = new Map<string, AgUiEvent>();
  for (const d of decisions) {
    const id = String(d.data.approval_id ?? "");
    if (id && !decisionByApproval.has(id)) decisionByApproval.set(id, d);
  }

  const setReason = (id: string, v: string) =>
    setReasons((p) => ({ ...p, [id]: v }));

  return (
    <section aria-label="approval-card" className="aow-card aow-approval" tabIndex={-1}>
      <h2>Proposed actions — approval required ({cards.length})</h2>
      <label className="aow-field">
        Approver
        <input
          aria-label="approval-approver"
          data-testid="approval-approver"
          className="aow-input"
          value={approver}
          onChange={(e) => setApprover(e.target.value)}
          placeholder="oncall@example.com"
        />
      </label>
      {cards.map((card, ci) => {
        const actions = (card.data.actions as Action[]) ?? [];
        const approvals = (card.data.approvals as ApprovalRef[]) ?? [];
        return (
          <div key={`${card.sequence}-${ci}`} data-testid="approval-request">
            <p className="aow-muted">
              Request seq {card.sequence}
              {ci < cards.length - 1 ? " · superseded" : " · latest"}
            </p>
            {actions.map((a) => {
              const ap = approvals.find((x) => x.action_id === a.action_id);
              const audit = ap ? decisionByApproval.get(ap.approval_id) : undefined;
              const decided = audit
                ? String(audit.data.decision ?? "")
                : ap && ap.decision !== "pending"
                  ? String(ap.decision)
                  : "";
              const isDeciding = decidingId === ap?.approval_id;
              const locked = Boolean(busy) || decidingId !== null;
              return (
                <div key={a.action_id} data-testid="approval-action" className="aow-finding-card">
                  <p>
                    <code>{a.tool_name}</code> · risk: {a.risk ?? "unknown"}
                    {a.dry_run ? " · dry-run" : ""}
                  </p>
                  {a.reason && <p>Reason: {a.reason}</p>}
                  {a.rollback && <p>Rollback: {a.rollback}</p>}
                  {decided ? (
                    <p data-testid="approval-decision" className="aow-muted">
                      Decision: {decided}
                      {audit?.data.approver ? ` by ${String(audit.data.approver)}` : ""}
                      {` (seq ${audit ? audit.sequence : card.sequence})`}
                    </p>
                  ) : (
                    ap && (
                      <>
                        <label className="aow-field">
                          Decision reason
                          <input
                            aria-label={`reason-${ap.approval_id}`}
                            data-testid="approval-reason"
                            className="aow-input"
                            value={reasons[ap.approval_id] ?? ""}
                            onChange={(e) => setReason(ap.approval_id, e.target.value)}
                            placeholder="Why approve or reject?"
                          />
                        </label>
                        <div className="aow-approval-actions">
                          <button
                            className="aow-btn aow-btn--approve"
                            disabled={locked}
                            onClick={() =>
                              onDecide(ap.approval_id, "approved", {
                                reason: (reasons[ap.approval_id] ?? "").trim() || null,
                                approver: approver.trim() || DEFAULT_APPROVER,
                              })
                            }
                          >
                            {isDeciding ? "Approving…" : "Approve"}
                          </button>
                          <button
                            className="aow-btn aow-btn--reject"
                            disabled={locked}
                            onClick={() =>
                              onDecide(ap.approval_id, "rejected", {
                                reason: (reasons[ap.approval_id] ?? "").trim() || null,
                                approver: approver.trim() || DEFAULT_APPROVER,
                              })
                            }
                          >
                            {isDeciding ? "Rejecting…" : "Reject"}
                          </button>
                          <span data-testid="approval-id" className="aow-chip">
                            {ap.approval_id}
                          </span>
                        </div>
                      </>
                    )
                  )}
                </div>
              );
            })}
          </div>
        );
      })}
      {decisions.length > 0 && (
        <div className="aow-muted" aria-label="approval-audit">
          <h3>Audit trail ({decisions.length})</h3>
          <ul>
            {decisions.map((d, i) => (
              <li key={`${d.sequence}-${i}`} data-testid="approval-decision">
                {String(d.data.approval_id ?? "approval")} · {String(d.data.decision ?? "decided")}
                {d.data.approver ? ` by ${String(d.data.approver)}` : ""} · seq {d.sequence}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
