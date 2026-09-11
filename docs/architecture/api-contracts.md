# API Contracts (Gateway REST + SSE)

> Spec §4, §8.3. Base: `/v1`. Auth: OIDC bearer (mock identity local); tenant from token, enforced on every row. Versioning: URL major + `schema_version` in bodies; additive changes only within major; breaking → `/v2` + migration note.

## Runs

### POST /v1/runs — create investigation run
Request (`AgentRequest`, abridged):
```json
{"objective":"Investigate elevated checkout API error rate","context":{"service":"checkout-api"},"max_tool_calls":20,"max_model_calls":10,"max_cost_usd":2.0,"schema_version":"1.0"}
```
Responses: `202 {run_id, status:"created", stream_url:"/v1/runs/{id}/events", schema_version}`. Errors: `400` validation, `401/403` auth, `429` rate-limit — all `ErrorEnvelope`.

### GET /v1/runs/{run_id} — status + result refs
`200 {run_id, status, current_state, findings:[...], proposed_actions:[...], approvals:[...], budgets:{...}, trace_id}`. Tenant mismatch → `404` (avoid existence leak) or `403` per policy; pick one and document.

### POST /v1/runs/{run_id}/cancel — cancel
`202 {run_id, status:"cancelled"}`; idempotent; in-flight tool/A2A tasks cancelled where possible, irreversible tools never auto-retried.

## Events / replay (§8.3)

### GET /v1/runs/{run_id}/events?after_sequence=42 (SSE)
- Persisted events `sequence > after` in order, then live stream; terminal `run.completed/failed` event closes.
- Headers: `Last-Event-ID` honored as fallback to `after_sequence`; `sequence` is source of truth for dedupe/ordering.
- Access check per tenant/run; `sensitive=true` payloads redacted unless `safe_for_ui=true`.
- Canonical `AgentEvent` inside; AG-UI mapping applied by adapter (see `agui-mapping.md`).

## Approvals

- `GET /v1/runs/{id}/approvals` → pending + decided list.
- `POST /v1/runs/{id}/approvals/{approval_id}/decide {decision:"approved"|"rejected", reason?}` → `200 Approval`; only authorized approver scopes (e.g. `case.approve`, senior-operator for prod rollback/failover); expiry enforced; double-decide → `409`.

## Health / ops

`GET /health` (liveness), `GET /ready` (readiness incl. PG + downstream checks), `GET /v1/openapi.json`, `GET /v1/contracts/{name}/{version}/schema` (serves Pydantic JSON Schemas — skill-pack generator source of truth).

## Conventions

Idempotency: `Idempotency-Key` header on POST runs/decide + write-tool path; pagination: `limit/cursor`; limits: objective ≤8KB, context ≤64KB; errors always `ErrorEnvelope {code, message, run_id?, retryable}`; `traceparent` in/out; `X-Request-ID` echoed.
