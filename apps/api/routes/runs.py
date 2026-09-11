"""Run endpoints — durable gateway (P0-1).

POST /v1/runs resolves identity, creates a Run row with Idempotency-Key
dedupe, initializes budgets, dispatches the orchestrator graph, and returns
``{run_id, status, stream_url, schema_version}``.

GET /v1/runs/{id} and /events replay from the repository (ordered,
``after_sequence`` + ``Last-Event-ID``). Cancel updates the row and
propagates to worker/A2A.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from apps.api.dependencies import get_identity
from apps.api.store import (
    RUN_STATES,
    RepositoryStore,
    get_repository,
    request_cancel,
)
from apps.orchestrator.graph import (
    FakeA2AClient,
    FakeMCPClient,
    FakeModelClient,
    FakePolicyClient,
    GraphDeps,
    OrchestratorGraph,
)
from apps.orchestrator.runtime import BudgetTracker
from apps.orchestrator.state import BudgetLimits, RunState
from packages.contracts import AgentRequest, EventType
from packages.persistence.repositories import ConflictError, NotFoundError
from packages.security.authz import require_scopes
from packages.security.identity import Identity

router = APIRouter(tags=["runs"])

SCHEMA_VERSION = "1.0"
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
TERMINAL_EVENTS = {
    EventType.RUN_COMPLETED.value,
    EventType.RUN_FAILED.value,
    EventType.RUN_CANCELLED.value,
}


class CreateRunRequest(BaseModel):
    objective: str = Field(max_length=8000, min_length=1)
    context: dict[str, object] = Field(default_factory=dict)
    max_tool_calls: int = Field(default=20, ge=1, le=100)
    max_model_calls: int = Field(default=10, ge=1, le=50)
    max_cost_usd: float = Field(default=2.0, ge=0.0, le=1000.0)
    schema_version: str = "1.0"


def _envelope(code: str, message: str) -> dict[str, object]:
    return {"code": code, "message": message, "retryable": False}


def _new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


@router.post("/runs", status_code=202)
def create_run(
    body: CreateRunRequest,
    request: Request,
    background: BackgroundTasks,
    identity: Identity = Depends(get_identity),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    require_scopes(identity, ["case.write"])
    if body.schema_version != SCHEMA_VERSION:
        raise HTTPException(
            status_code=400,
            detail=_envelope("validation_error", "stale schema_version; expected 1.0"),
        )
    repo = get_repository()
    tenant_id, user_id = identity.tenant_id, identity.user_id
    run_id = _new_run_id()
    budget: dict[str, Any] = {
        "max_tool_calls": body.max_tool_calls,
        "max_model_calls": body.max_model_calls,
        "max_cost_usd": body.max_cost_usd,
        "consumed_tool_calls": 0,
        "consumed_model_calls": 0,
        "consumed_cost_usd": 0.0,
    }
    # Validate through the versioned contract + init budget ledger.
    agent_request = AgentRequest(
        run_id=run_id,
        tenant_id=tenant_id,
        user_id=user_id,
        objective=body.objective,
        context=dict(body.context),
        max_tool_calls=body.max_tool_calls,
        max_model_calls=body.max_model_calls,
        max_cost_usd=body.max_cost_usd,
        idempotency_key=idempotency_key,
    )
    tracker = BudgetTracker.from_budget_dict(
        {
            "max_tool_calls": agent_request.max_tool_calls,
            "max_model_calls": agent_request.max_model_calls,
            "max_cost_usd": agent_request.max_cost_usd,
        }
    )
    tracker.check()

    row = repo.create_run(
        run_id=run_id,
        tenant_id=tenant_id,
        created_by=user_id,
        objective=body.objective,
        budget=budget,
        idempotency_key=idempotency_key,
    )
    if row.id != run_id:
        # Idempotent replay: same (tenant, Idempotency-Key) -> original row.
        return {
            "run_id": row.id,
            "status": row.status,
            "stream_url": f"/v1/runs/{row.id}/events",
            "schema_version": SCHEMA_VERSION,
        }

    repo.append_next_event(
        run_id=row.id,
        tenant_id=tenant_id,
        type=EventType.RUN_STARTED,
        data={"objective": body.objective},
    )
    state = RunState(
        run_id=row.id,
        tenant_id=tenant_id,
        objective=body.objective,
        context=dict(body.context),
        limits=BudgetLimits(
            max_model_calls=body.max_model_calls,
            max_tool_calls=body.max_tool_calls,
            max_cost_usd=body.max_cost_usd,
        ),
        budget=dict(budget),
    )
    RUN_STATES[row.id] = state
    # Dispatch execution as a background worker task (enqueue semantics);
    # the create contract stays stable: 202 {run_id, created, stream_url}.
    background.add_task(_execute_run, row.id, tenant_id)
    return {
        "run_id": row.id,
        "status": "created",
        "stream_url": f"/v1/runs/{row.id}/events",
        "schema_version": SCHEMA_VERSION,
    }


def _execute_run(run_id: str, tenant_id: str) -> None:
    """Worker dispatch: run the bounded graph against the durable store."""
    from apps.api.store import CANCEL_FLAGS

    repo = get_repository()
    state = RUN_STATES.get(run_id)
    if state is None:
        return
    if run_id in CANCEL_FLAGS:
        state.cancelled = True
    store = RepositoryStore(repo, run_id, tenant_id)
    graph = OrchestratorGraph(
        GraphDeps(
            mcp=FakeMCPClient(),  # type: ignore[arg-type]
            a2a=FakeA2AClient(),  # type: ignore[arg-type]
            policy=FakePolicyClient(),
            store=store,  # type: ignore[arg-type]
            model=FakeModelClient(),
            limits=state.limits,
        )
    )
    with contextlib.suppress(Exception):
        graph.run(state)


def _run_detail(run_id: str, tenant_id: str) -> dict[str, object]:
    repo = get_repository()
    try:
        row = repo.get_run(run_id, tenant_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=_envelope("not_found", "not found")) from e
    try:
        approvals = [a.model_dump(mode="json") for a in repo.list_approvals(run_id, tenant_id)]
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=_envelope("not_found", "not found")) from e
    state = RUN_STATES.get(run_id)
    if state is not None:
        findings = [dict(f) for f in list(getattr(state, "findings", []) or [])]
        proposed = [dict(a) for a in list(getattr(state, "proposed_actions", []) or [])]
        current_state = str(getattr(state, "current_state", row.current_state))
        # DB row is authoritative for terminal cancel (propagated post-execution).
        status = str(getattr(state, "status", row.status))
        if row.status == "cancelled":
            status = "cancelled"
    else:
        findings, proposed = _findings_from_events(run_id, tenant_id)
        current_state, status = row.current_state, row.status
    return {
        "run_id": run_id,
        "status": status,
        "current_state": current_state,
        "findings": findings,
        "proposed_actions": proposed,
        "approvals": approvals,
        "budgets": dict(row.budget_json or {}),
        "trace_id": row.trace_id,
    }


def _findings_from_events(run_id: str, tenant_id: str) -> tuple[list[dict], list[dict]]:
    repo = get_repository()
    try:
        events = repo.list_events(run_id, tenant_id)
    except NotFoundError:
        return [], []
    findings: list[dict] = []
    proposed: list[dict] = []
    for e in events:
        if e.type == EventType.FINDING_CREATED:
            f = (e.data or {}).get("finding")
            if isinstance(f, dict):
                findings.append(f)
        elif e.type == EventType.APPROVAL_REQUIRED:
            acts = (e.data or {}).get("actions")
            if isinstance(acts, list):
                proposed.extend([a for a in acts if isinstance(a, dict)])
    return findings, proposed


@router.get("/runs/{run_id}")
def get_run(run_id: str, identity: Identity = Depends(get_identity)) -> dict[str, object]:
    require_scopes(identity, ["case.read"])
    return _run_detail(run_id, identity.tenant_id)


@router.post("/runs/{run_id}/cancel", status_code=202)
def cancel_run(run_id: str, identity: Identity = Depends(get_identity)) -> dict[str, str]:
    require_scopes(identity, ["case.write"])
    repo = get_repository()
    try:
        row = repo.get_run(run_id, identity.tenant_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=_envelope("not_found", "not found")) from e
    if row.status != "cancelled":
        repo.update_run(run_id, identity.tenant_id, status="cancelled")
        with contextlib.suppress(NotFoundError, ConflictError):
            repo.append_next_event(
                run_id=run_id,
                tenant_id=identity.tenant_id,
                type=EventType.RUN_CANCELLED,
                data={"reason": "cancel requested"},
            )
    # Propagate to worker/A2A (in-process hook + durable flag).
    request_cancel(run_id)
    cached = RUN_STATES.get(run_id)
    if cached is not None:
        with contextlib.suppress(Exception):
            cached.status = "cancelled"
    try:
        from apps.worker.worker import propagate_cancel as _propagate

        _propagate(run_id)
    except Exception:
        pass
    return {"run_id": run_id, "status": "cancelled"}


@router.get("/runs/{run_id}/events")
def run_events(
    run_id: str,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    identity: Identity = Depends(get_identity),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> Any:
    require_scopes(identity, ["case.read"])
    repo = get_repository()
    try:
        repo.get_run(run_id, identity.tenant_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=_envelope("not_found", "not found")) from e
    effective_after = after_sequence
    header_val = last_event_id or request.headers.get("last-event-id")
    if after_sequence == 0 and header_val:
        try:
            effective_after = int(header_val)
        except ValueError:
            seq = _sequence_for_event_id(repo, run_id, identity.tenant_id, header_val)
            if seq is not None:
                effective_after = seq
    try:
        events = repo.list_events(run_id, identity.tenant_id, after_sequence=effective_after)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=_envelope("not_found", "not found")) from e
    payload = [e.model_dump(mode="json") for e in events]
    accept = request.headers.get("accept", "")
    if "text/event-stream" in accept:
        return _sse_response(run_id, payload)
    return {"run_id": run_id, "after_sequence": effective_after, "events": payload}


def _sequence_for_event_id(repo: Any, run_id: str, tenant_id: str, event_id: str) -> int | None:
    try:
        events = repo.list_events(run_id, tenant_id)
    except Exception:
        return None
    for e in events:
        if e.event_id == event_id:
            return int(e.sequence)
    return None


def _sse_response(run_id: str, events: list[dict[str, Any]]) -> StreamingResponse:
    terminal = any(str(e.get("type")) in TERMINAL_EVENTS for e in events)

    def _gen():
        for e in events:
            yield f"id: {e.get('sequence', 0)}\nevent: {e.get('type')}\ndata: {json.dumps(e)}\n\n"
        if terminal:
            yield "event: close\ndata: {}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")
