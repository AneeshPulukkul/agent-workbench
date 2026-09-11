# Migration Path: Mock → Enterprise

Goal: swap mock adapters for enterprise systems **without orchestrator changes**
(DoD gate §16). The seam is versioned contracts + adapter interfaces.

## M1 Identity: mock → Entra ID (OIDC) + workload identity
- Set `APP_ENV=production`, `AUTH_MODE=oidc`, `OIDC_ISSUER_URL`, `OIDC_AUDIENCE`
  in `deploy/helm/api/values.yaml` (+ worker/mcp/a2a).
- Enable JWKS signature verification at ingress/gateway (in-process parsing in
  `packages/security/identity.py` already enforces iss/aud/exp; add JWKS fetch
  + `kid` check as a drop-in inside `parse_bearer_identity`).
- Bind `ServiceAccount` to workload identity annotations
  (`azure.workload.identity/client-id`) — charts already use per-service accounts.
- MCP: add OAuth user-delegated flow where needed + workload S2S; A2A: OAuth2
  per Agent Card (`AgentAuthentication` in contracts).

## M2 MCP adapters: mock → enterprise
- Implement one adapter per tool behind the existing `HANDLERS` interface
  (`apps/mcp_server/executor.py`): same `ToolMetadata` (name/version/timeout/
  scopes), same `ToolInvocation` audit shape.
- Enterprise allowlist: egress hosts in NetworkPolicy `egressTo` + adapter-level
  URL allowlist (SSRF guard); typed args only, never raw model URLs.
- Keep `dry_run` semantics; `deployment.rollback` stays approval-gated with
  idempotency keys. Contract tests (`tests/contract/test_mcp_server.py`) must
  pass unchanged against the new adapter (authZ-failure cases included).

## M3 Persistence: in-memory/PG-local → Azure DB for PG
- Charts already externalize `DATABASE_URL` via `agent-*-secrets`; point at
  Azure DB for PG, run `alembic upgrade head`.
- Replace `InMemoryRunQueue` with PG polling
  (`SELECT ... FOR UPDATE SKIP LOCKED` on runs); replay/dedupe semantics
  (`(run_id, sequence)` unique) unchanged.

## M4 Models: FakeRuntime → LiteLLM router
- `LLM_MODE=router`, `LITELLM_MODEL` + provider keys via KeyVault CSI;
  budgets/circuit-breakers unchanged; cost dashboards validate burn.

## M5 Observability: local OTLP → Azure Monitor
- OTel Collector exporter option to Azure Monitor; Grafana dashboards
  (`run-overview`, `cost`, `slo`, `security`) re-point at the prod Prometheus.

## Verification per step
`make test` → contract/integration green → eval green → §16 DoD walkthrough →
`helm upgrade` per chart. Roll back with `helm rollback <release>`.
