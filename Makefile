.DEFAULT_GOAL := help
PY ?= python
COMPOSE ?= docker compose

.PHONY: help install lint test unit contract integration eval security up down migrate dev lock check-compose check-parity doctor

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install: ## Install python dev deps + frontend deps
	$(PY) -m pip install -r requirements.lock
	$(PY) -m pip install -e ".[dev]"
	$(PY) -m pre_commit install || true
	npm install

lock: ## Regenerate requirements.lock from requirements.in (pip-tools)
	$(PY) -m pip install pip-tools
	$(PY) -m piptools compile --generate-hashes --output-file=requirements.lock requirements.in

lint: ## Ruff check + format check + mypy
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(PY) -m mypy apps/api packages tests/unit tests/contract

test: unit contract ## Fast suite (no external services)

unit: ## Unit tests
	$(PY) -m pytest tests/unit -q

contract: ## Contract tests (schemas, Agent Card, event envelope)
	$(PY) -m pytest tests/contract -q

integration: ## Integration tests (needs `make up`)
	$(PY) -m pytest tests/integration -q

eval: ## Evaluation + replay + failure-injection + security suites
	$(PY) -m pytest tests/evaluation tests/unit/test_security.py -q

security: ## Security tests only
	$(PY) -m pytest tests/unit/test_security.py -q

up: ## Start local stack (mock adapters only)
	$(COMPOSE) up -d --build

down: ## Stop local stack
	$(COMPOSE) down

migrate: ## Run alembic migrations against local postgres
	$(PY) -m alembic upgrade head

dev: ## Run API locally with reload (no docker)
	$(PY) -m uvicorn apps.api.main:app --reload --port 8080

check-compose: ## Validate compose file
	$(COMPOSE) config

check-parity: ## Verify catalog==graph==skills parity (tools, versions, timeouts)
	$(PY) skills/_generator.py --check
	$(PY) -m pytest tests/contract/test_parity.py -q

doctor: ## Check python/node/docker/.env prerequisites
	$(PY) --version
	node --version || (echo "node 20+ required" && exit 1)
	npm --version || (echo "npm required" && exit 1)
	docker version || (echo "docker required" && exit 1)
	$(COMPOSE) version || (echo "docker compose v2 required" && exit 1)
	$(PY) -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" || (echo "python 3.12+ required" && exit 1)
	@test -f .env || (echo "missing .env — run: cp .env.example .env" && exit 1)
	@grep -q "^POSTGRES_USER=" .env || (echo "POSTGRES_USER missing in .env" && exit 1)
	@grep -q "^POSTGRES_PASSWORD=" .env || (echo "POSTGRES_PASSWORD missing in .env" && exit 1)
	@grep -q "^POSTGRES_DB=" .env || (echo "POSTGRES_DB missing in .env" && exit 1)
	@grep -q "^GF_SECURITY_ADMIN_USER=" .env || (echo "GF_SECURITY_ADMIN_USER missing in .env" && exit 1)
	@grep -q "^GF_SECURITY_ADMIN_PASSWORD=" .env || (echo "GF_SECURITY_ADMIN_PASSWORD missing in .env" && exit 1)
	@echo "doctor: ok (py/node/docker/.env)"
