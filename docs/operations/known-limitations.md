# Known Limitations (W1 scope)

1. **Mock enterprise adapters only.** No real credentials; enterprise swap is
   adapter-level (see migration doc). CI guards `LLM_MODE=fake` out of prod charts.
2. **JWT signature enforced fail-closed in OIDC mode.** `packages/security/identity.py`
   rejects `alg=none`, strictly enforces `iss`/`aud`/`exp` when
   `AUTH_MODE=oidc`, and verifies the signature against `OIDC_JWKS_URL` /
   `OIDC_JWKS_JSON` (kid lookup; injectable via `set_jwt_verifier`). With no
   JWKS source configured, OIDC tokens are rejected (fail-closed). App
   startup (`validate_auth_config`) and Helm (`required` + NOTES `fail` in
   api/worker charts) refuse `AUTH_MODE=oidc` with empty
   `OIDC_ISSUER_URL`/`OIDC_AUDIENCE`. Mock identity remains local/test-only.
   Gateway tenant/scopes/user always come from the verified identity — MCP
   `X-Tenant-ID`/`X-Approved` client headers are never trusted (mismatch →
   403/404), and MCP audit is durable in `audit_records` (in-memory
   `AUDIT_LOG` is a debug tail only). A2A tasks enforce caller-tenant ==
   task-tenant (mismatch → 404) with `agent_tasks` write-through.
3. **In-process rate limiter is per-replica (last-mile only).** Production must
    front it with ingress rate-limiting: NGINX
    (`limit_req_zone`/`limit_req`, 429 + `Retry-After`) or Envoy
    (`local_ratelimit` / global rate-limit service) per tenant + route. The
    token bucket (`packages/security/limits.py::RateLimiter`) never replaces
    edge enforcement; see `production-readiness.md` § ingress/HPA.
4. **No Kafka/ServiceBus.** Postgres polling first (PG-backed queue replaces
    `InMemoryRunQueue`; `SELECT ... FOR UPDATE SKIP LOCKED`). HPA on CPU alone
    lags queue drain — add queue-depth autoscaling (Prometheus
    `workbench_queue_depth` custom metric / KEDA `ScaledObject`); see
    `production-readiness.md` § ingress/HPA. `deploy/helm/api|worker` charts
    accept `autoscaling.customMetrics` for this.
5. **Python policy, OPA-ready — no OPA sidecar.** `evaluate_action` is the pure
   contract-typed interface; Rego bundle mapping is future work (ADR-003).
6. **Single-region with defined RPO/RTO (no multi-region).** Postgres is the
    durability substrate. Backup/restore via PG dumps + WAL (see RB-4 and
    `production-readiness.md` § RPO/RTO): RPO ≤ 5 min (WAL archiving),
    RTO ≤ 30 min (restore + `make migrate` + Helm rollback). Multi-region
    active/passive is future work; per-environment RPO/RTO are enforced by
    backup drills, not just dumps.
7. **A2A specialists in-repo initially.** Logical externals; extraction to
   separate deployables without contract change.
8. **Eval dataset is production-shaped (12 cases).** Covers prod rollback with
    senior-approval, budget exhaustion, A2A timeout fallback, duplicate
    delivery, DB outage, ticket approval, and trace triage on top of the seed
    five. Gates (accuracy/coverage/compliance) remain floor values; grow toward
    production distribution as incidents accrue.
9. **UI tolerates unknown events** but has no offline mode; SSE reconnect via
    `after_sequence`/`Last-Event-ID` only.
10. **HSTS is prod-only.** `packages/security/headers.py` emits
    `Strict-Transport-Security` only when `APP_ENV=production`; local/test stay
    plain HTTP so browsers are never poisoned into HTTPS-only for localhost.
