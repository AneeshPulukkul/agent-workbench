# Production Readiness Checklist + SLOs (Spec §16 DoD gate)

## Pre-prod checklist (all must be green)

- [ ] `make up && make migrate && make test` green (unit + contract, no services).
- [ ] Integration suite green against disposable PG (`tests/integration`).
- [ ] Eval suite green: `pytest tests/evaluation -q` (accuracy/coverage/compliance + replay + failure injection).
- [ ] Security suite green: `pytest tests/unit/test_security.py -q`.
- [ ] DoD walkthrough (§16): create run → AG-UI stream → MCP reads → A2A
      delegation → traces → structured recommendation → simulated remediation
      propose → approve/reject → replay → mock→enterprise adapter swap without
      orchestrator change → Helm deploy (`helm lint` + `helm template` clean
      for api/worker/mcp/a2a/ui).
- [ ] `AUTH_MODE=oidc`, `APP_ENV=production`; mock identity refused (fail-closed verified).
- [ ] Secrets via external-secrets/KeyVault CSI only; no secret in image/env dump.
- [ ] Images pinned by digest, SBOM attached, cosign-signed, trivy HIGH/CRITICAL=0
      (see `deploy/helm/SBOM-SCANNING.md`).
- [ ] `helm lint` + kubeconform clean; PDB/HPA/NetworkPolicy applied; PodSecurity
      (non-root, RO fs, dropped caps) verified with `kubectl describe`.
      NetworkPolicy ingress is deny-by-default with explicit `from`
      (namespace/pod selectors: ingress-nginx + in-namespace callers only).
- [ ] HSTS verified in prod (`Strict-Transport-Security` on API responses when
      `APP_ENV=production`; absent in local/test).
- [ ] Ingress rate-limiting enforced at edge (NGINX `limit_req` or Envoy
      `local_ratelimit`), not just the per-replica token bucket (see § Edge).
- [ ] Queue-depth HPA configured for workers (CPU + `workbench_queue_depth`
      custom metric / KEDA), not CPU alone (see § Edge).
- [ ] Backup/restore drill green: RPO ≤ 5 min, RTO ≤ 30 min (see § RPO/RTO,
      runbook RB-4).
- [ ] Prometheus alerts loaded; Grafana dashboards (run-overview, cost, slo,
      security) provisioned; log pipeline redaction spot-checked.
      PII redaction covers ssn/credit_card/email/phone in values and free text
      (`redact`/`scrub_message`/`redact_mapping` unified); oversize audit
      payloads are hash-referenced via `redact_for_audit` (64KB cap).

## Edge: ingress rate-limit + queue-depth HPA

**Rate limiting (two layers):**

- Edge (required): NGINX Ingress —
  `nginx.ingress.kubernetes.io/limit-rps: "20"`,
  `limit-burst-multiplier: "5"`, 429 + `Retry-After` on excess; or Envoy
  `local_ratelimit` (token bucket per route) fronting a global rate-limit
  service keyed by tenant. Tune per route: reads (GET /v1/runs) higher burst,
  writes/decide lower.
- Last-mile (defense in depth): `packages/security/limits.py::RateLimiter`
  per-tenant token bucket in the API. Per-replica only — never rely on it
  alone; it smooths bursts that pass the edge.

**Autoscaling (CPU floor + queue signal):**

- CPU HPA remains the floor (`targetCPUUtilizationPercentage` in
  `deploy/helm/*/values.yaml`).
- Workers drain a PG queue, so CPU lags: add a custom metric. Example
  (`autoscaling.customMetrics` in api/worker charts):
  ```yaml
  customMetrics:
    - type: Pods
      pods:
        metric: { name: workbench_queue_depth }
        target: { type: AverageValue, averageValue: 50 }
  ```
  backed by Prometheus Adapter (`workbench_queue_depth` gauge) or a KEDA
  `ScaledObject` (`postgresql` scaler on pending-run count). Alert on
  sustained queue age alongside depth.

## RPO/RTO + backup-restore (RB-4)

| Objective | Target | Mechanism |
|---|---|---|
| RPO | ≤ 5 min | Nightly `pg_dump` + continuous WAL archiving (point-in-time recovery) |
| RTO | ≤ 30 min | Restore latest base + WAL → `make migrate` → `helm rollback` / redeploy → `/ready` green |

Drill (quarterly, gate for prod deploys):

1. Snapshot: `pg_dump -Fc workbench > backup.dump` (+ WAL archive verify).
2. Restore to disposable PG: `pg_restore -d workbench backup.dump && make migrate`.
3. Replay spot-check: `GET /v1/runs/{id}/events` sequences dense; approvals intact.
4. Failover path: Azure DB for PG per migration doc; runbook RB-4 steps 1–3.
5. Record RPO/RTO actually achieved; file gap issues if targets missed.

## SLOs

| SLO | Target | Metric / alert |
|---|---|---|
| Run p95 duration | < 120s (10m window) | `workbench_run_duration_bucket` → `HighRunLatencyP95` |
| Tool failure rate | < 20% (5m) | `workbench_tool_calls_total` → `ToolFailureSpike` |
| Approval wait p95 | < 30m (15m) | `workbench_approval_wait_bucket` → `ApprovalWaitAging` |
| Model cost burn | < $5/h | `workbench_model_cost_usd_total` → `CostBurnRate` |
| Budget exhaustion | < 0.05/s (15m) | `workbench_budget_exhausted_total` → `BudgetExhaustionSpike` |
| Eval: tool accuracy | ≥ 0.8 on `tests/evaluation/dataset.json` | CI `test_eval.py` |
| Eval: evidence coverage | ≥ 0.9 (findings citing evidence) | CI `test_eval.py` |
| Eval: approval compliance | 100% (no unapproved writes) | CI + `test_failure_injection.py` |
| Eval: unsupported-claim rate | ≤ 0.1 | CI `test_eval.py` |
| Availability (API) | 99.5% monthly (2xx on /ready) | `ProbeDown` / `APIDown` alerts |

Alert routing: `warning` → on-call channel; `critical` (ToolFailureSpike,
APIDown) → page. All alerts carry runbook links in `docs/operations/runbooks.md`.
