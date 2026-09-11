# OpenTelemetry Plan

> Spec §1.4 observability + Prompt 10. Stack: OTel SDK → Collector → Jaeger/Tempo + Prometheus + Grafana + log backend (local); Azure Monitor exporter option in AKS.

## Spans (W3C trace context throughout)

HTTP server → run lifecycle → workflow transition → agent invocation → model call → retrieval → MCP tool → A2A task → policy decision → approval-wait → DB op. `run_id` on every span; `tenant_id` only if privacy policy allows (default: hash or omit).

## Attributes

- Stable: `http.*`, `db.*`, `rpc.*` semconv; custom `workbench.run_id`, `workbench.tenant_hash`, `workbench.tool.name`, `workbench.tool.side_effect`, `workbench.agent.name`, `workbench.task.id`, `workbench.policy.decision`, `workbench.budget.*`.
- GenAI (dev-status per spec fn 6–8): isolate behind `packages/telemetry/conventions.py` adapter — `gen_ai.operation.name`, `gen_ai.tool.name`, model/provider, input/output tokens, estimated cost. Swap without touching business code when conventions stabilize.

## Metrics & logs

Metrics: run count/duration by status, model calls/tokens/cost per run, tool success/failure + latency by tool, A2A latency/timeout rate, policy allow/deny/approval rate, approval wait p50/p95, SSE reconnects, budget-exhaustion count. Logs: structured JSON, trace-correlated; NEVER raw prompts/completions/secrets/PII by default; `OTEL_CONTENT_CAPTURE=dev-only` flag for local debugging with redaction still applied to secrets.

## Collector & dashboards (deploy/otel-collector)

OTLP receivers → batch → Jaeger/Tempo, Prometheus, Loki/log backend; health-checked; resource limits set. Grafana: run overview, cost dashboard, error-budget panels. Alerts (W2+): p95 run latency, tool failure spike, approval-wait age, cost-burn rate.

## Tests

Contract: traceparent propagated API→worker→MCP→A2A; spans present for policy + approval-wait; no secret leakage test (fixtures with fake secrets assert redacted).
