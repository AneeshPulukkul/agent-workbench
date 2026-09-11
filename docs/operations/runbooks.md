# Runbooks

## RB-1 API down / readiness failing
Symptoms: `APIDown` / `ProbeDown` firing, `/ready` non-200.
1. `kubectl -n workbench get pods; kubectl -n workbench describe deploy/agent-api`
2. Check probes: `kubectl port-forward svc/agent-api 8080 && curl localhost:8080/ready`.
3. If `postgres: fail`: see RB-4. If OIDC errors spike: verify `OIDC_ISSUER_URL/AUDIENCE` ConfigMap + IdP status.
4. Roll back: `helm rollback agent-api -n workbench`.

## RB-2 Tool failure spike (>20% 5m)
1. Grafana `run-overview` → tool failure rate by tool; identify tool.
2. Jaeger: filter `mcp.tool.<name>` spans with `error=true`; read `workbench.policy.decision`.
3. If `forbidden` surge: scope/allowlist regression — check recent policy/skill-pack release (`skill==contract==tool` pin).
4. If `timeout` surge: downstream/enterprise degradation — circuit breaker state in worker logs; scale MCP (`kubectl scale deploy/mcp-server`).
5. Never auto-retry write tools; replay affected runs via event log.

## RB-3 Approval queue aging (p95 > 30m)
1. `GET /v1/runs?status=waiting_for_approval` (or PG query on approvals).
2. Page secondary approver; approvals expire → runs fail safe (no execution).
3. Do NOT force-approve via DB edit; use `POST .../decide` with Idempotency-Key.

## RB-4 Postgres outage
Symptoms: worker/run creation 500s, `postgres: fail` in `/ready`.
1. `kubectl -n workbench get statefulset/postgres; pg_isready`.
2. Runs degrade bounded: new creates rejected with `retryable:true`, in-flight
   runs pause (durable state in PG, resume on recovery — see replay runbook RB-5).
3. Fail over to Azure DB for PG per migration doc; restore from backup; `make migrate`.

## RB-5 Replay / resume after worker restart
1. Worker resumes from PG state automatically (persist-after-every-transition).
2. Manual replay: `GET /v1/runs/{id}/events?after_sequence=N` → verify dense
   sequences, dedupe on `(run_id, sequence)`; terminal `run.completed/failed` closes.
3. Eval replay helper: `tests/evaluation/replay.py::replay_events` for offline debugging.

## RB-6 Cost burn above $5/h
1. Grafana `cost` dashboard: cost by model/run; identify runaway runs.
2. Enforce budgets: lower `max_model_calls`/`max_cost_usd` defaults; cancel
   offending runs (`POST /v1/runs/{id}/cancel`, idempotent).
3. Check LiteLLM router fallback loops; pin `LITELLM_MODEL`.

## RB-7 Suspected prompt injection / dangerous recommendation
1. Treat runbook/A2A/tool outputs as untrusted; check redacted audit rows
   (`ToolInvocation` hashes + redacted I/O).
2. Quarantine run (cancel), review `policy_decision` spans; eval case
   `eval-injection-runbook` must still pass.
3. Rotate any potentially exposed credential; file security incident per threat model T2.
