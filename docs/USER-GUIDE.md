# User Guide — Agent Operations Workbench

Technical audience: developers and operators running the local mock stack
(UI shell + Gateway API + MCP/A2A adapters). No browser is required for any
step below except visual verification of the UI.

## 1. What you get

- **UI shell** (`ui/web/src/App.tsx`, styles in `ui/web/src/styles/theme.css`):
  header (AO mark, run-status pill, `stream:` conn badge, theme toggle),
  sidebar (**New case** form, **Run state**), main grid (**Assistant**,
  **Approval** amber card, **Tool timeline**, **Delegation**, **Findings**).
- **API** (`apps/api/main.py`): `GET /health`, `GET /ready` (mock-ok),
  `POST /v1/runs`, `GET /v1/runs/{id}`, `GET /v1/runs/{id}/events`,
  `POST /v1/runs/{id}/cancel`, `GET /v1/runs/{id}/approvals`,
  `POST /v1/runs/{id}/approvals/{approval_id}/decide`.
- **Compose** (`docker-compose.yml`): `postgres`, `mcp-server` (:8081),
  `observability-agent` (:8082), `api` (:8080), `worker`, `otel-collector`,
  `jaeger`, `prometheus`, `grafana`, `ui` (:3000).

Shell previews (faithful SVG mocks of the new theme — no browser needed):

![UI overview, light theme](images/ui-overview-light.svg)
![UI overview, dark theme](images/ui-overview-dark.svg)
![Animated run flow](images/run-flow-animated.svg)

## 2. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Python | 3.12 (`requires-python = ">=3.12,<3.14"`) | `python --version` |
| Node | 24 (repo Docker pins node:20-alpine; use 20+) | `node --version` |
| Docker + Compose v2 | Desktop 4.x / Engine 24+ | `docker version && docker compose version` |
| curl | any recent | `curl --version` |

```powershell
python --version   # 3.12.x
node --version     # v20+ (24 recommended)
docker version; docker compose version
```

## 3. Native run (no Docker)

### 3.1 Backend API — `http://localhost:8080`

```powershell
python -m pip install -r requirements.lock
python -m pip install -e ".[dev]"
python -m uvicorn apps.api.main:app --reload --port 8080
# or: make dev
```

Smoke test (new terminal):

```powershell
curl -s http://localhost:8080/health
# {"status":"ok","service":"agent-api"}
curl -s http://localhost:8080/ready
# {"status":"ready","mode":"local","checks":{"postgres":"mock-ok","mcp":"mock-ok","a2a":"mock-ok"}}
```

In-process check without a server (FastAPI `TestClient`):

```python
from fastapi.testclient import TestClient
from apps.api.main import create_app

c = TestClient(create_app())
assert c.get("/health").json() == {"status": "ok", "service": "agent-api"}
assert c.get("/ready").json()["checks"]["postgres"] == "mock-ok"
print("api mock-ok")
```

### 3.2 Frontend — `http://localhost:3000`

```powershell
npm install                      # or: npm --workspace ui/web install
npm --workspace ui/web run dev   # vite --port 3000
```

Other scripts (`ui/web/package.json`):

```powershell
npm --workspace ui/web run build    # tsc --noEmit && vite build
npm --workspace ui/web run preview  # vite preview --port 3000
npm --workspace ui/web run test     # vitest run
```

The Vite dev server proxies nothing by itself — point the UI at the API
(`fetch` base `http://localhost:8080`) or run the full compose stack below.

## 4. Docker run

```powershell
docker compose up -d --build   # or: make up
docker compose ps
curl -s http://localhost:8080/ready
curl -s http://localhost:3000/ | Select-Object -First 5
```

Service → host port map:

| Service | Host port | Notes |
|---|---|---|
| `api` | 8080 | Gateway; `/docs`, `/v1/openapi.json` |
| `ui` | 3000 | nginx serving `dist/` (container :80) |
| `postgres` | 5432 | `workbench` db; volume `pgdata` |
| `mcp-server` | 8081 | Tool Gateway adapters |
| `observability-agent` | 8082 | A2A endpoint `/a2a` |
| `jaeger` | 16686 | traces UI |
| `prometheus` | 9090 | metrics |
| `grafana` | 3001 → 3000 | admin/admin (local only) |
| otel-collector | 4317/4318, 8889, 13133 | OTLP gRPC/HTTP |

Stop: `docker compose down` (`make down`). Validate only:
`docker compose config` (`make check-compose`).

### Known Docker Desktop API-version workaround

Symptom:

```text
Error response from daemon: client version 1.47 is too new.
Maximum supported API version is 1.44
```

Fix (pick one, in order):

```powershell
# 1. Upgrade Docker Desktop (preferred) so daemon API >= client.
# 2. Pin the client API for this shell (temporary):
$env:DOCKER_API_VERSION = "1.44"
docker compose up -d --build
# 3. Or upgrade the compose plugin: Docker Desktop > Settings > Updates.
Remove-Item Env:\DOCKER_API_VERSION  # unset after daemon upgrade
```

## 5. End-to-end demo (curl only)

Base: `$BASE = "http://localhost:8080"`. Auth: local mock accepts requests
without a bearer token; in OIDC mode add `-H "Authorization: Bearer $TOKEN"`.
Current skeleton returns `created` + empty event/approval lists — the flow
below still exercises every contract surface.

