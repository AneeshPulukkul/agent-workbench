# AG-UI Mapping

> Spec §8. Canonical internal events (§4.2) mapped to AG-UI at API boundary (`packages/protocols/ag_ui.py`); UI tolerates unknown future types; `sensitive` gated.

## Mapping table

| Canonical `EventType` | AG-UI-facing rendering | UI component |
|---|---|---|
| `run.started` | `RUN_STARTED` + run metadata | header/status |
| `message.delta` | `TEXT_MESSAGE_CONTENT` (stream chunks) | assistant stream |
| `tool.started/completed` | `TOOL_CALL_START/FINISH` (name + redacted args, no secrets) | tool timeline |
| `agent.delegated` (+ completion) | `ACTIVITY` / sub-agent timeline entry | delegation timeline |
| `finding.created` | `STATE_SNAPSHOT` card (title/summary/confidence/citations) | findings panel |
| `approval.required` | `FRONTEND_INTERACTION` approval card (action, reason, risk, rollback, approve/reject) | approval card |
| `approval.received` | `STATE_UPDATE` (decision + audit ref) | status |
| `run.completed/failed` | `RUN_FINISHED/ERROR` + result summary | result + replay entry |

Example: canonical `approval.required {action_id, tool_name: deployment.rollback, reason, risk: medium, rollback}` → AG-UI interaction event rendering the approval component, not raw JSON (§8.2).

## Frontend capabilities (§8.1)

Streamed assistant output, tool + delegation timelines, findings/evidence, approval cards, cancel, reconnect-after-refresh, replay from persisted events, error/timeout states, run metadata. Rules: render only `safe_for_ui=true`; unknown types → generic collapsible (forward-compat); citations link to `evidence_refs`.

## Reconnect & replay

`GET /v1/runs/{id}/events?after_sequence=N` (+ `Last-Event-ID`): ordered catch-up then live; terminal event if done; per-tenant/run authZ. UI restores state purely from event replay (history-restore friendly per AG-UI serialization).
