---
name: review-action
version: 1.0.0
description: Review a proposed action for operational safety (advisory only).
contract_version: "1.0"
tool_version: 1.0.0
allowed-tools:
  - remediation.simulate
  - deployment.rollback
input-schema: ProposedAction@1.0
output-schema: Approval@1.0, ApprovalDecisionRequest@1.0
---

# Skill: review-action (v1.0.0 · contract 1.0 · tools 1.0.0)

> Generated view of versioned contracts. Source: `docs/prompts/action-review.md` + catalog.
> Pin: skill == contract == tool. Stale `schema_version` → `400 + ErrorEnvelope`
> (fetch the matching skill-pack release; mixed versions unsupported).

Pre-policy advisory feeding the change-risk agent / orchestrator correlate step.
Advisory only — allow/deny + approval routing decided by the policy service + human,
never by model text. (Source: `docs/prompts/action-review.md` §15.4.)

## Schema refs (import from contracts — never hand-copy)

- input: `ProposedAction@1.0` → packages/contracts/schemas/ProposedAction.1_0.json
- output: `Approval@1.0` → packages/contracts/schemas/Approval.1_0.json
- output: `ApprovalDecisionRequest@1.0` → packages/contracts/schemas/ApprovalDecisionRequest.1_0.json
- error: `ErrorEnvelope@1.0` → packages/contracts/schemas/ErrorEnvelope.1_0.json

Validate locally: `GET /v1/contracts/<name>/<version>/schema`
(e.g. `packages/contracts/schemas/ProposedAction.1_0.json`).
`schema_version` const on every body: `"1.0"`.

## Allowed tools (catalog v1.0.0)

| tool | category | timeout | approval | version |
|---|---|---|---|---|
| `remediation.simulate` | analysis | 60s | no | 1.0.0 |
| `deployment.rollback` | write | 60s | yes | 1.0.0 |

Write/destructive entries above are **propose-only**: server policy eval + persisted
human approval required; execution checks the Approval row, not client claims.
Prefer `remediation.simulate` / `dry_run=true` before any consequential proposal.

## Checklist

- [ ] is the action necessary? does evidence support it?
- [ ] blast radius? reversibility? preconditions? rollback plan?
- [ ] required authorization? dry-run available? lower-risk alternative?
- [ ] NEVER approve an action — advisory only (allow/deny + routing decided by policy service + human)
- [ ] return: risk_level, evidence_quality, preconditions, rollback_plan,
  safer_alternative, requires_human_approval, decision_recommendation

## Auth

```http
Authorization: Bearer <OIDC token>
```

Tenant comes from the token and is enforced on every row. Required scopes per tool
(`required_scopes` in catalog; e.g. `tools.read`, `tools.write.deployment.rollback`).
Approver scopes e.g. `case.approve` (senior-operator for prod rollback/failover).
Expiry enforced; double-decide → `409`.

## Worked example

```bash
BASE="${BASE_URL:-http://localhost:8080}"
# ProposedAction@1.0 is advisory — propose, never execute directly
curl -s "$BASE/v1/runs/<run_id>/approvals" -H "Authorization: Bearer $TOKEN"
# human decides; agent only polls the Approval row:
curl -s -X POST "$BASE/v1/runs/<run_id>/approvals/<approval_id>/decide" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: dec-001' \
  -d '{"decision":"approved","approver":"senior-operator@example.com",
       "schema_version":"1.0"}'
# dry-run first where supported: remediation.simulate / deployment.rollback dry_run=true
```

## Policy / approval expectations

- Read-only tools: auto-selectable within budget.
- Analysis tools: need policy permission.
- Write/destructive: proposal only → policy eval → human approve/reject → server executes.
- Budgets: max_tool_calls default 20 (1..100), max_model_calls default 10 (1..50), max_cost_usd default 2.0 (0..1000) (server-enforced; exceeded → `budget_exceeded`).
- Redaction: hashes + redacted JSON in audit; `sensitive` needs `safe_for_ui=true` for UI.

## Dependencies

HTTP + JSON only. No graph-library client import.
