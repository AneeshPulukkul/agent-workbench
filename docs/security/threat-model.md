# Threat Model — Agent Operations Workbench

> Spec §10–§11 + Prompt 11 checklist: SSRF, tool poisoning, excessive agency,
> deserialization, SQLi, CSRF, DoS, telemetry leakage, k8s RBAC.
> Zones Z1–Z5 per `docs/architecture/context.md`; every crossing enforces
> authN/authZ, validation, rate-limit, timeout, trace, audit.

## 1. Assets & trust zones

| Asset | Zone | Impact if compromised |
|---|---|---|
| Run/event/approval/audit rows (PG) | Z2/Z5 | Cross-tenant data leak, forged approvals |
| MCP tool gateway (enterprise adapters) | Z3→Z4 | Unauthorized writes to prod systems |
| A2A specialist outputs | Z3 | Prompt injection → bad recommendation/action |
| Model I/O + traces | Z5 | Secret/telemetry leakage |
| Helm/AKS workloads | infra | Privilege escalation, lateral movement |

## 2. Threats & mitigations

### T1 Server-side request forgery (SSRF)
Mock adapters are local-only; enterprise migration allowlists egress hosts.
MCP adapters take typed args (no raw URLs from model text); `validate_tool_call`
rejects non-allowlisted tool names; NetworkPolicies deny pod egress except
DNS, PG, OTLP, and explicit enterprise endpoints.

### T2 Tool/result poisoning
All tool/A2A/runbook content is labeled `untrusted`; injection detection runs
on retrieved docs; retrieved text can never widen `packages/security/allowlist.py`
or scopes; `remediation.simulate` dry-run-first; evidence-required answers
(unsupported claims flagged by eval metric).

### T3 Excessive agency
Write/destructive tools require server-side policy eval (`apps/orchestrator/policies.py`)
+ persisted human `Approval` row; execution checks the approval row, not client
claims. `deployment.rollback`/`database.failover` in prod require
`senior-operator` scope. No auto-retry of irreversible tools; idempotency keys
on write path.

### T4 Deserialization / injection (SQLi, command injection)
Pydantic `extra="forbid"` everywhere; `ProposedAction.input` ≤50 keys/≤32KB
validated against tool JSON Schema — never `model text → executable command`.
SQLAlchemy parameterized queries only; no `exec/eval/subprocess` on model text.

### T5 CSRF
Bearer-token API (no cookies); `Idempotency-Key` on POST runs/decide;
MCP Streamable HTTP enforces Origin check (see migration doc).

### T6 DoS / cost exhaustion
`packages/security/limits.py`: objective ≤8KB, context ≤64KB, per-tenant token
bucket + global bucket, tool timeouts (read 15–30s, analysis/write 60s),
run budgets (max_tool_calls/model_calls/cost) → `budget_exceeded` envelope.
HPA + PDB per Helm chart; worker concurrency limits.

### T7 AuthN/authZ bypass & tenant escape
OIDC bearer (`packages/security/identity.py`); tenant from token only; every
row check via `check_tenant_access` → **404** on cross-tenant id access (no
existence leak), **403** on scope failures. Service identity via workload
identity/ServiceAccount in AKS; MCP per-tool authZ inside server; A2A
propagates principal without raw credentials.

### T8 Telemetry / audit leakage
`packages/security/redaction.py`: hashes + redacted JSON in PG by default;
`sensitive` payloads need `safe_for_ui=true` before UI display; bearer/PEM
patterns scrubbed; `OTEL_CONTENT_CAPTURE` empty by default.

### T9 k8s RBAC / supply chain
Least-privilege ServiceAccounts (no cluster role), non-root + readOnlyRootFilesystem
+ dropped caps (`NET_RAW` etc.), pinned lockfile + SBOM + image signing +
scanning (see `deploy/helm/SBOM-SCANNING.md`); no secrets in images/code.

## 3. Residual risks (see also `docs/operations/known-limitations.md`)
- Local JWT parsing defers signature verification to IdP/gateway (enterprise
  migration enables JWKS verification).
- In-process rate limiter is per-replica (ingress limit required in prod).
- FakeRuntime/mock adapters must never reach prod (CI guard + Helm `llm.mode`).
