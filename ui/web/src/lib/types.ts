// Canonical + AG-UI types shared by the workbench client.

export type CanonicalEventType =
  | "run.started"
  | "message.delta"
  | "tool.started"
  | "tool.completed"
  | "agent.delegated"
  | "finding.created"
  | "approval.required"
  | "approval.received"
  | "run.completed"
  | "run.failed"
  | "run.cancelled"
  | (string & {});

export interface CanonicalEvent {
  schema_version: string;
  event_id: string;
  run_id: string;
  tenant_id?: string;
  sequence: number;
  type: CanonicalEventType;
  timestamp: string;
  data: Record<string, unknown>;
  trace_id?: string | null;
  correlation_id?: string | null;
  sensitive?: boolean;
  safe_for_ui?: boolean;
}

export type AgUiType =
  | "RUN_STARTED"
  | "TEXT_MESSAGE_CONTENT"
  | "TOOL_CALL_START"
  | "TOOL_CALL_FINISH"
  | "ACTIVITY"
  | "STATE_SNAPSHOT"
  | "FRONTEND_INTERACTION"
  | "STATE_UPDATE"
  | "RUN_FINISHED"
  | "RUN_ERROR"
  | "RUN_CANCELLED"
  | "UNKNOWN";

export interface AgUiEvent {
  type: AgUiType;
  sequence: number;
  event_id?: string;
  run_id?: string;
  timestamp?: string;
  trace_id?: string | null;
  data: Record<string, unknown>;
}

export interface Finding {
  finding_id?: string;
  title: string;
  summary: string;
  confidence?: number;
  severity?: string;
  evidence_refs?: string[];
}

export interface ProposedAction {
  action_id: string;
  tool_name: string;
  reason?: string;
  risk?: string;
  rollback?: string | null;
  dry_run?: boolean;
  input?: Record<string, unknown>;
}

export interface Approval {
  approval_id: string;
  action_id: string;
  decision: string;
  requested_by?: string;
  approver?: string | null;
}

export type RunStatus =
  | "created"
  | "running"
  | "waiting_for_approval"
  | "completed"
  | "failed"
  | "cancelled";

export interface RunInfo {
  run_id: string;
  status: RunStatus | string;
  current_state?: string | null;
  findings?: Finding[];
  proposed_actions?: ProposedAction[];
  approvals?: Approval[];
  budgets?: Record<string, unknown>;
  trace_id?: string | null;
}
