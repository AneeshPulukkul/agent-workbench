// REST + SSE client. SSE honors Last-Event-ID with ?after_sequence= fallback;
// sequence is the source of truth for dedupe/ordering.

import type { CanonicalEvent, RunInfo } from "./types";

function resolveApiBase(): string {
  try {
    const v = (import.meta as unknown as { env?: Record<string, unknown> })?.env
      ?.VITE_API_BASE_URL;
    if (typeof v === "string" && v.length > 0) return v.replace(/\/+$/, "");
  } catch {
    /* ignore */
  }
  return "";
}
export const API_BASE = resolveApiBase();

export const SCHEMA_VERSION = "1.0";
export const DEFAULT_APPROVER = "ui-operator";

export function newIdempotencyKey(): string {
  try {
    const c = globalThis.crypto as unknown as { randomUUID?: () => string } | undefined;
    if (c?.randomUUID) return c.randomUUID();
  } catch {
    /* fall through */
  }
  return `idm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

const FRIENDLY_CONFLICT = "This approval was already decided — refreshing state.";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (e) {
    throw new ApiError("network_error", `Network error: ${(e as Error).message}`, true);
  }
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* ignore */
    }
    const retryable = res.status === 429 || res.status >= 500;
    const raw =
      (body as { message?: string })?.message ??
      (body as { error?: string })?.error ??
      (body as { detail?: { message?: string } })?.detail?.message ??
      (typeof (body as { detail?: unknown })?.detail === "string"
        ? ((body as { detail: string }).detail as string)
        : null) ??
      `Request failed (${res.status})`;
    const msg = res.status === 409 ? `${FRIENDLY_CONFLICT} (${String(raw)})` : String(raw);
    const code = res.status === 409 ? "approval_conflict" : `http_${res.status}`;
    throw new ApiError(code, msg, retryable, res.status);
  }
  return (await res.json()) as T;
}

export class ApiError extends Error {
  code: string;
  retryable: boolean;
  status?: number;
  constructor(code: string, message: string, retryable = false, status?: number) {
    super(message);
    this.code = code;
    this.retryable = retryable;
    this.status = status;
  }
}

export interface CreateRunResponse {
  run_id: string;
  status: string;
  stream_url: string;
  schema_version: string;
}

export async function createRun(
  input: {
    objective: string;
    context?: Record<string, unknown>;
    max_tool_calls?: number;
    max_model_calls?: number;
    max_cost_usd?: number;
  },
  opts?: { idempotencyKey?: string },
): Promise<CreateRunResponse> {
  return req<CreateRunResponse>("/v1/runs", {
    method: "POST",
    headers: { "Idempotency-Key": opts?.idempotencyKey ?? newIdempotencyKey() },
    body: JSON.stringify({ ...input, schema_version: SCHEMA_VERSION }),
  });
}

export async function createCase(
  input: {
    title: string;
    objective: string;
    service?: string;
  },
  opts?: { idempotencyKey?: string },
): Promise<CreateRunResponse> {
  const context: Record<string, unknown> = {};
  if (input.service) context.service = input.service;
  if (input.title) context.case_title = input.title;
  return createRun({ objective: input.objective, context }, opts);
}

export async function getRun(runId: string): Promise<RunInfo> {
  return req<RunInfo>(`/v1/runs/${encodeURIComponent(runId)}`);
}

export async function cancelRun(runId: string): Promise<{ run_id: string; status: string }> {
  return req(`/v1/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
}

export async function listApprovals(runId: string): Promise<{ run_id: string; approvals: unknown[] }> {
  return req(`/v1/runs/${encodeURIComponent(runId)}/approvals`);
}

export interface DecideOptions {
  approver?: string;
  reason?: string | null;
  idempotencyKey?: string;
}

export async function decideApproval(
  runId: string,
  approvalId: string,
  decision: "approved" | "rejected",
  reasonOrOpts?: string | null | DecideOptions,
  approverArg?: string,
): Promise<unknown> {
  let approver = DEFAULT_APPROVER;
  let reason: string | null = null;
  let idempotencyKey = newIdempotencyKey();
  if (typeof reasonOrOpts === "string" || reasonOrOpts === null || reasonOrOpts === undefined) {
    if (reasonOrOpts != null) reason = reasonOrOpts;
    if (approverArg) approver = approverArg;
  } else {
    if (reasonOrOpts.approver) approver = reasonOrOpts.approver;
    if (reasonOrOpts.reason !== undefined) reason = reasonOrOpts.reason;
    if (reasonOrOpts.idempotencyKey) idempotencyKey = reasonOrOpts.idempotencyKey;
    if (approverArg) approver = approverArg;
  }
  try {
    return await req(
      `/v1/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}/decide`,
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify({
          decision,
          approver,
          reason,
          schema_version: SCHEMA_VERSION,
        }),
      },
    );
  } catch (e) {
    if (e instanceof ApiError && e.status === 409 && !e.message.includes("already decided")) {
      throw new ApiError(e.code, `${FRIENDLY_CONFLICT} (${e.message})`, e.retryable, e.status);
    }
    throw e;
  }
}

