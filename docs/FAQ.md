# FAQ

## 1. Do I need API keys to run this?

No. Copy `cp .env.example .env` and run — local mode uses `AUTH_MODE=mock`, `LLM_MODE=fake`, and mock MCP/A2A adapters. Keys (`OPENAI_API_KEY`, etc.) stay empty unless you wire a real provider.

## 2. Will it cost me money / call the cloud?

No, locally. `FakeRuntime` answers model calls and tools return fixtures, so `make up` + `make test` make zero external calls. Real cost only appears if you configure a live LiteLLM model.

## 3. Docker build/up fails — what first?

Run `docker compose config` (`make check-compose`), then retry `docker compose build ui --no-cache` (needs network for `npm install`). On `client version 1.47 is too new`, set `$env:DOCKER_API_VERSION="1.44"` or upgrade Docker Desktop — see `docs/USER-GUIDE.md` §4.

## 4. Which ports does it use? Something is already in use.

API 8080, UI 3000, Postgres 5432, MCP 8081, A2A 8082, Jaeger 16686, Prometheus 9090, Grafana 3001→3000. Find the clash with `netstat -ano | findstr :8080` and remap (e.g. compose `"8081:8080"`) or stop the other process — see `docs/USER-GUIDE.md` §7.

## 5. Is this production-ready?

No — it's a production-shaped reference. See `docs/operations/known-limitations.md`: mock adapters only, per-replica rate limiter (needs ingress limiting), no Kafka/ServiceBus, Python policy (no OPA sidecar), single-region, no offline UI mode, HSTS prod-only.

## 6. Skill-pack vs LangGraph — which do I use?

Managed default (UI/Gateway + `LangGraphRuntime`) for product flows; portable skill-pack (`AGENTS.md` + `skills/*/SKILL.md`, HTTP + JSON, no `langgraph` import) for BYO agents like Claude/Codex/Copilot. Both hit the same endpoints with the same guarantees — see `docs/architecture/skill-consumption.md` and `docs/tutorials/03-byo-agent.md`.

## 7. Where do secrets go? Are they logged?

Nowhere in the repo: keep real values in the untracked `.env` only (never commit). Audit/telemetry store hashes + redacted JSON; `sensitive` payloads need `safe_for_ui=true` before UI display. OIDC mode is fail-closed (JWKS-verified, `alg=none` rejected).

## 8. How do I contribute?

Run `make lint` (ruff + mypy) and `make test` (unit + contract) before opening a PR; keep `skill == contract == tool` pins in sync (`make check-parity`). Small, scoped PRs with updated docs/tutorials preferred.
