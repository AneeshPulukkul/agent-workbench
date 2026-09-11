# Security Model

> Spec §10–§11 + Prompts 11–12. Zones Z1–Z5 (see `context.md`); every crossing: authN, authZ, validation, rate-limit, timeout, trace, audit.

## Identity & authZ

- OIDC (Entra ID in AKS; mock/local-dev identity mode with explicit `ENV=local` guard). Service identity: workload identity / ServiceAccount; MCP: OAuth user-delegated where needed + workload S2S; A2A: OAuth2 per Card.
- Scope-based: `case.read/write`, `case.approve`, `tools.read`, `tools.write.<name>`, `senior-operator` for prod `deployment.rollback`/`database.failover`. Tenant isolation: `tenant_id != resource.tenant_id → deny`; 404-vs-403 decided once (see `api-contracts.md`).
- MCP per-tool authZ inside server (never trust model text); A2A propagates principal without raw credentials.

## Policy (Python interface, OPA-ready — ADR-003)

```
side_effect==none → auto-allow (scoped)
external_write + case.write → allow + human approval
destructive → deny-by-default
prod + {rollback, failover} → senior-operator approval
tenant mismatch → deny
```
`PolicyDecision {allowed, requires_approval, reason, required_scopes[], max_impact?}`; span + immutable audit record per decision.

## Injection & agency defenses (§10.3)

Label content `system/user/tool/resource/agent`; instruction/data separation; injection detection on retrieved docs/tool/A2A outputs; tool allowlists + arg validation; no authority escalation from retrieved text; no secret retrieval via LLM; consequential actions need policy + human approval; context caps; evidence-required answers; `remediation.simulate` dry-run-first; never `model text → executable command`.

## Data protection & hardening

Redact/hash prompts/tool I/O; `sensitive` flag + `safe_for_ui` gate; hashes + redacted JSON in PG by default; input size limits, timeouts, rate limits, security headers, NetworkPolicies, non-root + RO fs + dropped caps, pinned lockfile + SBOM + signing + scanning, no secrets in images/code (`.env.example` placeholders). Threat model + security tests in W1-M5 (Prompt 11 checklist: SSRF, tool poisoning, excessive agency, deser, SQLi, CSRF, DoS, telemetry leakage, k8s RBAC).
