# Agent Operations Workbench

Production-shaped reference platform: case investigation with AG-UI streaming,
MCP tools/resources/prompts, A2A specialist delegation, durable Postgres run state,
policy + human approval gates, and OpenTelemetry observability.

Architecture source of truth: `docs/architecture/` + `docs/adr/` — do not duplicate here.
Spec: `Re-ARch-Agentic.md` §3 (monorepo layout), Prompt 2 (this bootstrap).

> Local mode uses **mock adapters only** — no cloud credentials required. No irreversible
> action executes without policy + explicit human approval (enforced from Milestone 3+).

## Prerequisites

- Python 3.12+ (3.12 recommended)
- Node 20+, npm 10+
- Docker + Docker Compose v2
- Make is optional — every `make` target has a Windows PowerShell equivalent below
  (Git Bash / WSL also works)

## Quickstart — start here

> Compose now requires a `.env` file with `POSTGRES_*` and `GF_*` vars.
> **Copy `.env.example` to `.env` FIRST** — `docker compose up` fails fast without it.

Bash (macOS / Linux / Git Bash):

```bash
cp .env.example .env
make install
make lint
make test
make up
make migrate
```

Windows PowerShell (no make needed):

```powershell
Copy-Item .env.example .env
python -m pip install -r requirements.lock
python -m pip install -e ".[dev]"
npm install
python -m pytest tests/unit tests/contract -q
docker compose up -d --build
python -m alembic upgrade head
```

Verify:

```powershell
curl -s http://localhost:8080/health
curl -s http://localhost:8080/ready
docker compose ps
```

Stop:

```bash
make down
# PowerShell equivalent:
# docker compose down
```

## Ports

| Service | Host port | URL / notes |
|---|---|---|
| `api` | 8080 | http://localhost:8080/health · /ready · /docs |
| `ui` | 3000 | http://localhost:3000 (container :80) |
| `mcp-server` | 8081 | http://localhost:8081/health |
| `observability-agent` | 8082 | http://localhost:8082/.well-known/agent-card.json |
| `postgres` | 5432 | `workbench` db, volume `pgdata` |
| `jaeger` | 16686 | http://localhost:16686 (traces UI) |
| `prometheus` | 9090 | http://localhost:9090 |
| `grafana` | 3001 → 3000 | http://localhost:3001 (local creds from `.env`, see `.env.example`) |
| `otel-collector` | 4317 / 4318, 8889, 13133 | OTLP gRPC / HTTP |

## Make targets

| Target | What it does | PowerShell equivalent (no make) |
|---|---|---|
| `make install` | pip install lock + editable dev extras + pre-commit + npm install | `python -m pip install -r requirements.lock; python -m pip install -e ".[dev]"; npm install` |
| `make lint` | ruff check + format check + mypy | `python -m ruff check .; python -m ruff format --check .; python -m mypy apps/api packages tests/unit tests/contract` |
| `make test` | unit + contract (no services needed) | `python -m pytest tests/unit tests/contract -q` |
| `make unit` / `make contract` / `make integration` | pytest subsets (`integration` needs `make up`) | `python -m pytest tests/unit -q` etc. |
| `make up` / `make down` | compose up -d --build / down | `docker compose up -d --build` / `docker compose down` |
| `make migrate` | alembic upgrade head | `python -m alembic upgrade head` |
| `make dev` | uvicorn reload on :8080 | `python -m uvicorn apps.api.main:app --reload --port 8080` |
| `make lock` | regenerate `requirements.lock` via pip-compile | `python -m piptools compile --generate-hashes --output-file=requirements.lock requirements.in` |
| `make check-compose` | `docker compose config` validation | `docker compose config` |
| `make check-parity` | catalog == graph == skills parity (generator --check + test_parity.py) | `python skills/_generator.py --check; python -m pytest tests/contract/test_parity.py -q` |
| `make doctor` | checks python / node / docker / `.env` | `python --version; node --version; docker version; docker compose version` + `.env` check |

## Layout (§3)

