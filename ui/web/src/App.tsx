import { useCallback, useEffect, useRef, useState } from "react";
import "./styles/theme.css";
import { useRunEvents } from "./hooks/useRunEvents";
import { ApiError, cancelRun, createCase, decideApproval, getRun } from "./lib/api";
import { ApprovalCard } from "./components/ApprovalCard";
import { AssistantStream } from "./components/AssistantStream";
import { CaseForm } from "./components/CaseForm";
import { DelegationTimeline } from "./components/DelegationTimeline";
import { FindingsPanel } from "./components/FindingsPanel";
import { ErrorBanner, RunHeader, UnknownEvents } from "./components/RunHeader";
import { ToolTimeline } from "./components/ToolTimeline";

export default function App() {
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const { agui, conn, error, lastSequence, reconnect } = useRunEvents(runId);
  const seenApprovals = useRef<Set<string>>(new Set());

  // Focus-move: when a new approval request arrives, move focus to the card.
  useEffect(() => {
    const ids = new Set<string>();
    for (const e of agui) {
      if (e.type !== "FRONTEND_INTERACTION") continue;
      const approvals = (e.data.approvals as { approval_id: string }[]) ?? [];
      for (const a of approvals) {
        if (a?.approval_id) ids.add(String(a.approval_id));
      }
    }
    const fresh = [...ids].filter((id) => !seenApprovals.current.has(id));
    seenApprovals.current = ids;
    if (fresh.length > 0) {
      const el = document.querySelector('[aria-label="approval-card"]') as HTMLElement | null;
      el?.focus?.();
    }
  }, [agui]);

  const handleCreate = useCallback(async (input: { title: string; objective: string; service: string }) => {
    setCreating(true);
    setNotice(null);
    try {
      const res = await createCase(input);
      setRunId(res.run_id);
      setStatus(res.status);
      try {
        const info = await getRun(res.run_id);
        setStatus(String(info.status ?? res.status));
      } catch {
        /* status stays as created */
      }
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setCreating(false);
    }
  }, []);

  const handleCancel = useCallback(async () => {
    if (!runId) return;
    setCancelling(true);
    try {
      const res = await cancelRun(runId);
      setStatus(res.status);
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setCancelling(false);
    }
  }, [runId]);

  const handleDecide = useCallback(
    async (
      approvalId: string,
      decision: "approved" | "rejected",
      opts?: { reason?: string | null; approver?: string },
    ) => {
      if (!runId) return;
      setDecidingId(approvalId);
      try {
        await decideApproval(runId, approvalId, decision, {
          approver: opts?.approver,
          reason: opts?.reason ?? null,
        });
        setNotice(`Approval ${approvalId} ${decision}`);
        try {
          const info = await getRun(runId);
          setStatus(String(info.status ?? status));
        } catch {
          /* ignore */
        }
      } catch (e) {
        if (e instanceof ApiError && (e.status === 409 || e.code === "approval_conflict")) {
          setNotice(`This approval was already decided — refreshing state. (${e.message})`);
          try {
            const info = await getRun(runId);
            setStatus(String(info.status ?? status));
          } catch {
            /* ignore */
          }
        } else {
          setNotice(e instanceof ApiError ? e.message : (e as Error).message);
        }
      } finally {
        setDecidingId(null);
      }
    },
    [runId, status],
  );

  return (
    <main className="aow-root">
      <RunHeader
        runId={runId}
        status={status}
        conn={conn}
        lastSequence={lastSequence}
        onCancel={handleCancel}
        onReconnect={reconnect}
        cancelling={cancelling}
      />
      {notice && (
        <p role="status" aria-live="polite" data-testid="notice" className="aow-notice">
          {notice}
        </p>
      )}
      <ErrorBanner error={error} onRetry={reconnect} />

      <div className="aow-layout">
        <div className="aow-sidebar">
          <section className="aow-card" aria-label="new-case">
            <h2>New case</h2>
            <CaseForm onCreate={handleCreate} busy={creating} />
          </section>
          <section className="aow-card" aria-label="run-state" aria-live="polite">
            <h2>Run state</h2>
            <p data-testid="agui-count">{agui.length} UI-safe events (seq {lastSequence})</p>
            <p className="aow-muted">
              Stream closes on <code>RUN_FINISHED / RUN_ERROR / RUN_CANCELLED</code>; refresh replays
              from persisted events via <code>?after_sequence=</code> + <code>Last-Event-ID</code>.
            </p>
          </section>
        </div>
        <div className="aow-main-grid">
          <AssistantStream events={agui} />
          <ApprovalCard events={agui} onDecide={handleDecide} decidingId={decidingId} />
          <ToolTimeline events={agui} />
          <DelegationTimeline events={agui} />
          <FindingsPanel events={agui} />
          <UnknownEvents events={agui} />
        </div>
      </div>
    </main>
  );
}
