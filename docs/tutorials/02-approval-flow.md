# Tutorial 02 — Approval Flow (human gate)

Goal: propose `deployment.rollback` as dry-run, approve vs reject, hit 409 on double-decide, replay events.

Rule: write tools never execute without server policy eval + a persisted human `Approval` row. Clients never self-authorize.

## 1. Create a run that proposes a rollback

```powershell
$BASE = "http://localhost:8080"
$r = curl -s -X POST "$BASE/v1/runs" -H 'Content-Type: application/json' `
  -H 'Idempotency-Key: tut02-001' `
  -d '{"objective":"Propose checkout-api rollback after error spike","context":{"service":"checkout-api","deployment":"checkout-api"},"max_tool_calls":20,"max_model_calls":10,"max_cost_usd":2.0,"schema_version":"1.0"}'
$runId = ($r | ConvertFrom-Json).run_id; "run=$runId"
```

The agent proposes (never executes directly). Safe simulation first:

```powershell
# read-only/analysis tools need no approval; rollback dry_run=true is the safe default
curl -s "$BASE/v1/runs/$runId/approvals"
```

Copy the `<approval_id>` from the list (amber card in UI shows the same row).

## 2. Decide via UI or curl

UI: open `http://localhost:3000`, find the amber **Approval** card → **Approve** or **Reject** with a reason. Same effect as curl:

```powershell
$ap = "<approval_id>"
# APPROVE
curl -s -X POST "$BASE/v1/runs/$runId/approvals/$ap/decide" `
  -H 'Content-Type: application/json' -H 'Idempotency-Key: tut02-dec-001' `
  -d '{"decision":"approved","approver":"oncall@example.com","reason":"dry-run clean, error budget burning","schema_version":"1.0"}'
# REJECT variant (use a fresh approval or new run):
# -d '{"decision":"rejected","approver":"oncall@example.com","reason":"need second signal","schema_version":"1.0"}'
```

Notes: `approver` + `Idempotency-Key` + `"schema_version":"1.0"` are required. Rejected runs complete with `outcome: rejected`, `executed: false`.

## 3. Double-decide → 409

```powershell
# repeat with a NEW Idempotency-Key after a decision is recorded:
curl -s -X POST "$BASE/v1/runs/$runId/approvals/$ap/decide" `
  -H 'Content-Type: application/json' -H 'Idempotency-Key: tut02-dec-002' `
  -d '{"decision":"approved","approver":"oncall@example.com","schema_version":"1.0"}'
# -> 409 {"code":"conflict",...} (or approval_expired)
# same (approval, key) replays the stored outcome instead of erroring
```

## 4. Replay after a disconnect

Events are sequence-ordered; resume with the last seen sequence (UI **Reconnect / replay** does this):

```powershell
curl -s "$BASE/v1/runs/$runId/events?after_sequence=0"   # full replay
curl -s "$BASE/v1/runs/$runId/events?after_sequence=5"   # resume from 5
# live: curl -sN "$BASE/v1/runs/$runId/events?after_sequence=5"
```

Refs: `AGENTS.md` §2–§4, `docs/USER-GUIDE.md` §5, `docs/operations/known-limitations.md`.
