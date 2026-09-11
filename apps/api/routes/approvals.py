"""Approval endpoints — durable gate (P0-1).

List/decide operate on persisted ``Approval`` rows. Decide requires
``approver`` + ``schema_version`` + ``Idempotency-Key``, enforces expiry,
and returns 409 on double-decide via ``ApprovalStore``.
"""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, Depends, Header
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field

from apps.api.dependencies import get_identity
from apps.api.store import DECIDE_IDEMPOTENCY, RUN_STATES, RepositoryStore, get_repository
from apps.orchestrator.graph import (
    FakeA2AClient,
    FakeMCPClient,
    FakeModelClient,
    FakePolicyClient,
    GraphDeps,
    OrchestratorGraph,
)
from packages.contracts import EventType
from packages.persistence.repositories import ConflictError, NotFoundError
from packages.security.authz import require_scopes
from packages.security.identity import Identity

router = APIRouter(tags=["approvals"])


class DecideRequest(BaseModel):
    decision: str  # approved | rejected
    approver: str = Field(min_length=1, max_length=256)
    reason: str | None = Field(default=None, max_length=2000)
    schema_version: str = "1.0"


def _envelope(code: str, message: str) -> dict[str, object]:
    return {"code": code, "message": message, "retryable": False}


@router.get("/runs/{run_id}/approvals")
def list_approvals(run_id: str, identity: Identity = Depends(get_identity)) -> dict[str, object]:
    require_scopes(identity, ["case.read"])
    repo = get_repository()
    try:
        rows = repo.list_approvals(run_id, identity.tenant_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=_envelope("not_found", "not found")) from e
    return {"run_id": run_id, "approvals": [a.model_dump(mode="json") for a in rows]}


@router.post("/runs/{run_id}/approvals/{approval_id}/decide")
def decide(
    run_id: str,
    approval_id: str,
    body: DecideRequest,
    identity: Identity = Depends(get_identity),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    require_scopes(identity, ["case.approve"])
    if body.schema_version != "1.0":
        raise HTTPException(
            status_code=400,
            detail=_envelope("validation_error", "stale schema_version; expected 1.0"),
        )
    if not body.approver or not body.approver.strip():
        raise HTTPException(
            status_code=400, detail=_envelope("validation_error", "approver is required")
        )
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail=_envelope("validation_error", "Idempotency-Key header is required"),
        )
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(
            status_code=400,
            detail=_envelope("validation_error", "decision must be approved|rejected"),
        )
    # Decide idempotency: same (approval, key) replays the stored outcome.
    cache_key = (approval_id, idempotency_key)
    if cache_key in DECIDE_IDEMPOTENCY:
        cached = dict(DECIDE_IDEMPOTENCY[cache_key])
        if cached.get("run_id") == run_id:
            return cached

    repo = get_repository()
    try:
        repo.get_run(run_id, identity.tenant_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=_envelope("not_found", "not found")) from e
    try:
        decided = repo.decide_approval(
            approval_id=approval_id,
            tenant_id=identity.tenant_id,
            decision=body.decision,
            approver=body.approver,
            reason=body.reason,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=_envelope("not_found", "not found")) from e
    except ConflictError as e:
        msg = str(e).lower()
        code = "approval_expired" if "expir" in msg else "conflict"
        raise HTTPException(status_code=409, detail=_envelope(code, str(e))) from e
    if decided.run_id != run_id:
        raise HTTPException(status_code=404, detail=_envelope("not_found", "not found"))

    with contextlib.suppress(NotFoundError, ConflictError):
        repo.append_next_event(
            run_id=run_id,
            tenant_id=identity.tenant_id,
            type=EventType.APPROVAL_RECEIVED,
            data={
                "approval_id": approval_id,
                "decision": body.decision,
                "approver": body.approver,
            },
        )

    result: dict[str, object] = {
        "approval_id": approval_id,
        "run_id": run_id,
        "decision": body.decision,
        "approver": body.approver,
        "reason": body.reason,
    }
    DECIDE_IDEMPOTENCY[cache_key] = dict(result)
    _maybe_resume(run_id, identity.tenant_id, body.decision)
    return result


def _maybe_resume(run_id: str, tenant_id: str, decision: str) -> None:
    """Resume a paused run after a human decision (best-effort, synchronous)."""
    state = RUN_STATES.get(run_id)
    if state is None:
        return
    try:
        # Sync cached approval dicts with the persisted decision.
        repo = get_repository()
        try:
            rows = repo.list_approvals(run_id, tenant_id)
            by_id = {a.approval_id: a for a in rows}
            for ap in getattr(state, "approvals", []) or []:
                row = by_id.get(str(ap.get("approval_id")))
                if row is not None:
                    ap["decision"] = row.decision.value
                    ap["approver"] = row.approver
                    ap["reason"] = row.reason
        except Exception:
            pass
        if getattr(state, "current_state", "") != "approval_pause":
            return
        if decision == "rejected":
            state.status = "completed"
            state.outcome = "rejected"
            RepositoryStore(repo, run_id, tenant_id).save(state)
            try:
                repo.append_next_event(
                    run_id=run_id,
                    tenant_id=tenant_id,
                    type=EventType.RUN_COMPLETED,
                    data={"outcome": "rejected", "executed": False},
                )
                repo.update_run(run_id, tenant_id, status="completed")
            except Exception:
                pass
            return
        # Approved -> continue synchronously without re-emitting approval.received.
        state.current_state = "execute_approved"
        state.status = "running"
        graph = OrchestratorGraph(
            GraphDeps(
                mcp=FakeMCPClient(),  # type: ignore[arg-type]
                a2a=FakeA2AClient(),  # type: ignore[arg-type]
                policy=FakePolicyClient(),
                store=RepositoryStore(repo, run_id, tenant_id),  # type: ignore[arg-type]
                model=FakeModelClient(),
                limits=state.limits,
            )
        )
        graph.run(state)
    except Exception:
        pass
