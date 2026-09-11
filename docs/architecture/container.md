# Container Diagram — Agent Operations Workbench (C4 L2)

> Spec §2–§3, §11. Locked: FastAPI Gateway + worker, MCP server (Streamable HTTP), A2A specialists, PostgreSQL, React+TS UI, OTel Collector, Docker Compose local / Helm AKS.

```mermaid
C4Container
  title Container Diagram — Agent Operations Workbench
  Person(analyst, "Analyst", "Browser")
  Container(ui, "Web UI", "React+TS, AG-UI client", "Streams runs, cards, approvals, replay")
  Container(api, "Agent Gateway / API", "Python 3.12, FastAPI", "Auth, tenant ctx, rate limit, Run API, AG-UI adapter, SSE")
  Container(worker, "Agent Worker / Orchestrator", "Python, LangGraphRuntime via AgentRuntime", "Bounded graph, MCP+A2A clients, policy checks, budgets")
  Container(mcp, "MCP Tool Gateway", "Python MCP SDK, Streamable HTTP", "Tool registry, resources, prompts; OAuth/workload auth")
  Container(a2a1, "Observability Agent", "Python, A2A", "correlate-service-symptoms")
  Container(a2a2, "Knowledge Agent", "Python, A2A", "runbook/knowledge synthesis")
  Container(a2a3, "Change-Risk Agent", "Python, A2A", "risk scoring")
  Container(a2a4, "Remediation Agent", "Python, A2A", "simulate/plan only; never executes")
  ContainerDb(pg, "PostgreSQL", "Postgres 16", "runs, events, tasks, tools, approvals, audit, eval")
  Container(otel, "OTel Collector", "OpenTelemetry", "Traces/metrics/logs pipeline")
  System_Ext(idp, "OIDC IdP", "Entra ID / mock")
  System_Ext(llm, "Models via LiteLLM", "Router + FakeRuntime fallback")
  System_Ext(ent, "Enterprise (mock adapters)", "Metrics/logs/CMDB/tickets/cloud")

  Rel(analyst, ui, "HTTPS")
  Rel(ui, api, "AG-UI events/commands (SSE)")
  Rel(api, pg, "R/W runs+events")
  Rel(api, worker, "Enqueue/dispatch run")
  Rel(worker, pg, "Durable state, idempotent append")
  Rel(worker, mcp, "MCP tools/resources (HTTP)")
  Rel(worker, a2a1, "A2A delegation")
  Rel(worker, a2a2, "A2A delegation")
  Rel(worker, a2a3, "A2A delegation")
  Rel(worker, a2a4, "A2A delegation")
  Rel(mcp, ent, "Adapter calls (mock local)")
  Rel(a2a1, mcp, "Read-only tools only")
  Rel(api, idp, "OIDC")
  Rel(worker, llm, "LiteLLM router")
  Rel(api, otel, "OTLP")
  Rel(worker, otel, "OTLP")
  Rel(mcp, otel, "OTLP")
```

## Containers & responsibilities (§3.1)

| Container / image | Code boundary | Scales | Notes |
|---|---|---|---|
| `agent-api` (FastAPI) | `apps/api` — auth, run creation, SSE stream, AG-UI map, approval endpoints | HPA independent | Stateless; durable state in PG |
| `agent-worker` | `apps/orchestrator` + `apps/worker` — graph, budgets, MCP/A2A clients, policy, event publish | HPA + concurrency limits | Persist after every transition; idempotency keys |
| `agent-mcp-server` | `apps/mcp_server` — tools/resources/prompts, tenant+scope authZ | HPA independent | Streamable HTTP + Origin check + OAuth/workload; read vs write split |
| `agent-observability-agent`, `agent-knowledge-agent` (+ change-risk, remediation) | `apps/a2a_agents/*` | HPA / scale-to-zero optional | Agent Card + versioned I/O; deterministic local mode |
| `agent-ui` | `ui/web` | CDN/static | Never coupled to orchestrator internals; tolerates unknown events |
| `postgres` | `packages/persistence` migrations | StatefulSet / Azure DB for PG in prod | Unique `(run_id, sequence)`; hashes + redacted payloads |
| `otel-collector` + jaeger/prom/grafana | `deploy/otel-collector` | infra | W3C propagation; GenAI attrs behind adapter |

## Deployments

- **Local:** Docker Compose services `api, worker, mcp-server, observability-agent, postgres, otel-collector, jaeger, prometheus, grafana, ui` (§11.3), all on mock adapters + FakeRuntime.
- **AKS:** Helm chart per service (Deployment/Service/HPA/PDB/ServiceAccount/NetworkPolicy/ConfigMap/Secret/liveness+readiness/resources per §11.2), hardened images (non-root, RO fs, dropped caps, SBOM, signed, scanned).

## Key flows

1. `POST /v1/runs` → persist `runs` row → worker executes bounded graph (§5.1) → canonical events → PG + SSE (AG-UI mapped at API boundary).
2. Read tools auto; write/destructive → policy eval → `approval.required` → human → execute with idempotency key + dry-run where supported.
3. Reconnect: `GET /v1/runs/{id}/events?after_sequence=N` (+ `Last-Event-ID`), terminal event if done.
