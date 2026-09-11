# Glossary

| Term | What it means (junior-friendly) |
|---|---|
| AG-UI | The event protocol the UI uses to stream assistant text, tool calls, and approvals live. |
| MCP | The single doorway to enterprise systems — agents call tools only through this gateway, never directly. |
| A2A | Agent-to-agent calls: the orchestrator asking a specialist agent (observability, knowledge…) for help. |
| OTel | OpenTelemetry: the shared tracing/metrics/logs plumbing so every run is observable in Jaeger/Prometheus/Grafana. |
| Run | One investigation case from creation to completion, with durable state you can poll, cancel, and replay. |
| Event | One sequence-numbered fact in a run's history (tool call, finding, approval) — replayable via `after_sequence`. |
| Policy | Server-side rules that decide whether a proposed action is allowed, denied, or needs a human. |
| Approval | A persisted human yes/no row that gates any write/destructive tool; double-decide returns 409. |
| BudgetTracker | The server-side counter enforcing `max_tool_calls` / `max_model_calls` / `max_cost_usd` so runs can't burn money. |
| LiteLLM | The model router that lets the backend swap LLM providers behind one interface. |
| FakeRuntime | The local/test stand-in for real models and tools — deterministic answers, zero cost, zero cloud calls. |
