"""Gateway persistence + execution wiring (Spec §9, ADR-004).

Prod path uses :class:`SqlAlchemyRepository` only (sqlite fallback for local).
``InMemoryPersistence`` (apps.orchestrator.graph) is test-only and must never
be imported here.

Singletons here are process-local and safe for tests via
:func:`reset_repository`.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import sessionmaker

from packages.contracts import Approval, ApprovalDecision
from packages.persistence.models import Base
from packages.persistence.repositories import (
    ConflictError,
    SqlAlchemyRepository,
    create_all,
    get_engine,
)

_REPO: SqlAlchemyRepository | None = None
_ENGINE = None

# Process-local caches / coordination (durable state lives in the repo).
RUN_STATES: dict[str, Any] = {}
CANCEL_FLAGS: set[str] = set()
DECIDE_IDEMPOTENCY: dict[tuple[str, str], dict[str, Any]] = {}
A2A_CANCELLED: list[str] = []


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return url
    if os.getenv("APP_ENV", "local") == "test":
        return "sqlite:///:memory:"
    return "sqlite:///./workbench.db"


def get_repository() -> SqlAlchemyRepository:
    global _REPO, _ENGINE
    if _REPO is not None:
        return _REPO
    url = get_database_url()
    try:
        _ENGINE = get_engine(url)
        create_all(_ENGINE)
    except Exception:
        _ENGINE = get_engine("sqlite:///:memory:")
        create_all(_ENGINE)
    _REPO = SqlAlchemyRepository(
        sessionmaker(bind=_ENGINE, expire_on_commit=False)
    )
    return _REPO


def reset_repository(database_url: str = "sqlite:///:memory:") -> SqlAlchemyRepository:
    """Test hook: drop + recreate schema, clear caches."""
    global _REPO, _ENGINE
    _ENGINE = get_engine(database_url)
    Base.metadata.drop_all(_ENGINE)
    Base.metadata.create_all(_ENGINE)
    _REPO = SqlAlchemyRepository(
        sessionmaker(bind=_ENGINE, expire_on_commit=False)
    )
    RUN_STATES.clear()
    CANCEL_FLAGS.clear()
    DECIDE_IDEMPOTENCY.clear()
    A2A_CANCELLED.clear()
    return _REPO


def request_cancel(run_id: str) -> None:
    """Mark cancellation + propagate to cached state / A2A hook."""
    CANCEL_FLAGS.add(run_id)
    state = RUN_STATES.get(run_id)
    if state is not None:
        try:
            state.cancelled = True
        except Exception:
            pass
    task_id = f"{run_id}-obs-1"
    if task_id not in A2A_CANCELLED:
        A2A_CANCELLED.append(task_id)


class RepositoryStore:
    """Graph ``Store`` adapter backed by :class:`SqlAlchemyRepository`.

    - ``append(event_dict)`` persists via ``append_next_event`` (persist-then-publish).
    - ``save(state)`` updates the Run row + upserts approval rows + caches state.
    """

    def __init__(self, repo: SqlAlchemyRepository, run_id: str, tenant_id: str) -> None:
        self._repo = repo
        self._run_id = run_id
        self._tenant_id = tenant_id

    def save(self, state: Any) -> None:
        RUN_STATES[self._run_id] = state
        budget = {
            "max_tool_calls": int(getattr(getattr(state, "limits", None), "max_tool_calls", 20)),
            "max_model_calls": int(getattr(getattr(state, "limits", None), "max_model_calls", 10)),
            "max_cost_usd": float(getattr(getattr(state, "limits", None), "max_cost_usd", 2.0)),
            "consumed_tool_calls": int(getattr(getattr(state, "usage", None), "tool_calls", 0)),
            "consumed_model_calls": int(getattr(getattr(state, "usage", None), "model_calls", 0)),
            "consumed_cost_usd": float(getattr(getattr(state, "usage", None), "cost_usd", 0.0)),
        }
        try:
            self._repo.update_run(
                self._run_id,
                self._tenant_id,
                status=str(getattr(state, "status", "running")),
                current_state=str(getattr(state, "current_state", "")),
                budget_json=budget,
            )
        except Exception:
            pass
        # Upsert approvals so human gate rows are durable after every transition.
        for ap in list(getattr(state, "approvals", []) or []):
            try:
                self._upsert_approval(ap, state)
            except Exception:
                pass

    def append(self, event: dict[str, Any]) -> None:
        etype = str(event.get("type", "message.delta"))
        data = dict(event.get("data", {}) or {})
        try:
            self._repo.append_next_event(
                run_id=self._run_id,
                tenant_id=self._tenant_id,
                type=etype,  # type: ignore[arg-type]
                data=data,
                event_id=str(event.get("event_id") or ""),
                trace_id=event.get("trace_id"),
                correlation_id=event.get("correlation_id"),
                sensitive=bool(event.get("sensitive", False)),
                safe_for_ui=bool(event.get("safe_for_ui", False))
                and not bool(event.get("sensitive", False)),
            )
        except ConflictError:
            pass  # idempotent event_id replay
        except Exception:
            # Never break graph transitions on best-effort fan-out failure
            # after the Run row itself was persisted via save().
            pass

    def _upsert_approval(self, ap: dict[str, Any], state: Any) -> None:
        approval_id = str(ap.get("approval_id", ""))
        if not approval_id:
            return
        requested_at = _parse_dt(ap.get("requested_at")) or _now()
        expires_at = _parse_dt(ap.get("expires_at")) or (requested_at + timedelta(minutes=30))
        approval = Approval(
            approval_id=approval_id,
            run_id=self._run_id,
            tenant_id=self._tenant_id,
            action_id=str(ap.get("action_id", approval_id)),
            requested_by=str(ap.get("requested_by", "orchestrator")),
            approver=ap.get("approver"),
            decision=_coerce_decision(ap.get("decision", "pending")),
            reason=ap.get("reason"),
            requested_at=requested_at,
            decided_at=_parse_dt(ap.get("decided_at")),
            expires_at=expires_at,
            trace_id=getattr(state, "trace_id", None),
            correlation_id=getattr(state, "correlation_id", None),
        )
        try:
            self._repo.create_approval(approval)
        except ConflictError:
            pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(v))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _coerce_decision(v: Any) -> ApprovalDecision:
    try:
        return ApprovalDecision(str(v))
    except ValueError:
        return ApprovalDecision.PENDING


__all__ = [
    "A2A_CANCELLED",
    "CANCEL_FLAGS",
    "DECIDE_IDEMPOTENCY",
    "RUN_STATES",
    "RepositoryStore",
    "get_database_url",
    "get_repository",
    "request_cancel",
    "reset_repository",
]
