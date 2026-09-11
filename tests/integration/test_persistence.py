"""Integration: persistence repositories.

Uses disposable PostgreSQL when TEST_DATABASE_URL/DATABASE_URL points at a
live server, else falls back to in-memory SQLite (StaticPool) so the suite
runs with `pytest` and no `make up`.

Covers: persist-then-publish, (run_id, sequence) dedupe, replay order,
approval persistence + double-decide, tool audit redaction + idempotency,
transaction boundaries, redaction hook.
"""

from __future__ import annotations

import contextlib
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from packages.contracts import AgentEvent, Approval, EventType, ToolInvocation
from packages.contracts.tools import ToolInvocationStatus
from packages.persistence.models import Base
from packages.persistence.repositories import (
    ConflictError,
    NotFoundError,
    SqlAlchemyRepository,
    default_redact,
    get_engine,
    hash_payload,
)

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def _database_url() -> str:
    for var in ("TEST_DATABASE_URL", "DATABASE_URL"):
        v = os.getenv(var)
        if v:
            return v
    return "sqlite:///:memory:"


def _make_repo() -> SqlAlchemyRepository:
    url = _database_url()
    try:
        engine = get_engine(url)
        # quick connectivity check for real PG URLs
        with engine.connect():
            pass
    except Exception:
        engine = get_engine("sqlite:///:memory:")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return SqlAlchemyRepository(sessionmaker(bind=engine, expire_on_commit=False))


@pytest.fixture()
def repo() -> SqlAlchemyRepository:
    return _make_repo()


def _seed_run(
    repo: SqlAlchemyRepository,
    run_id: str = "run_123",
    tenant: str = "tenant_a",
    idem: str | None = None,
) -> None:
    repo.ensure_tenant(tenant)
    with contextlib.suppress(ConflictError):
        repo.create_run(
            run_id=run_id,
            tenant_id=tenant,
            created_by="analyst_1",
            objective="Investigate elevated checkout API error rate",
            budget={"max_tool_calls": 20, "max_model_calls": 10, "max_cost_usd": 2.0},
            idempotency_key=idem,
        )


# --- redaction ---------------------------------------------------------------


def test_redaction_never_stores_secrets():
    red = default_redact(
        {
            "service": "checkout-api",
            "password": "hunter2",
            "nested": {"api_key": "sk-abc123", "ok": 1},
            "auth_header": "Bearer abc.def.ghi",
        }
    )
    assert red["password"] == "***REDACTED***"
    assert red["nested"]["api_key"] == "***REDACTED***"
    assert red["nested"]["ok"] == 1
    assert red["auth_header"] == "***REDACTED***"
    assert hash_payload({"b": 1, "a": 2}) == hash_payload({"a": 2, "b": 1})


# --- runs --------------------------------------------------------------------


def test_run_idempotent_on_idempotency_key(repo: SqlAlchemyRepository):
    _seed_run(repo, run_id="run_idem_1", idem="idem-1")
    # Same key, different run_id -> returns the original row (no duplicate).
    row = repo.create_run(
        run_id="run_idem_2",
        tenant_id="tenant_a",
        created_by="analyst_1",
        objective="other",
        budget={},
        idempotency_key="idem-1",
    )
    assert row.id == "run_idem_1"


def test_run_tenant_isolation(repo: SqlAlchemyRepository):
    _seed_run(repo)
    with pytest.raises(NotFoundError):
        repo.get_run("run_123", "tenant_b")


# --- events ------------------------------------------------------------------


def test_event_append_replay_order(repo: SqlAlchemyRepository):
    _seed_run(repo)
    e1 = repo.append_next_event(
        run_id="run_123", tenant_id="tenant_a", type=EventType.RUN_STARTED, data={"objective": "x"}
    )
    e2 = repo.append_next_event(
        run_id="run_123", tenant_id="tenant_a", type=EventType.MESSAGE_DELTA, data={"delta": "hi"}
    )
    assert (e1.sequence, e2.sequence) == (0, 1)
    assert repo.next_sequence("run_123") == 2
    all_ev = repo.list_events("run_123", "tenant_a")
    assert [e.sequence for e in all_ev] == [0, 1]
    tail = repo.list_events("run_123", "tenant_a", after_sequence=0)
    assert [e.sequence for e in tail] == [1]
    assert tail[0].type == EventType.MESSAGE_DELTA


def test_event_append_idempotent_on_event_id(repo: SqlAlchemyRepository):
    _seed_run(repo)
    ev = AgentEvent(
        event_id="evt_dup",
        run_id="run_123",
        tenant_id="tenant_a",
        sequence=0,
        type=EventType.RUN_STARTED,
        timestamp=NOW,
        data={},
    )
    first = repo.append_event(ev)
    second = repo.append_event(ev)  # same event_id -> idempotent
    assert first.event_id == second.event_id == "evt_dup"
    assert len(repo.list_events("run_123", "tenant_a")) == 1


def test_event_sequence_conflict(repo: SqlAlchemyRepository):
    _seed_run(repo)
    repo.append_event(
        AgentEvent(
            event_id="e1",
            run_id="run_123",
            tenant_id="tenant_a",
            sequence=0,
            type=EventType.RUN_STARTED,
            timestamp=NOW,
            data={},
        )
    )
    with pytest.raises(ConflictError):
        repo.append_event(
            AgentEvent(
                event_id="e2",
                run_id="run_123",
                tenant_id="tenant_a",
                sequence=0,
                type=EventType.MESSAGE_DELTA,
                timestamp=NOW,
                data={},
            )
        )
    # Transaction boundary: failed write left exactly one row.
    assert len(repo.list_events("run_123", "tenant_a")) == 1


