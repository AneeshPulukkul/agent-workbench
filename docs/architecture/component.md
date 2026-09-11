# Component Diagram — Agent Gateway + Orchestrator (C4 L3)

> Spec §2.1, §5. Bounded graph; `AgentRuntime` Protocol; server-side policy/approval/durability.

## API (apps/api)

```mermaid
flowchart TB
  subgraph API["agent-api (FastAPI)"]
    MW[AuthN/Z + TenantCtx + RateLimit + Redaction] --> Runs[Run API: POST /v1/runs, GET run, POST cancel]
    Runs --> AGUI[AG-UI Adapter: canonical→AG-UI map, SSE serialize]
    AGUI --> Stream[Event Stream: GET /v1/runs/:id/events]
    Runs --> Appr[Approval API: list/decide]
    Runs --> RepoI[Repository interfaces]
    Appr --> RepoI
    Stream --> RepoI
  end
  RepoI --> PG[(PostgreSQL)]
  Runs --> Q[Dispatch to worker]
```

## Orchestrator / Worker (apps/orchestrator + apps/worker)

```mermaid
flowchart TB
  subgraph ORCH["agent-worker"]
    G[LangGraphRuntime: AgentRuntime Protocol] --> Bud[BudgetTracker: model/tool/delegation/time/cost]
    G --> Router[LiteLLM Router: fast/reasoning/small + FakeRuntime]
    G --> Val[Structured-output validation: Pydantic/JSON Schema]
    G --> MCPc[MCP Client: read/analysis/write dispatch]
    G --> A2Ac[A2A Client: delegate, timeout, fallback, schema-validate]
    G --> Pol[Policy Client: Python PolicyDecision interface]
    G --> Evt[Event Service: persist-then-publish, seq, replay]
    G --> Cancel[Cancel/timeout/circuit-breaker]
  end
  MCPc --> MCPGW[MCP Tool Gateway]
  A2Ac --> A2A[A2A specialists]
  Pol --> PolSvc[Policy Service: Python module, OPA-ready]
  Evt --> PG[(PostgreSQL: runs/run_events/...)]
  Router --> LLM[(Models)]
```

## MCP server (apps/mcp_server)

Components: `Transport (Streamable HTTP + auth)` → `Tool Registry (metadata: category/side_effect/idempotent/timeout/scopes/approval/dry_run/owner/version)` → `Read tools` / `Analysis tools` / `Write+Destructive tools (allowlist, idempotency, dry-run)` → `Resources (services/runbooks)` → `Prompts (investigate-incident, summarize-evidence, prepare-change-review)` → `Enterprise adapters (mock local)`; cross-cutting: tenant/resource authZ, audit records, OTel spans, redaction.

## Packages (shared)

`packages/contracts` (versioned Pydantic, JSON Schema), `packages/protocols` (ag_ui, mcp_client, a2a_client), `packages/llm` (client/router/cost), `packages/security` (identity/authorization/redaction), `packages/persistence` (models/repos/migrations), `packages/telemetry` (tracing/metrics/conventions adapter).

## Bounded workflow states (§5.1)

`classify → plan → retrieve context → read-only diagnostics → A2A delegate → correlate → recommend → policy eval → approval gate → execute approved → verify → complete`, with branches: insufficient context→clarify; low confidence→more info; denied→explain; rejected→complete-with-rejection. Persist + emit event on every transition; enforce max duration/model-calls/tool-calls/delegation-depth/tokens; retry safe+idempotent only, never auto-retry irreversible tools.
