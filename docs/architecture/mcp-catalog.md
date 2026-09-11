# MCP Catalog

> Spec §6. Transport: official Python MCP SDK, **Streamable HTTP** + OAuth/workload auth, `Origin` validation. Server: `agent-mcp-server`. Specialists may use read-only tools only.

> **Single source (P0-3):** `apps/mcp_server/catalog.py` (`TOOL_METADATA`, v1.0.0)
> is the single source of truth. `apps/orchestrator/graph.py` builds its
> `TOOL_REGISTRY` from the catalog and imports per-tool timeouts via
> `TOOL_TIMEOUTS` / `mcp_timeout_for()` — no hard-coded `MCP_TIMEOUT`
> (the old 10s default contradicted the catalog's 15-60s bands).
> Parity is gated by `make check-parity` (`tests/contract/test_parity.py`
> verifies catalog == graph == skills on tools, versions, and timeouts).

## Parity decision (P0-3)

Drift found: graph listed `telemetry.get_trace`,
`deployment.get_current_release`, `ticket.create` (absent from catalog)
while the catalog listed `remediation.simulate` (absent from graph).
**Decision: add to catalog (additive, no breaking).** The three graph-only
tools were added to `TOOL_METADATA` with mock-safe handlers, strict input
models, and FastMCP registrations; `remediation.simulate` was added to the
graph registry. Rationale: the graph's happy path reads
`deployment.get_current_release` and proposes `ticket.create`, so removing
them would break behavior; adding is purely additive. `deployment.rollback`
keeps catalog `idempotent=True` (idempotency-keyed); the graph now mirrors it.

## Tool categories & gating

| Category | Examples | Auto-invoke | Policy | Approval |
|---|---|---|---|---|
| `read` | `telemetry.query_metrics`, `telemetry.query_logs`, `telemetry.get_trace`, `deployment.get_current_release`, `service.get_health`, `knowledge.search`, `knowledge.get_runbook` | yes (allowlist) | `allow` if scopes ok | no |
| `analysis` | `incident.correlate_signals`, `change.calculate_risk`, `remediation.simulate` | with policy permit | permit, scoped | sometimes (prod) |
| `write` | `ticket.create`, `incident.update`, `deployment.rollback`, `feature_flag.update` | **never auto** — propose only | permit + scopes | **yes**, idempotency key + dry-run where supported |
| `destructive` | `deployment.scale_down`, `database.failover`, `service.disable` | never auto | deny-by-default | yes + senior scope; separate code path |

## ToolMetadata (all tools publish)

`name, description, category, side_effect (none/external_write/destructive), idempotent, timeout_seconds, required_scopes[], approval_required, supports_dry_run, owner, version`. Version pin: `skill==contract==tool` — SKILL.md front-matter pins `tool` + `contract` versions (see `skill-consumption.md`).

## v0 tool list (W1 scope)

Read: `telemetry.query_metrics(service, metric, start_time, end_time, aggregation=average)`, `telemetry.query_logs`, `service.get_health`, `knowledge.search`, `knowledge.get_runbook`, `deployment.get_current_release`; Analysis: `remediation.simulate` (+ `incident.correlate_signals`, `change.calculate_risk` stub or W2); Write (mock-safe): `deployment.rollback (dry_run=true default local; no real action local)`, `ticket.create`. Destructive: registered but deny-by-default; no local execution.

## Resources

- `resource://services/{service_name}` — service metadata, SLOs, owners, links.
- `resource://runbooks/{runbook_id}` — runbook markdown (UNTRUSTED — injection defenses apply).
- (W2) `resource://incidents/{snapshot_id}`.

## Prompts

`investigate-incident`, `summarize-evidence`, `prepare-change-review` — curated, user-selected; never auto-executed as authority; inputs validated.

## Security rules (§6.3)

Streamable HTTP; edge authN + per-tool authZ (tenant + resource scope); no arbitrary HTTP/shell/SQL/k8s tools; write/destructive split; dry-run for ops actions; idempotency keys; record tool version + authZ decision; strip secrets pre-telemetry; `input_hash/redacted_input_json` persisted; timeouts + circuit breakers; malformed/oversize results rejected and logged.