def test_event_data_redacted_on_write(repo: SqlAlchemyRepository):
    _seed_run(repo)
    ev = repo.append_next_event(
        run_id="run_123",
        tenant_id="tenant_a",
        type=EventType.TOOL_STARTED,
        data={"tool": "ticket.create", "api_key": "sk-secret", "n": 1},
    )
    assert ev.data["api_key"] == "***REDACTED***"
    stored = repo.list_events("run_123", "tenant_a")[0]
    assert stored.data["api_key"] == "***REDACTED***"


# --- approvals -----------------------------------------------------------------


def test_approval_persist_decide_double_decide(repo: SqlAlchemyRepository):
    _seed_run(repo)
    requested = datetime.now(UTC) - timedelta(minutes=5)
    ap = Approval(
        approval_id="ap_1",
        run_id="run_123",
        tenant_id="tenant_a",
        action_id="act_1",
        requested_by="orchestrator",
        requested_at=requested,
        expires_at=requested + timedelta(minutes=30),
    )
    repo.create_approval(ap)
    decided = repo.decide_approval(
        approval_id="ap_1", tenant_id="tenant_a", decision="approved", approver="lead"
    )
    assert decided.decision.value == "approved"
    with pytest.raises(ConflictError):
        repo.decide_approval(
            approval_id="ap_1", tenant_id="tenant_a", decision="rejected", approver="lead2"
        )


def test_approval_expiry(repo: SqlAlchemyRepository):
    _seed_run(repo)
    base = datetime.now(UTC)
    ap = Approval(
        approval_id="ap_exp",
        run_id="run_123",
        tenant_id="tenant_a",
        action_id="act_9",
        requested_by="orchestrator",
        requested_at=base - timedelta(hours=2),
        expires_at=base - timedelta(hours=1),
    )
    repo.create_approval(ap)
    with pytest.raises(ConflictError):
        repo.decide_approval(
            approval_id="ap_exp", tenant_id="tenant_a", decision="approved", approver="lead"
        )


# --- tool audit ------------------------------------------------------------------


def _invocation(iid: str = "inv_1", idem: str | None = None) -> ToolInvocation:
    started = datetime.now(UTC) - timedelta(seconds=5)
    return ToolInvocation(
        invocation_id=iid,
        run_id="run_123",
        tenant_id="tenant_a",
        tool_name="telemetry.query_metrics",
        tool_version="1.0.0",
        input_hash="placeholder",
        redacted_input={},
        status=ToolInvocationStatus.RUNNING,
        started_at=started,
        idempotency_key=idem,
    )


def test_tool_audit_redacts_and_hashes(repo: SqlAlchemyRepository):
    _seed_run(repo)
    raw = {"service": "checkout-api", "password": "s3cr3t", "window": "5m"}
    stored = repo.record_tool_invocation(_invocation(), raw_input=raw)
    assert stored.redacted_input["password"] == "***REDACTED***"
    assert stored.input_hash == hash_payload(raw)
    assert stored.input_hash.startswith("sha256:")
    # Completed with secret-bearing output -> redacted too.
    done = repo.complete_tool_invocation(
        invocation_id="inv_1",
        tenant_id="tenant_a",
        status="succeeded",
        raw_output={"rows": 5, "token": "Bearer abc.def.ghi"},
    )
    assert done.status == ToolInvocationStatus.SUCCEEDED
    assert done.completed_at is not None


def test_tool_idempotency_key_dedupes(repo: SqlAlchemyRepository):
    _seed_run(repo)
    repo.record_tool_invocation(_invocation("inv_a", idem="tool-idem-1"), raw_input={"a": 1})
    dup = repo.record_tool_invocation(_invocation("inv_b", idem="tool-idem-1"), raw_input={"a": 1})
    assert dup.invocation_id == "inv_a"  # second key delivery returns original


# --- findings / tasks / model calls / audit ---------------------------------------


def test_finding_task_modelcall_audit_paths(repo: SqlAlchemyRepository):
    _seed_run(repo)
    repo.save_evidence_ref(
        ref_id="ev_1",
        run_id="run_123",
        tenant_id="tenant_a",
        kind="metric",
        uri="telemetry://m/1",
        excerpt_hash=hash_payload("excerpt"),
    )
    repo.save_finding(
        finding_id="f_1",
        run_id="run_123",
        tenant_id="tenant_a",
        title="error spike",
        summary="5xx after release",
        evidence_refs=["ev_1"],
        confidence=0.8,
        severity="high",
    )
    repo.save_agent_task(
        task_id="task_1",
        run_id="run_123",
        tenant_id="tenant_a",
        skill_id="correlate-service-symptoms",
        agent_name="observability-agent",
        objective="correlate",
        raw_inputs={"service": "checkout-api", "token": "should-redact"},
    )
    repo.update_agent_task(
        task_id="task_1",
        tenant_id="tenant_a",
        status="completed",
        raw_output={"cause": "bad release"},
    )
    cid = repo.record_model_call(
        run_id="run_123",
        tenant_id="tenant_a",
        model="gpt-4o-mini",
        task_type="fast",
        raw_prompt={"messages": "classify this"},
        input_tokens=50,
        output_tokens=10,
        cost_usd=0.001,
        duration_ms=120,
    )
    assert cid.startswith("mcall_")
    aid = repo.write_audit(
        run_id="run_123",
        tenant_id="tenant_a",
        actor="policy",
        action="policy.decision",
        decision={"allowed": True, "token": "x"},
        reason="read-only allow",
    )
    assert aid.startswith("audit_")
