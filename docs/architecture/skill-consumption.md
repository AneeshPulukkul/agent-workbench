# Skill Consumption — Managed vs Unmanaged

> Locked dual-consumption decision. Skills are **generated views** of versioned contracts; server enforces policy/approval/durability. No `langgraph` client-side for skill-pack users.

## A. Managed runtime (default)

UI/Gateway + `LangGraphRuntime implements AgentRuntime` + LiteLLM router (+ FakeRuntime local/test). Full durability, budgets, policy, approvals, replay, OTel. For product UI and backend-owned automation.

## B. Portable skill-pack (unmanaged / BYO-agent)

Artifact: `AGENTS.md` (repo operating contract: endpoints, auth, budgets, approval flow, redaction rules) + `skills/*/SKILL.md` (one per capability, e.g. `investigate-incident`, `correlate-symptoms`, `propose-mitigation`, `review-action`). Each SKILL.md contains: purpose, HTTP calls against **same** Gateway/MCP/A2A APIs, versioned JSON I/O (copied from contract schemas), auth snippet, policy/approval expectations, worked `curl` example. Dependencies: HTTP + JSON only.

Example layout (generated, not hand-written):
```
skill-pack/
├── AGENTS.md
└── skills/
    ├── investigate-incident/SKILL.md   # pins runs v1, events v1
    ├── correlate-symptoms/SKILL.md     # pins a2a task v1 + observability skill v0.1.0
    ├── propose-mitigation/SKILL.md     # pins ProposedAction v1 + rollback tool vX
    └── review-action/SKILL.md          # pins change-risk skill v0.1.0
```

## Version-pin rule (hard)

```
skill == contract == tool
```
SKILL.md front-matter pins exact `contract_version` + `tool_version` (+ `agent-card version` for A2A skills). Generator reads `GET /v1/contracts/*` schemas + MCP `ToolMetadata`/A2A Cards; CI fails on drift (contract test). Consumers pin one skill-pack release; mixed versions unsupported. Server rejects stale `schema_version` with `400 + ErrorEnvelope{UPGRADE_REQUIRED}` pointing at new pack.

## Enforcement boundary

Client (either mode) NEVER self-authorizes: write/destructive tools require server policy eval + persisted human approval row; execution checks approval, not client claims. Budgets, idempotency, audit, redaction, trace all server-side. Skill-pack agents get same guarantees by calling the same endpoints.

## Generation & release

`make skill-pack` (W2+): dump schemas → render templates → output versioned `skill-pack/{version}/`. Release together with Gateway image; changelog lists contract bumps. W1: this doc + contract-version fields only (no generator code — docs-only phase).
