// AG-UI adapter: mirrors packages/protocols/ag_ui.py in TypeScript.
// Rules: only render safe_for_ui && !sensitive; unknown types -> UNKNOWN generic.

import type { AgUiEvent, CanonicalEvent } from "./types";

const MAP: Record<string, AgUiEvent["type"]> = {
  "run.started": "RUN_STARTED",
  "message.delta": "TEXT_MESSAGE_CONTENT",
  "tool.started": "TOOL_CALL_START",
  "tool.completed": "TOOL_CALL_FINISH",
  "agent.delegated": "ACTIVITY",
  "finding.created": "STATE_SNAPSHOT",
  "approval.required": "FRONTEND_INTERACTION",
  "approval.received": "STATE_UPDATE",
  "run.completed": "RUN_FINISHED",
  "run.failed": "RUN_ERROR",
  "run.cancelled": "RUN_CANCELLED",
};

export const TERMINAL_TYPES = new Set(["RUN_FINISHED", "RUN_ERROR", "RUN_CANCELLED"]);

export function isRenderable(e: CanonicalEvent): boolean {
  if (e.sensitive === true) return false;
  return e.safe_for_ui === true;
}

function dataOf(e: CanonicalEvent): Record<string, unknown> {
  return (e.data ?? {}) as Record<string, unknown>;
}

export function toAgUi(e: CanonicalEvent): AgUiEvent | null {
  if (!isRenderable(e)) return null;
  const canonical = String(e.type ?? "");
  const type = MAP[canonical] ?? "UNKNOWN";
  const data = dataOf(e);
  const base: AgUiEvent = {
    type,
    sequence: e.sequence,
    event_id: e.event_id,
    run_id: e.run_id,
    timestamp: e.timestamp,
    trace_id: e.trace_id ?? null,
    data: {},
  };
  switch (type) {
    case "RUN_STARTED":
      base.data = { run_id: e.run_id, ...data };
      break;
    case "TEXT_MESSAGE_CONTENT": {
      const d = data as Record<string, unknown>;
      const delta =
        (d.delta as string) ||
        (d.text as string) ||
        (d.clarification_question as string) ||
        (d.more_info as string) ||
        (d.policy_denial as string) ||
        "";
      base.data = { delta, raw: data };
      break;
    }
    case "TOOL_CALL_START":
    case "TOOL_CALL_FINISH": {
      const d = data as Record<string, unknown>;
      base.data = {
        tool_name: d.tool_name,
        status: d.status,
        action_id: d.action_id,
        latency_s: d.latency_s,
        error: d.error,
        args: d.args ?? {},
      };
      break;
    }
    case "ACTIVITY":
      base.data = {
        agent: data.agent,
        skill_id: data.skill_id,
        task_id: data.task_id,
        status: (data.status as string) ?? "delegated",
      };
      break;
    case "STATE_SNAPSHOT": {
      const f = ((data.finding ?? data) as Record<string, unknown>) ?? {};
      base.data = {
        kind: "finding",
        title: f.title,
        summary: f.summary,
        confidence: f.confidence,
        severity: f.severity,
        evidence_refs: f.evidence_refs ?? [],
        finding_id: f.finding_id,
      };
      break;
    }
    case "FRONTEND_INTERACTION":
      base.data = { kind: "approval", actions: data.actions ?? [], approvals: data.approvals ?? [] };
      break;
    case "STATE_UPDATE":
      base.data = { decision: data.decision, approver: data.approver, approval_id: data.approval_id };
      break;
    case "RUN_FINISHED":
    case "RUN_ERROR":
    case "RUN_CANCELLED":
      base.data = data;
      break;
    default:
      base.data = { canonical_type: canonical, payload: data };
      break;
  }
  return base;
}

export function batchToAgUi(events: CanonicalEvent[]): AgUiEvent[] {
  const out: AgUiEvent[] = [];
  for (const e of events) {
    const m = toAgUi(e);
    if (m) out.push(m);
  }
  // Deterministic order for replay.
  out.sort((a, b) => a.sequence - b.sequence);
  return out;
}

export function isTerminal(e: AgUiEvent): boolean {
  return TERMINAL_TYPES.has(e.type);
}
