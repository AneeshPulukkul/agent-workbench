# ADR-003: Python Policy Interface (OPA-ready)

- Status: Accepted. Date: 2026-09-11. Spec §2.2, §10.2, Prompt 11.
- Context: Need policy eval on every write/destructive proposal + approval routing, with path to OPA/Rego or Cedar without rewriting orchestrator.
- Decision: `PolicyDecision{allowed, requires_approval, reason, required_scopes[], max_impact?}` via Python interface mirroring Rego rules in §10.2 (deny-by-default destructive, senior-approval prod rollback/failover, tenant-match deny). Every decision spanned + audit-persisted. OPA sidecar is a future drop-in behind the same interface.
- Alternatives: OPA sidecar day-1 (rejected: ops weight for W1), inline if-statements (rejected: untestable, not portable), Cedar-only (rejected: team familiarity).
- Consequences: + shippable W1, testable matrix; − must keep rule shape Rego-translatable (no Python-only tricks) and record rule version per decision.