```text
apps/api            FastAPI gateway (auth, runs, SSE, approvals)
apps/orchestrator    bounded graph runtime (Prompt 5/8)
apps/mcp_server      MCP Streamable-HTTP tools/resources/prompts (Prompt 6)
apps/a2a_agents/     observability / knowledge / remediation specialists (Prompt 7)
apps/worker          async execution (durable transitions, budgets)
packages/            contracts, protocols, llm, security, persistence, telemetry
ui/web               React+TS AG-UI client (Prompt 9)
tests/               unit / contract / integration / evaluation / replay
deploy/              otel-collector, prometheus, grafana configs (+ helm later)
docs/                architecture + adr (W0, authoritative — not overwritten by bootstrap)
```

## Dependency lock strategy

- `requirements.in` = direct deps (human-edited).
- `requirements.lock` = pinned snapshot (`pip-compile --generate-hashes ...`).
- Refresh: `make lock`. CI installs `pip install -r requirements.lock`.
- Frontend: `ui/web/package-lock.json` (npm). Root `package.json` workspaces.

## Mock adapters

Compose sets `APP_ENV=local`, `AUTH_MODE=mock`, `LLM_MODE=fake`:
`FakeRuntime` answers, MCP tools return fixture telemetry, A2A agent is deterministic,
`/ready` reports `mock-ok` without real enterprise calls. `LLM_MODE=fake` means no
cloud LLM calls — if you see real-model errors, check `LLM_MODE` is still `fake`
and `LITELLM_MODEL=mock/fake` in your `.env`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `docker compose up` fails: `POSTGRES_USER is required` / `GF_SECURITY_ADMIN_* is required` | You skipped the `.env` step. Run `Copy-Item .env.example .env` (or `cp .env.example .env`), then retry. Never commit real passwords — `.env` is git-ignored. |
| Docker Desktop error: `client version 1.55 is too new. Maximum supported API version is 1.44` (or similar 1.47/1.44) | Upgrade Docker Desktop (preferred) so the daemon API >= client. Temporary workaround for the current shell: `$env:DOCKER_API_VERSION = "1.44"; docker compose up -d --build`, then `Remove-Item Env:\DOCKER_API_VERSION` after upgrading. See `docs/USER-GUIDE.md` §4. |
| `8080 already in use` | Another API/dev server holds it. PowerShell: `netstat -ano \| findstr :8080`, kill the PID, or run `python -m uvicorn apps.api.main:app --reload --port 8081`. Compose: change `api.ports` to `"8081:8080"`. |
| `3000 already in use` | Vite vs compose `ui` clash. Use `npm --workspace ui/web run preview -- --port 3001 --strictPort` or remap compose `"3002:80"`. |
| `5432 already in use` | Local Postgres is running. Stop it, or remap compose `"5433:5432"` **and** set `DATABASE_URL=postgresql+psycopg://workbench:workbench_local_only@localhost:5433/workbench`. |
| Real LLM calls / missing `LLM_MODE=fake` | Local stack must run with `LLM_MODE=fake`. Check `.env` (`LLM_MODE=fake`, `LITELLM_MODEL=mock/fake`) and compose `api`/`worker` env. Production charts refuse `fake` — see `docs/operations/known-limitations.md`. |
| Stale `schema_version` → 400 | Send `"schema_version":"1.0"`. Mixed skill-pack versions unsupported — refresh from `skills/` / `AGENTS.md` (`python skills/_generator.py --check`). |

Full guide: `docs/USER-GUIDE.md` §7.

## Docs

- User guide: `docs/USER-GUIDE.md` (native + Docker runs, curl demo, troubleshooting)
- Known limitations (mock adapters, rate limits, single-region, …): `docs/operations/known-limitations.md`
- Tutorials: `docs/tutorials/01-first-run.md` · `02-approval-flow.md` · `03-byo-agent.md`
- FAQ / glossary: `docs/FAQ.md` · `docs/GLOSSARY.md`

## Definition of done (skeleton)

`make up && make migrate && make test` green; `/health` + `/ready` 200;
compose config valid; lint clean. Full product DoD per spec §16 (runs → stream →
MCP → A2A → traces → approval → replay) lands in later prompts.