```powershell
$BASE = "http://localhost:8080"

# 1. Create a run (202 {run_id, status:"created", stream_url})
$r = curl -s -X POST "$BASE/v1/runs" -H 'Content-Type: application/json' `
  -H 'Idempotency-Key: inv-001' `
  -d '{"objective":"Investigate elevated checkout API error rate","context":{"service":"checkout-api"},"max_tool_calls":20,"max_model_calls":10,"max_cost_usd":2.0,"schema_version":"1.0"}'
$r
$runId = ($r | ConvertFrom-Json).run_id; "run=$runId"

# 2. Status
curl -s "$BASE/v1/runs/$runId"

# 3. Poll events (persist-then-publish, sequence-ordered; replay with after_sequence)
curl -s "$BASE/v1/runs/$runId/events?after_sequence=0"
# live SSE in full implementation: curl -sN "$BASE/v1/runs/$runId/events?after_sequence=0"
# honor Last-Event-ID fallback; terminal run.completed/failed closes the stream

# 4. Approvals: list, then decide (human only; Idempotency-Key required)
curl -s "$BASE/v1/runs/$runId/approvals"
# replace <approval_id> with a real id; skeleton returns []
curl -s -X POST "$BASE/v1/runs/$runId/approvals/<approval_id>/decide" `
  -H 'Content-Type: application/json' -H 'Idempotency-Key: dec-001' `
  -d '{"decision":"approved","reason":"rollback is safe, dry-run passed"}'
# reject variant: {"decision":"rejected","reason":"need second signal"}
# double-decide -> 409 in full implementation

# 5. Cancel (idempotent -> 202 {status:"cancelled"})
curl -s -X POST "$BASE/v1/runs/$runId/cancel"
```

Rules the server enforces (see `AGENTS.md`): write/destructive tools need a
persisted human `Approval` row; never self-authorize; budgets
(`max_tool_calls` 1–100, `max_model_calls` 1–50, `max_cost_usd` 0–1000);
objective ≤ 8 KB, context ≤ 64 KB; `traceparent` in, `X-Request-ID` out.

## 6. Theme toggle + responsive notes

- `RunHeader.tsx` → `ThemeToggle` (`<select data-testid="theme-toggle">`):
  **Light / Dark / Auto**, persisted in `localStorage` key `aow-theme`.
- `applyTheme()`: `light|dark` sets `document.documentElement.dataset.theme`;
  `auto` removes the attribute so `prefers-color-scheme` wins
  (`theme.css` `@media (prefers-color-scheme: dark)` block).
- Tokens: light `bg #f6f7f9 / surface #ffffff / text #0f172a / primary #2563eb`;
  dark `bg #0b1220 / surface #131d33 / text #e8edf7 / primary #60a5fa`.
  Approval card is always amber (`warning #b45309 / warning-bg #fef3c7`
  light; `#fbbf24` on translucent amber dark).
- Responsive: `.aow-layout` is `320px 1fr`; at `≤900px` it collapses to one
  column and the sticky sidebar becomes static (`Run state` stacks under
  `New case`). Cards use `min-width: 0` so timelines/tables don't overflow.
- A11y: `:focus-visible` ring, `role="status"` notices, `role="alert"` stream
  errors, labelled sections (`aria-label="approval-card"`, etc.).

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `8080 already in use` | Another API/dev server. `netstat -ano \| findstr :8080`, kill PID or run `--port 8081` (then point UI/curl at it). Compose: change `api.ports` to `"8081:8080"`. |
| `3000 already in use` | Vite vs compose `ui`. Use `npm --workspace ui/web run preview -- --port 3001 --strictPort` or remap compose `"3002:80"`. |
| `5432 already in use` | Local Postgres running. Stop it, or remap compose `"5433:5432"` **and** set `DATABASE_URL=postgresql+psycopg://workbench:workbench_local_only@localhost:5433/workbench`. |
| `/ready` shows `mock-ok` but PG expected | `APP_ENV=local` intentionally mocks downstream checks (`health.py`). Real checks land with Postgres wiring — set `DATABASE_URL` + run `make migrate` (`alembic upgrade head`). |
| Auth errors (`401/403`) | `AUTH_MODE=mock` locally; with OIDC set, send `Authorization: Bearer $TOKEN` (tenant comes from the token). |
| UI shows "Stream issue" | SSE dropped. Click **Reconnect / replay** (re-subscribes with `?after_sequence=<lastSequence>` + `Last-Event-ID`). Check `api` logs / `otel-collector`. |
| Compose build fails on `ui` | `npm install` needs network. Retry `docker compose build ui --no-cache`; or build natively (`npm --workspace ui/web run build`) to isolate. |
| Docker `client version too new` | See §4 workaround (`DOCKER_API_VERSION`, upgrade Desktop). |
| Stale `schema_version` → 400 | Upgrade skill-pack/contracts; mixed versions unsupported. Send `"schema_version":"1.0"`. |

## 8. File map

- `apps/api/main.py` — app factory, routers, envelope errors.
- `apps/api/routes/health.py` — `/health`, `/ready`.
- `apps/api/routes/runs.py`, `approvals.py` — run/approval skeleton (Prompt 8 fills in).
- `ui/web/src/App.tsx` — shell composition; `components/RunHeader.tsx` (theme),
  `CaseForm.tsx`, `AssistantStream.tsx`, `ApprovalCard.tsx`,
  `ToolTimeline.tsx`, `DelegationTimeline.tsx`, `FindingsPanel.tsx`.
- `docker-compose.yml` — full local stack.
- `docs/images/ui-overview-light.svg`, `ui-overview-dark.svg`,
  `run-flow-animated.svg` — browser-free visuals for this guide.
