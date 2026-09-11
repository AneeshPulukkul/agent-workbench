# Contributing to Agent Operations Workbench

Thanks for contributing. This repo is a production-shaped reference platform
(FastAPI gateway, MCP tools, A2A agents, Postgres run state, OTel).

## Prerequisites

- Python 3.12 (requires-python `>=3.12,<3.14`)
- Node 20+, npm 10+
- Docker + Docker Compose v2
- Make (Git Bash / WSL on Windows)

## Setup (in order)

```bash
cp .env.example .env
make install
make lint
make test
make up
make migrate
```

Never commit a real `.env`. Only safe placeholders live in `.env.example`.

## Workflow

```bash
make install       # pip install lock + editable .[dev] + pre-commit + npm install
make lint          # ruff check + ruff format --check + mypy
make test          # unit + contract (no services needed)
make check-parity  # skills/_generator.py --check + test_parity.py
make up            # compose up -d --build (mock adapters only)
make migrate       # alembic upgrade head
```

PowerShell equivalents (no Make):

```powershell
Copy-Item .env.example .env
python -m pip install -r requirements.lock
python -m pip install -e ".[dev]"
npm install
python -m ruff check .
python -m ruff format --check .
python -m mypy apps/api packages tests/unit tests/contract
python -m pytest tests/unit -q
python -m pytest tests/contract -q
python skills/_generator.py --check
docker compose config
docker compose up -d --build
python -m alembic upgrade head
```

Run `make check-parity` before opening a PR that touches
`packages/contracts`, `apps/mcp_server/catalog`, `skills/`, or `AGENTS.md`.
Skill == contract == tool pins (`version 1.0.0`, `contract_version 1.0`) must stay in sync.

## Pull requests

- Keep PRs small and focused; include tests for behavior changes.
- `make lint` and `make test` must be green.
- Contract or tool changes must include `make check-parity` output in the PR.
- Follow Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`).

## DCO sign-off (required)

All commits must be signed off per the Developer Certificate of Origin:

```bash
git commit -s -m "feat: describe change"
```

The `-s` adds `Signed-off-by: Name <email>`. PRs without sign-off are blocked.

## Issues

- `bug report`: include repro steps, expected vs actual, commit SHA, and logs.
- `feature request`: include problem statement and proposed contract changes.
- `good first issue`: small, well-scoped tasks labeled `good first issue` and
  `help wanted`. New contributors should start here.
