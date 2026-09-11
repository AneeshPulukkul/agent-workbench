# Tutorial 01 — First Run (5 min)

Goal: start the stack, open the UI, create a checkout-incident case, poll events.

> Local mode uses mock adapters only. No cloud keys needed.

## 1. Setup (1 min)

```powershell
cp .env.example .env
python -m pip install -r requirements.lock
python -m pip install -e ".[dev]"
npm install
```

Verify fast suite (no services needed):

```powershell
python -m pytest tests/unit tests/contract -q
```

## 2. Start (pick one, 1 min)

**Option A — Docker (full stack):**

```powershell
docker compose up -d --build
python -m alembic upgrade head   # make migrate
curl -s http://localhost:8080/ready
```

**Option B — Native (no Docker):**

```powershell
# terminal 1: API on :8080
python -m uvicorn apps.api.main:app --reload --port 8080
# terminal 2: UI on :3000
npm --workspace ui/web run dev
```

Health check:

```powershell
curl -s http://localhost:8080/health
# {"status":"ok","service":"agent-api"}
```

## 3. Create a checkout case (1 min)

```powershell
$BASE = "http://localhost:8080"
$r = curl -s -X POST "$BASE/v1/runs" -H 'Content-Type: application/json' `
  -H 'Idempotency-Key: tut01-001' `
  -d '{"objective":"Investigate elevated checkout API error rate","context":{"service":"checkout-api"},"max_tool_calls":20,"max_model_calls":10,"max_cost_usd":2.0,"schema_version":"1.0"}'
$r
$runId = ($r | ConvertFrom-Json).run_id; "run=$runId"
```

## 4. Open the UI + poll events (2 min)

1. Open `http://localhost:3000`. Fill **New case** with the same objective, submit.
2. Watch **Assistant**, **Tool timeline**, **Run state**. Amber **Approval** card appears only for write tools.
3. Poll the stream (persist-then-publish, sequence-ordered):

```powershell
curl -s "$BASE/v1/runs/$runId/events?after_sequence=0"
curl -s "$BASE/v1/runs/$runId"
curl -s -X POST "$BASE/v1/runs/$runId/cancel"
```

## Next

- Approval gate walkthrough: `02-approval-flow.md`
- Stuck? `docs/USER-GUIDE.md` §7, `docs/FAQ.md`. Stop with `docker compose down`.
