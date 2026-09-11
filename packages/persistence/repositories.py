"""Repository interfaces + SQLAlchemy implementation (Spec §9, ADR-004).

Guarantees:
- Persist-then-publish: every mutating method commits before returning.
- Idempotent event append on ``event_id``; (run_id, sequence) unique —
  conflicting sequence with a different event_id raises ConflictError.
- ``next_sequence`` is MAX(sequence)+1 per run (starts at 0). The UNIQUE
  constraint is the arbiter under races; ``append_next_event`` retries
  sequence collisions (not tool writes — never auto-retry those).
- Replay: ``list_events(run_id, tenant, after_sequence)`` ordered ASC.
- Tenant isolation: reads return NotFoundError on tenant mismatch (no
  existence leak, per api-contracts.md); writes verify tenant.
- Redaction hook: raw payloads are NEVER stored. Callers pass raw dicts;
  the repository stores ``redacted_*`` + sha256 hashes. The hook is
  injectable for tests/policy tightening.

Transaction boundaries: each public method is one transaction
(session per call, commit on success / rollback on error). ``transaction()``
context manager is available for multi-step atomic units.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from packages.contracts import AgentEvent, Approval, EventType, ToolInvocation
from packages.contracts.approvals import ApprovalDecision
from packages.contracts.tools import ToolInvocationStatus

from .models import (
    AgentTask,
    ApprovalRow,
    AuditRecord,
    Base,
    EvidenceRef,
    FindingRow,
    ModelCall,
    Run,
    RunEvent,
    Tenant,
    ToolInvocationRow,
)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RepositoryError(RuntimeError):
    pass


class NotFoundError(RepositoryError):
    pass


class ConflictError(RepositoryError):
    pass


# ---------------------------------------------------------------------------
# Redaction + hashing (never store secrets)
# ---------------------------------------------------------------------------

REDACTED = "***REDACTED***"

# Keys whose values must never be persisted in cleartext (case-insensitive,
# substring match on the dotted key path).
_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"client[_-]?secret|auth|authorization|bearer|cookie|sessionid|"
    r"connection[_-]?string|dsn|credit[_-]?card|ssn)",
    re.IGNORECASE,
)

# Loose secret-shaped values: bearer tokens, long base64-ish blobs, PEM blocks.
_SECRET_VALUE_RES = [
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}\b"),
]


def _redact_value(key_path: str, value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _redact_value(f"{key_path}.{k}" if key_path else str(k), v) for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact_value(key_path, v) for v in value]
    if isinstance(value, str):
        if key_path and _SENSITIVE_KEY_RE.search(key_path):
            return REDACTED
        for rx in _SECRET_VALUE_RES:
            if rx.search(value):
                return REDACTED
        # Long opaque blobs that look like keys/tokens: redact, keep hash.
        if len(value) > 64 and re.fullmatch(r"[A-Za-z0-9\-_+/=]{64,}", value.strip()):
            return REDACTED
        return value
    return value


def default_redact(obj: Any) -> Any:
    """Deep-copy + redact. Never mutates the caller's object."""
    if isinstance(obj, dict):
        return {k: _redact_value(str(k), v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_redact_value("", v) for v in obj]
    return _redact_value("", obj)


def hash_payload(payload: Any) -> str:
    """Stable sha256 over canonical JSON (sort_keys, str fallback)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _contains_bare_secret_marker(obj: Any) -> bool:
    """Best-effort guard: reject persisting values that still look secret-y
    under a *sensitive key name* (post-redaction this must be empty)."""

    def walk(o: Any, path: str = "") -> bool:
        if isinstance(o, dict):
            return any(walk(v, f"{path}.{k}") for k, v in o.items())
        if isinstance(o, list | tuple):
            return any(walk(v, path) for v in o)
        if isinstance(o, str) and path and _SENSITIVE_KEY_RE.search(path):
            return o != REDACTED
        return False

    return walk(obj)


RedactFn = Callable[[Any], Any]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite strips tzinfo — re-attach UTC so Pydantic AwareDatetime passes."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


# ---------------------------------------------------------------------------
# Engine helpers (PG primary, sqlite fallback for tests)
# ---------------------------------------------------------------------------


def get_engine(database_url: str, **kwargs: Any):
    kwargs.setdefault("future", True)
    if database_url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"check_same_thread": False})
        # StaticPool keeps :memory: alive across sessions in tests.
        from sqlalchemy.pool import StaticPool

        kwargs.setdefault("poolclass", StaticPool)
    return create_engine(database_url, **kwargs)


def create_all(engine) -> None:
    Base.metadata.create_all(engine)


# ---------------------------------------------------------------------------
# Interfaces (Protocols — API layer depends on these, not SQLAlchemy)
# ---------------------------------------------------------------------------


class RunStore(Protocol):
    def ensure_tenant(self, tenant_id: str, name: str = "") -> None: ...
    def create_run(
        self,
        *,
        run_id: str,
        tenant_id: str,
        created_by: str,
        objective: str,
        budget: dict[str, Any],
        current_state: str = "created",
        trace_id: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Run: ...
    def get_run(self, run_id: str, tenant_id: str) -> Run: ...
    def update_run(self, run_id: str, tenant_id: str, **fields: Any) -> Run: ...


class EventStore(Protocol):
    def next_sequence(self, run_id: str) -> int: ...
    def append_event(self, event: AgentEvent) -> AgentEvent: ...
    def append_next_event(
        self,
        *,
        run_id: str,
        tenant_id: str,
        type: EventType | str,
        data: dict[str, Any],
        event_id: str | None = None,
        trace_id: str | None = None,
        correlation_id: str | None = None,
        sensitive: bool = False,
        safe_for_ui: bool = False,
    ) -> AgentEvent: ...
    def list_events(
        self, run_id: str, tenant_id: str, after_sequence: int = -1, limit: int = 1000
    ) -> list[AgentEvent]: ...


class ApprovalStore(Protocol):
    def create_approval(self, approval: Approval) -> Approval: ...
    def get_approval(self, approval_id: str, tenant_id: str) -> Approval: ...
    def decide_approval(
        self,
        *,
        approval_id: str,
        tenant_id: str,
        decision: str,
        approver: str,
        reason: str | None = None,
    ) -> Approval: ...


class ToolAuditStore(Protocol):
    def record_tool_invocation(
        self,
        invocation: ToolInvocation,
        raw_input: dict[str, Any] | None = None,
        raw_output: dict[str, Any] | None = None,
    ) -> ToolInvocation: ...
    def complete_tool_invocation(
        self,
        *,
        invocation_id: str,
        tenant_id: str,
        status: str,
        raw_output: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> ToolInvocation: ...


# ---------------------------------------------------------------------------
# SQLAlchemy implementation
# ---------------------------------------------------------------------------


class SqlAlchemyRepository:
    """Single concrete repo implementing all store Protocols."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        redact: RedactFn = default_redact,
    ) -> None:
        self._factory = session_factory
        self._redact = redact

    @classmethod
    def from_url(cls, database_url: str, redact: RedactFn = default_redact) -> SqlAlchemyRepository:
        engine = get_engine(database_url)
        create_all(engine)
        return cls(sessionmaker(bind=engine, expire_on_commit=False), redact=redact)

    # -- sessions / transactions -----------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _session(self) -> Session:
        return self._factory()

    # -- tenants / runs ----------------------------------------------------
    def ensure_tenant(self, tenant_id: str, name: str = "") -> None:
        with self.transaction() as s:
            if s.get(Tenant, tenant_id) is None:
                s.add(Tenant(id=tenant_id, name=name or tenant_id, created_at=_now()))

    def _get_run_row(self, s: Session, run_id: str, tenant_id: str) -> Run:
        row = s.get(Run, run_id)
        if row is None or row.tenant_id != tenant_id:
            raise NotFoundError(f"run {run_id} not found")
        return row

    def create_run(
        self,
        *,
        run_id: str,
        tenant_id: str,
        created_by: str,
        objective: str,
        budget: dict[str, Any],
        current_state: str = "created",
        trace_id: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Run:
        # Idempotent on (tenant, idempotency_key): return existing run.
        with self.transaction() as s:
            self._ensure_tenant_in(s, tenant_id)
            if idempotency_key:
                existing = (
                    s.execute(
                        select(Run).where(
                            Run.tenant_id == tenant_id,
                            Run.idempotency_key == idempotency_key,
                        )
                    )
                    .scalars()
                    .first()
                )
                if existing is not None:
                    return existing
            if s.get(Run, run_id) is not None:
                raise ConflictError(f"run {run_id} already exists")
            row = Run(
                id=run_id,
                tenant_id=tenant_id,
                created_by=created_by,
                objective=objective,
                status="created",
                current_state=current_state,
                budget_json=budget,
                idempotency_key=idempotency_key,
                trace_id=trace_id,
                correlation_id=correlation_id,
                created_at=_now(),
                updated_at=_now(),
            )
            s.add(row)
            s.flush()
            return row

    def get_run(self, run_id: str, tenant_id: str) -> Run:
        with self._session() as s:
            row = self._get_run_row(s, run_id, tenant_id)
            s.expunge(row)
            return row

    def update_run(self, run_id: str, tenant_id: str, **fields: Any) -> Run:
        allowed = {
            "status",
            "current_state",
            "budget_json",
            "completed_at",
            "trace_id",
            "correlation_id",
            "objective",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise RepositoryError(f"cannot update fields: {sorted(unknown)}")
        with self.transaction() as s:
            row = self._get_run_row(s, run_id, tenant_id)
            for k, v in fields.items():
                setattr(row, k, v)
            row.updated_at = _now()
            s.flush()
            s.expunge(row)
            return row

    @staticmethod
    def _ensure_tenant_in(s: Session, tenant_id: str) -> None:
        if s.get(Tenant, tenant_id) is None:
            s.add(Tenant(id=tenant_id, name=tenant_id, created_at=_now()))
            s.flush()

    # -- events --------------------------------------------------------------
    def next_sequence(self, run_id: str) -> int:
        with self._session() as s:
            mx = s.execute(
                select(func.coalesce(func.max(RunEvent.sequence), -1)).where(
                    RunEvent.run_id == run_id
                )
            ).scalar()
            return (int(mx) if mx is not None else -1) + 1

    def _next_sequence_in(self, s: Session, run_id: str) -> int:
        mx = s.execute(
            select(func.coalesce(func.max(RunEvent.sequence), -1)).where(RunEvent.run_id == run_id)
        ).scalar()
        return (int(mx) if mx is not None else -1) + 1

    def append_event(self, event: AgentEvent) -> AgentEvent:
        """Idempotent on event_id. Sequence conflict -> ConflictError."""
        if event.sensitive and event.safe_for_ui:
            raise RepositoryError("sensitive events must not be safe_for_ui")
        with self.transaction() as s:
            run = s.get(Run, event.run_id)
            if run is None or run.tenant_id != event.tenant_id:
                raise NotFoundError(f"run {event.run_id} not found")
            dup = (
                s.execute(select(RunEvent).where(RunEvent.event_id == event.event_id))
                .scalars()
                .first()
            )
            if dup is not None:
                return self._row_to_event(dup)
            row = RunEvent(
                event_id=event.event_id,
                run_id=event.run_id,
                tenant_id=event.tenant_id,
                sequence=event.sequence,
                type=event.type.value if isinstance(event.type, EventType) else str(event.type),
                timestamp=event.timestamp,
                data_json=dict(event.data),
                trace_id=event.trace_id,
                correlation_id=event.correlation_id,
                sensitive=event.sensitive,
                safe_for_ui=event.safe_for_ui,
            )
            s.add(row)
            try:
                s.flush()
            except IntegrityError as e:
                # Sequence collision (unique run_id+sequence) or event_id race.
                s.rollback()
                raise ConflictError(
                    f"event sequence conflict run={event.run_id} seq={event.sequence}"
                ) from e
            return event

    def append_next_event(
        self,
        *,
        run_id: str,
        tenant_id: str,
        type: EventType | str,
        data: dict[str, Any],
        event_id: str | None = None,
        trace_id: str | None = None,
        correlation_id: str | None = None,
        sensitive: bool = False,
        safe_for_ui: bool = False,
    ) -> AgentEvent:
        """Assign next sequence + insert atomically; retry seq collisions."""
        if sensitive and safe_for_ui:
            raise RepositoryError("sensitive events must not be safe_for_ui")
        # Redact event data defensively (never persist raw secrets even in
        # free-form data); hashes are the caller's concern for large blobs.
        redacted_data = self._redact(dict(data))
        last_exc: Exception | None = None
        for _ in range(4):  # initial + 3 retries on sequence race only
            with self.transaction() as s:
                run = s.get(Run, run_id)
                if run is None or run.tenant_id != tenant_id:
                    raise NotFoundError(f"run {run_id} not found")
                eid = event_id or new_id("evt")
                dup = s.execute(select(RunEvent).where(RunEvent.event_id == eid)).scalars().first()
                if dup is not None:
                    return self._row_to_event(dup)
                seq = self._next_sequence_in(s, run_id)
                row = RunEvent(
                    event_id=eid,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    sequence=seq,
                    type=type.value if isinstance(type, EventType) else str(type),
                    timestamp=_now(),
                    data_json=redacted_data,
                    trace_id=trace_id,
                    correlation_id=correlation_id,
                    sensitive=sensitive,
                    safe_for_ui=safe_for_ui,
                )
                s.add(row)
                try:
                    s.flush()
                except IntegrityError as e:
                    last_exc = e
                    continue  # sequence race -> recompute and retry
                return AgentEvent(
                    event_id=eid,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    sequence=seq,
                    type=EventType(row.type),
                    timestamp=row.timestamp,
                    data=redacted_data,
                    trace_id=trace_id,
                    correlation_id=correlation_id,
                    sensitive=sensitive,
                    safe_for_ui=safe_for_ui,
                )
        raise ConflictError(f"could not assign event sequence for run {run_id}") from last_exc

    def list_events(
        self, run_id: str, tenant_id: str, after_sequence: int = -1, limit: int = 1000
    ) -> list[AgentEvent]:
        if limit < 1 or limit > 5000:
            raise RepositoryError("limit must be 1..5000")
        with self._session() as s:
            run = s.get(Run, run_id)
            if run is None or run.tenant_id != tenant_id:
                raise NotFoundError(f"run {run_id} not found")
            rows = (
                s.execute(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id, RunEvent.sequence > after_sequence)
                    .order_by(RunEvent.sequence.asc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [self._row_to_event(r) for r in rows]

    @staticmethod
    def _row_to_event(r: RunEvent) -> AgentEvent:
        return AgentEvent(
            event_id=r.event_id,
            run_id=r.run_id,
            tenant_id=r.tenant_id,
            sequence=r.sequence,
            type=EventType(r.type),
            timestamp=_aware(r.timestamp),  # type: ignore[arg-type]
            data=dict(r.data_json or {}),
            trace_id=r.trace_id,
            correlation_id=r.correlation_id,
            sensitive=bool(r.sensitive),
            safe_for_ui=bool(r.safe_for_ui),
        )

    # -- approvals -------------------------------------------------------------
    def create_approval(self, approval: Approval) -> Approval:
        with self.transaction() as s:
            run = s.get(Run, approval.run_id)
            if run is None or run.tenant_id != approval.tenant_id:
                raise NotFoundError(f"run {approval.run_id} not found")
            if (
                s.execute(
                    select(ApprovalRow).where(ApprovalRow.approval_id == approval.approval_id)
                )
                .scalars()
                .first()
                is not None
            ):
                raise ConflictError(f"approval {approval.approval_id} already exists")
            s.add(
                ApprovalRow(
                    approval_id=approval.approval_id,
                    run_id=approval.run_id,
                    tenant_id=approval.tenant_id,
                    action_id=approval.action_id,
                    requested_by=approval.requested_by,
                    approver=approval.approver,
                    decision=approval.decision.value,
                    reason=approval.reason,
                    requested_at=approval.requested_at,
                    decided_at=approval.decided_at,
                    expires_at=approval.expires_at,
                    trace_id=approval.trace_id,
                    correlation_id=approval.correlation_id,
                )
            )
            s.flush()
            return approval

    def get_approval(self, approval_id: str, tenant_id: str) -> Approval:
        with self._session() as s:
            row = (
                s.execute(select(ApprovalRow).where(ApprovalRow.approval_id == approval_id))
                .scalars()
                .first()
            )
            if row is None or row.tenant_id != tenant_id:
                raise NotFoundError(f"approval {approval_id} not found")
            return self._row_to_approval(row)

    def decide_approval(
        self,
        *,
        approval_id: str,
        tenant_id: str,
        decision: str,
        approver: str,
        reason: str | None = None,
    ) -> Approval:
        if decision not in ("approved", "rejected"):
            raise RepositoryError("decision must be approved|rejected")
        with self.transaction() as s:
            row = (
                s.execute(select(ApprovalRow).where(ApprovalRow.approval_id == approval_id))
                .scalars()
                .first()
            )
            if row is None or row.tenant_id != tenant_id:
                raise NotFoundError(f"approval {approval_id} not found")
            if row.decision != ApprovalDecision.PENDING.value:
                raise ConflictError(f"approval {approval_id} already decided")
            now = _now()
            expires = _aware(row.expires_at)
            if expires is not None and now > expires:
                row.decision = ApprovalDecision.EXPIRED.value
                row.decided_at = now
                s.flush()
                raise ConflictError(f"approval {approval_id} expired")
            row.decision = decision
            row.approver = approver
            row.reason = reason
            row.decided_at = now
            s.flush()
            return self._row_to_approval(row)

    @staticmethod
    def _row_to_approval(r: ApprovalRow) -> Approval:
        return Approval(
            approval_id=r.approval_id,
            run_id=r.run_id,
            tenant_id=r.tenant_id,
            action_id=r.action_id,
            requested_by=r.requested_by,
            approver=r.approver,
            decision=ApprovalDecision(r.decision),
            reason=r.reason,
            requested_at=_aware(r.requested_at),  # type: ignore[arg-type]
            decided_at=_aware(r.decided_at),
            expires_at=_aware(r.expires_at),
            trace_id=r.trace_id,
            correlation_id=r.correlation_id,
        )

    def list_approvals(self, run_id: str, tenant_id: str) -> list[Approval]:
        with self._session() as s:
            run = s.get(Run, run_id)
            if run is None or run.tenant_id != tenant_id:
                raise NotFoundError(f"run {run_id} not found")
            rows = (
                s.execute(
                    select(ApprovalRow)
                    .where(ApprovalRow.run_id == run_id)
                    .order_by(ApprovalRow.requested_at.asc())
                )
                .scalars()
                .all()
            )
            return [self._row_to_approval(r) for r in rows]

    # -- tool audit --------------------------------------------------------------
    def record_tool_invocation(
        self,
        invocation: ToolInvocation,
        raw_input: dict[str, Any] | None = None,
        raw_output: dict[str, Any] | None = None,
    ) -> ToolInvocation:
        """Persist audit row. Raw payloads are redacted+hashed; only the
        redacted form + hashes are stored."""
        source_input = raw_input if raw_input is not None else dict(invocation.redacted_input)
        redacted_in = self._redact(dict(source_input))
        if _contains_bare_secret_marker(redacted_in):
            raise RepositoryError("redaction hook failed to mask secrets")
        in_hash = hash_payload(source_input)
        redacted_out: dict[str, Any] | None = None
        out_hash = invocation.output_hash
        if raw_output is not None:
            redacted_out = self._redact(dict(raw_output))
            out_hash = hash_payload(raw_output)
        with self.transaction() as s:
            run = s.get(Run, invocation.run_id)
            if run is None or run.tenant_id != invocation.tenant_id:
                raise NotFoundError(f"run {invocation.run_id} not found")
            if invocation.idempotency_key:
                dup = (
                    s.execute(
                        select(ToolInvocationRow).where(
                            ToolInvocationRow.run_id == invocation.run_id,
                            ToolInvocationRow.idempotency_key == invocation.idempotency_key,
                        )
                    )
                    .scalars()
                    .first()
                )
                if dup is not None:
                    return self._row_to_tool_invocation(dup)
            if (
                s.execute(
                    select(ToolInvocationRow).where(
                        ToolInvocationRow.invocation_id == invocation.invocation_id
                    )
                )
                .scalars()
                .first()
                is not None
            ):
                raise ConflictError(f"tool invocation {invocation.invocation_id} exists")
            s.add(
                ToolInvocationRow(
                    invocation_id=invocation.invocation_id,
                    run_id=invocation.run_id,
                    tenant_id=invocation.tenant_id,
                    tool_name=invocation.tool_name,
                    tool_version=invocation.tool_version,
                    input_hash=in_hash,
                    redacted_input_json=redacted_in,
                    output_hash=out_hash,
                    redacted_output_json=redacted_out,
                    status=invocation.status.value,
                    authorization_decision=(
                        invocation.authorization_decision.value
                        if invocation.authorization_decision
                        else None
                    ),
                    idempotency_key=invocation.idempotency_key,
                    dry_run=invocation.dry_run,
                    error_json=(dict(invocation.error) if invocation.error else None),
                    trace_id=invocation.trace_id,
                    correlation_id=invocation.correlation_id,
                    started_at=invocation.started_at,
                    completed_at=invocation.completed_at,
                )
            )
            s.flush()
            stored = invocation.model_copy(
                update={
                    "input_hash": in_hash,
                    "redacted_input": redacted_in,
                    "output_hash": out_hash,
                }
            )
            return stored

    def complete_tool_invocation(
        self,
        *,
        invocation_id: str,
        tenant_id: str,
        status: str,
        raw_output: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> ToolInvocation:
        try:
            st = ToolInvocationStatus(status)
        except ValueError as e:
            raise RepositoryError(f"unknown tool status {status}") from e
        with self.transaction() as s:
            row = (
                s.execute(
                    select(ToolInvocationRow).where(
                        ToolInvocationRow.invocation_id == invocation_id
                    )
                )
                .scalars()
                .first()
            )
            if row is None or row.tenant_id != tenant_id:
                raise NotFoundError(f"tool invocation {invocation_id} not found")
            row.status = st.value
            if raw_output is not None:
                row.redacted_output_json = self._redact(dict(raw_output))
                row.output_hash = hash_payload(raw_output)
            if error is not None:
                row.error_json = self._redact(dict(error))
            row.completed_at = _now()
            s.flush()
            return self._row_to_tool_invocation(row)

    @staticmethod
    def _row_to_tool_invocation(r: ToolInvocationRow) -> ToolInvocation:
        from packages.contracts.tools import AuthorizationDecision

        return ToolInvocation(
            invocation_id=r.invocation_id,
            run_id=r.run_id,
            tenant_id=r.tenant_id,
            tool_name=r.tool_name,
            tool_version=r.tool_version,
            input_hash=r.input_hash,
            redacted_input=dict(r.redacted_input_json or {}),
            output_hash=r.output_hash,
            status=ToolInvocationStatus(r.status),
            authorization_decision=(
                AuthorizationDecision(r.authorization_decision)
                if r.authorization_decision
                else None
            ),
            idempotency_key=r.idempotency_key,
            dry_run=bool(r.dry_run),
            started_at=_aware(r.started_at),  # type: ignore[arg-type]
            completed_at=_aware(r.completed_at),
            trace_id=r.trace_id,
            correlation_id=r.correlation_id,
            error=dict(r.error_json) if r.error_json else None,
        )

    # -- findings / tasks / model calls / audit ----------------------------------
    def save_finding(
        self,
        *,
        finding_id: str,
        run_id: str,
        tenant_id: str,
        title: str,
        summary: str,
        evidence_refs: list[str],
        confidence: float,
        severity: str | None = None,
        trace_id: str | None = None,
        correlation_id: str | None = None,
    ) -> str:
        with self.transaction() as s:
            run = s.get(Run, run_id)
            if run is None or run.tenant_id != tenant_id:
                raise NotFoundError(f"run {run_id} not found")
            if (
                s.execute(select(FindingRow).where(FindingRow.finding_id == finding_id))
                .scalars()
                .first()
                is not None
            ):
                raise ConflictError(f"finding {finding_id} exists")
            s.add(
                FindingRow(
                    finding_id=finding_id,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    title=title,
                    summary=summary,
                    evidence_refs_json=list(evidence_refs),
                    confidence=confidence,
                    severity=severity,
                    trace_id=trace_id,
                    correlation_id=correlation_id,
                    created_at=_now(),
                )
            )
            s.flush()
            return finding_id

    def save_evidence_ref(
        self,
        *,
        ref_id: str,
        run_id: str,
        tenant_id: str,
        kind: str,
        uri: str,
        excerpt_hash: str | None = None,
        span_ref: str | None = None,
        finding_id: str | None = None,
    ) -> str:
        with self.transaction() as s:
            run = s.get(Run, run_id)
            if run is None or run.tenant_id != tenant_id:
                raise NotFoundError(f"run {run_id} not found")
            if (
                s.execute(select(EvidenceRef).where(EvidenceRef.ref_id == ref_id)).scalars().first()
                is not None
            ):
                raise ConflictError(f"evidence ref {ref_id} exists")
            s.add(
                EvidenceRef(
                    ref_id=ref_id,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    finding_id=finding_id,
                    kind=kind,
                    uri=uri,
                    excerpt_hash=excerpt_hash,
                    span_ref=span_ref,
                )
            )
            s.flush()
            return ref_id

    def save_agent_task(
        self,
        *,
        task_id: str,
        run_id: str,
        tenant_id: str,
        skill_id: str,
        agent_name: str,
        objective: str,
        status: str = "pending",
        raw_inputs: dict[str, Any] | None = None,
        deadline: datetime | None = None,
    ) -> str:
        redacted = self._redact(dict(raw_inputs or {}))
        in_hash = hash_payload(raw_inputs or {})
        with self.transaction() as s:
            run = s.get(Run, run_id)
            if run is None or run.tenant_id != tenant_id:
                raise NotFoundError(f"run {run_id} not found")
            if (
                s.execute(select(AgentTask).where(AgentTask.task_id == task_id)).scalars().first()
                is not None
            ):
                raise ConflictError(f"task {task_id} exists")
            s.add(
                AgentTask(
                    task_id=task_id,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    skill_id=skill_id,
                    agent_name=agent_name,
                    objective=objective,
                    status=status,
                    input_hash=in_hash,
                    redacted_input_json=redacted,
                    deadline=deadline,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            s.flush()
            return task_id

    def update_agent_task(
        self,
        *,
        task_id: str,
        tenant_id: str,
        status: str,
        raw_output: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction() as s:
            row = s.execute(select(AgentTask).where(AgentTask.task_id == task_id)).scalars().first()
            if row is None or row.tenant_id != tenant_id:
                raise NotFoundError(f"task {task_id} not found")
            row.status = status
            if raw_output is not None:
                row.redacted_output_json = self._redact(dict(raw_output))
                row.output_hash = hash_payload(raw_output)
            if error is not None:
                row.error_json = self._redact(dict(error))
            row.updated_at = _now()
            s.flush()

    def record_model_call(
        self,
        *,
        call_id: str | None = None,
        run_id: str,
        tenant_id: str,
        model: str,
        task_type: str = "reasoning",
        raw_prompt: Any | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: int = 0,
        status: str = "succeeded",
        trace_id: str | None = None,
    ) -> str:
        cid = call_id or new_id("mcall")
        prompt_hash = hash_payload(raw_prompt if raw_prompt is not None else {})
        with self.transaction() as s:
            run = s.get(Run, run_id)
            if run is None or run.tenant_id != tenant_id:
                raise NotFoundError(f"run {run_id} not found")
            s.add(
                ModelCall(
                    call_id=cid,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    model=model,
                    task_type=task_type,
                    prompt_hash=prompt_hash,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    duration_ms=duration_ms,
                    status=status,
                    trace_id=trace_id,
                    created_at=_now(),
                )
            )
            s.flush()
            return cid

    def write_audit(
        self,
        *,
        audit_id: str | None = None,
        run_id: str | None,
        tenant_id: str,
        actor: str,
        action: str,
        decision: dict[str, Any] | None = None,
        reason: str | None = None,
        trace_id: str | None = None,
        correlation_id: str | None = None,
    ) -> str:
        aid = audit_id or new_id("audit")
        redacted_decision = self._redact(dict(decision)) if decision else None
        with self.transaction() as s:
            if run_id is not None:
                run = s.get(Run, run_id)
                if run is None or run.tenant_id != tenant_id:
                    raise NotFoundError(f"run {run_id} not found")
            s.add(
                AuditRecord(
                    audit_id=aid,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    actor=actor,
                    action=action,
                    decision_json=redacted_decision,
                    reason=reason,
                    trace_id=trace_id,
                    correlation_id=correlation_id,
                    created_at=_now(),
                )
            )
            s.flush()
            return aid


__all__ = [
    "REDACTED",
    "ApprovalStore",
    "ConflictError",
    "EventStore",
    "NotFoundError",
    "RedactFn",
    "RepositoryError",
    "RunStore",
    "SqlAlchemyRepository",
    "ToolAuditStore",
    "create_all",
    "default_redact",
    "get_engine",
    "hash_payload",
    "new_id",
]