export async function fetchEvents(
  runId: string,
  afterSequence = 0,
  signal?: AbortSignal,
): Promise<{ run_id: string; events: CanonicalEvent[] }> {
  return req(`/v1/runs/${encodeURIComponent(runId)}/events?after_sequence=${afterSequence}`, {
    signal,
  });
}

export function streamUrl(runId: string, afterSequence = 0): string {
  return `${API_BASE}/v1/runs/${encodeURIComponent(runId)}/events?after_sequence=${afterSequence}`;
}

export interface StreamHandlers {
  onEvent: (e: CanonicalEvent) => void;
  onError: (e: ApiError | Error) => void;
  onOpen?: () => void;
  onClose?: () => void;
}

/** SSE stream with Last-Event-ID + after_sequence reconnect. Returns a closer. */
export function streamRunEvents(
  runId: string,
  handlers: StreamHandlers,
  opts?: { afterSequence?: number; timeoutMs?: number },
): () => void {
  let closed = false;
  let es: EventSource | null = null;
  let lastSeq = opts?.afterSequence ?? 0;
  const timeoutMs = opts?.timeoutMs ?? 45000;
  let timeout: ReturnType<typeof setTimeout> | null = null;

  const armTimeout = () => {
    if (timeout) clearTimeout(timeout);
    timeout = setTimeout(() => {
      if (!closed) handlers.onError(new ApiError("stream_timeout", "Stream timed out — reconnecting", true));
      reconnect();
    }, timeoutMs);
  };

  const connect = () => {
    if (closed) return;
    try {
      es = new EventSource(streamUrl(runId, lastSeq));
    } catch (e) {
      handlers.onError(e as Error);
      return;
    }
    handlers.onOpen?.();
    armTimeout();
    es.onmessage = (msg: MessageEvent) => {
      armTimeout();
      // Last-Event-ID fallback: server sends `id` == sequence.
      const idSeq = msg.lastEventId ? Number(msg.lastEventId) : NaN;
      try {
        const parsed = JSON.parse(msg.data) as CanonicalEvent | { events?: CanonicalEvent[] };
        const batch: CanonicalEvent[] = Array.isArray((parsed as { events?: unknown }).events)
          ? ((parsed as { events: CanonicalEvent[] }).events as CanonicalEvent[])
          : [parsed as CanonicalEvent];
        for (const e of batch) {
          const seq = typeof e.sequence === "number" && !Number.isNaN(e.sequence) ? e.sequence : idSeq;
          if (!Number.isNaN(seq) && seq > 0) lastSeq = Math.max(lastSeq, seq);
          // Tolerate malformed envelopes — skip, don't crash the stream.
          if (e && typeof e === "object" && "type" in e) handlers.onEvent(e as CanonicalEvent);
        }
      } catch (e) {
        handlers.onError(e as Error);
      }
    };
    es.onerror = () => {
      if (timeout) clearTimeout(timeout);
      if (!closed) {
        handlers.onError(new ApiError("stream_error", "Stream disconnected — reconnecting", true));
        reconnect();
      }
    };
  };

  let backoff = 500;
  const reconnect = () => {
    try {
      es?.close();
    } catch {
      /* ignore */
    }
    es = null;
    if (closed) return;
    // Catch-up from persisted events, then re-open live stream.
    // Backoff capped at 8s to stay responsive without hammering the server.
    const delay = Math.min(backoff, 8000);
    backoff = Math.min(backoff * 2, 8000);
    setTimeout(async () => {
      if (closed) return;
      try {
        const snap = await fetchEvents(runId, lastSeq);
        for (const e of snap.events ?? []) {
          if (e.sequence > lastSeq) {
            lastSeq = e.sequence;
            handlers.onEvent(e);
          }
        }
        backoff = 500;
      } catch (e) {
        handlers.onError(e as Error);
      }
      connect();
    }, delay);
  };

  connect();
  return () => {
    closed = true;
    if (timeout) clearTimeout(timeout);
    try {
      es?.close();
    } catch {
      /* ignore */
    }
    handlers.onClose?.();
  };
}
