"""Contract unit tests: schema generation + validation (Prompt 3).

Run: pytest tests/test_contracts.py -q  (from repo root)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.contracts import (
    A2ATaskRequest,
    A2ATaskResult,
    AgentCard,
    AgentEvent,
    AgentRequest,
    AgentResult,
    Approval,
    ApprovalDecisionRequest,
    ErrorCode,
    ErrorEnvelope,
    EventType,
    EvidenceReference,
    Finding,
    PolicyDecision,
    ProposedAction,
    Run,
    RunStatus,
    ToolInvocation,
    ToolInvocationStatus,
    ToolMetadata,
)
from packages.contracts import export_schemas as export_mod

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=5)
DEADLINE = NOW + timedelta(minutes=30)

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "packages" / "contracts" / "schemas"


def _agent_request(**kw):
    base = dict(
        run_id="run_123", tenant_id="tenant_a", user_id="analyst_1",
        objective="Investigate elevated checkout API error rate",
        context={"service": "checkout-api"},
    )
    base.update(kw)
    return AgentRequest(**base)


# --- schema generation -----------------------------------------------------

def test_all_models_expose_schema_version():
    for model in (
        AgentRequest, Run, Finding, EvidenceReference, ProposedAction,
        AgentResult, AgentEvent, ToolMetadata, ToolInvocation,
        A2ATaskRequest, A2ATaskResult, AgentCard, Approval,
        ApprovalDecisionRequest, PolicyDecision, ErrorEnvelope,
    ):
        schema = model.model_json_schema()
        assert schema["properties"]["schema_version"]["const"] == "1.0", model.__name__


def test_exported_schema_files_exist_and_match(tmp_path):
    written = export_mod.export_schemas(tmp_path)
    assert len(written) >= 20
    for p in written:
        assert json.loads(p.read_text())


def test_committed_schemas_up_to_date():
    """Committed schemas/*.json must match generator output (CI gate)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        fresh = {p.name: p.read_text() for p in export_mod.export_schemas(Path(tmp))}
    for name, content in fresh.items():
        committed = SCHEMAS_DIR / name
        assert committed.exists(), f"missing committed schema {name}; run export_schemas.py"
        assert committed.read_text() == content, f"stale schema {name}; run export_schemas.py"


# --- AgentRequest / Run ----------------------------------------------------

def test_agent_request_defaults_and_budgets():
    r = _agent_request()
    assert r.max_tool_calls == 20 and r.max_model_calls == 10 and r.max_cost_usd == 2.0
    assert r.schema_version == "1.0"


def test_agent_request_rejects_extra_and_bad_budgets():
    with pytest.raises(ValidationError):
        _agent_request(objective="x" * 9000)
    with pytest.raises(ValidationError):
        _agent_request(max_tool_calls=0)
    with pytest.raises(ValidationError):
        AgentRequest(run_id="run_1", tenant_id="t", user_id="u", objective="o",
                     unknown_field="nope")


def test_agent_request_carries_trace_ids():
    r = _agent_request(trace_id="trace-1", correlation_id="corr-1")
    assert r.trace_id == "trace-1" and r.correlation_id == "corr-1"


def test_run_status_enum_values():
    assert {s.value for s in RunStatus} == {
        "created", "running", "waiting_for_approval",
        "completed", "failed", "cancelled"}


def test_run_model_round_trip():
    run = Run(
        run_id="run_123", tenant_id="tenant_a", created_by="analyst_1",
        objective="obj", status="running", current_state="diagnosing",
        budget={"max_tool_calls": 20, "max_model_calls": 10, "max_cost_usd": 2.0},
        created_at=NOW, updated_at=NOW,
        trace_id="t1", correlation_id="c1",
    )
    assert run.status is RunStatus.RUNNING
    assert run.budget.max_tool_calls == 20
    with pytest.raises(ValidationError):
        Run(**{**run.model_dump(), "status": "flying"})


def test_proposed_action_high_risk_requires_approval():
    good = dict(action_id="act_1", tool_name="deployment.rollback", reason="r",
                input={"release": "2026.09.10"}, risk="medium",
                idempotency_key="idem-1")
    assert ProposedAction(**good).requires_approval is True
    with pytest.raises(ValidationError):
        ProposedAction(**{**good, "risk": "critical", "requires_approval": False})
    with pytest.raises(ValidationError):  # raw model text must not become command
        ProposedAction(**{**good, "tool_name": "rm -rf /;"})
    with pytest.raises(ValidationError):
        ProposedAction(**{**good, "input": {f"k{i}": i for i in range(60)}})


def test_finding_confidence_bounds_and_evidence():
    with pytest.raises(ValidationError):
        Finding(finding_id="f1", title="t", summary="s", confidence=1.5)
    f = Finding(finding_id="f1", title="t", summary="s",
                evidence_refs=["ev_1"], confidence=0.8, severity="high",
                run_id="run_123", tenant_id="tenant_a")
    assert f.severity.value == "high"


def test_evidence_reference_uri_scheme():
    EvidenceReference(ref_id="ev_1", kind="metric", uri="telemetry://m/1")
    with pytest.raises(ValidationError):
        EvidenceReference(ref_id="ev_1", kind="metric", uri="file:///etc/passwd")


def test_agent_result_shape():
    res = AgentResult(answer="cause: bad release", unresolved_questions=["q1"])
    assert res.findings == [] and res.schema_version == "1.0"


# --- events ----------------------------------------------------------------

def test_event_types_complete():
    assert {e.value for e in EventType} >= {
        "run.started", "message.delta", "tool.started", "tool.completed",
        "agent.delegated", "finding.created", "approval.required",
        "approval.received", "run.completed", "run.failed"}


def test_agent_event_sequence_and_sensitive_gate():
    ev = AgentEvent(event_id="e1", run_id="run_123", tenant_id="tenant_a",
                    sequence=42, type="run.started", timestamp=NOW,
                    data={"objective": "x"}, trace_id="t", correlation_id="c")
    assert ev.sequence == 42
    with pytest.raises(ValidationError):  # naive timestamp rejected
        AgentEvent(event_id="e1", run_id="r", tenant_id="t", sequence=0,
                   type="run.started", timestamp=datetime(2026, 9, 11, 12, 0),
                   data={})
    with pytest.raises(ValidationError):  # sensitive must not be ui-safe
        AgentEvent(event_id="e1", run_id="r", tenant_id="t", sequence=0,
                   type="run.started", timestamp=NOW, data={},
                   sensitive=True, safe_for_ui=True)
    with pytest.raises(ValidationError):
        AgentEvent(event_id="e1", run_id="r", tenant_id="t", sequence=-1,
                   type="run.started", timestamp=NOW, data={})


# --- tools -----------------------------------------------------------------

def test_tool_metadata_policy_invariants():
    ToolMetadata(name="telemetry.query_metrics", description="d",
                 category="read", side_effect="none", idempotent=True,
                 timeout_seconds=30, approval_required=False,
                 supports_dry_run=False, owner="telemetry", version="1.0.0")
    with pytest.raises(ValidationError):  # write must require approval
        ToolMetadata(name="ticket.create", description="d", category="write",
                     side_effect="external_write", idempotent=False,
                     timeout_seconds=30, approval_required=False,
                     supports_dry_run=False, owner="o", version="1.0.0")
    with pytest.raises(ValidationError):  # read must be side-effect free
        ToolMetadata(name="knowledge.search", description="d", category="read",
                     side_effect="external_write", idempotent=True,
                     timeout_seconds=30, approval_required=False,
                     supports_dry_run=False, owner="o", version="1.0.0")


def test_tool_invocation_lifecycle():
    with pytest.raises(ValidationError):  # completed before started
        ToolInvocation(invocation_id="i1", run_id="r", tenant_id="t",
                       tool_name="telemetry.query_metrics", tool_version="1.0.0",
                       input_hash="h", status="succeeded",
                       started_at=LATER, completed_at=NOW)
    with pytest.raises(ValidationError):  # terminal needs completed_at
        ToolInvocation(invocation_id="i1", run_id="r", tenant_id="t",
                       tool_name="telemetry.query_metrics", tool_version="1.0.0",
                       input_hash="h", status="succeeded", started_at=NOW)
    inv = ToolInvocation(invocation_id="i1", run_id="run_123", tenant_id="tenant_a",
                         tool_name="deployment.rollback", tool_version="1.0.0",
                         input_hash="h", status=ToolInvocationStatus.RUNNING,
                         authorization_decision="requires_approval",
                         idempotency_key="idem-1", dry_run=True,
                         started_at=NOW, trace_id="t", correlation_id="c")
    assert inv.dry_run is True


# --- A2A -------------------------------------------------------------------

def test_a2a_request_result_contracts():
    req = A2ATaskRequest(task_id="task_1", tenant_id="tenant_a",
                         skill_id="correlate-service-symptoms",
                         agent_name="observability-agent", objective="o",
                         inputs={"service": "checkout-api"},
                         deadline=DEADLINE, requester="orchestrator",
                         run_id="run_123", trace_id="t", correlation_id="c")
    assert req.skill_id == "correlate-service-symptoms"
    with pytest.raises(ValidationError):
        A2ATaskRequest(**{**req.model_dump(), "callback_url": "not-a-url"})
    ok = A2ATaskResult(task_id="task_1", status="completed", output={"a": 1})
    assert ok.error is None
    with pytest.raises(ValidationError):  # failed needs error
        A2ATaskResult(task_id="task_1", status="failed")
    with pytest.raises(ValidationError):  # completed must not carry error
        A2ATaskResult(task_id="task_1", status="completed",
                      error={"code": "x", "message": "y"})


def test_agent_card_example_from_spec():
    card = AgentCard(
        name="observability-agent",
        description="Correlates metrics, logs, traces, and deployment changes.",
        url="http://observability-agent:8082/a2a",
        version="1.0.0",
        skills=[{"id": "correlate-service-symptoms", "name": "Correlate service symptoms",
                 "description": "Find likely causes from telemetry evidence.",
                 "input_modes": ["application/json"], "output_modes": ["application/json"]}],
        authentication={"schemes": ["oauth2"]},
    )
    assert card.skills[0].id == "correlate-service-symptoms"


# --- approvals / policy ----------------------------------------------------

def test_approval_lifecycle():
    ap = Approval(approval_id="ap_1", run_id="run_123", tenant_id="tenant_a",
                  action_id="act_1", requested_by="orchestrator",
                  requested_at=NOW, trace_id="t", correlation_id="c")
    assert ap.decision.value == "pending"
    with pytest.raises(ValidationError):  # terminal needs decided_at + approver
        Approval(**{**ap.model_dump(), "decision": "approved"})
    decided = Approval(**{**ap.model_dump(), "decision": "approved",
                          "approver": "lead", "decided_at": LATER})
    assert decided.approver == "lead"
    ApprovalDecisionRequest(decision="approved", approver="lead")
    with pytest.raises(ValidationError):
        ApprovalDecisionRequest(decision="maybe", approver="lead")


def test_policy_decision_deny_is_terminal():
    d = PolicyDecision(allowed=True, requires_approval=True, reason="write needs human")
    assert d.rule_version == "1.0"  # additive default
    with pytest.raises(ValidationError):
        PolicyDecision(allowed=False, requires_approval=True, reason="deny-by-default")


# --- errors ----------------------------------------------------------------

def test_error_envelope_safe_message_and_codes():
    e = ErrorEnvelope(code="policy_denied", message="Denied by policy",
                      run_id="run_123", retryable=False, trace_id="t")
    assert e.code is ErrorCode.POLICY_DENIED
    with pytest.raises(ValidationError):
        ErrorEnvelope(code="internal_error", message="failed, Bearer abc123")
    with pytest.raises(ValidationError):
        ErrorEnvelope(code="bogus", message="x")
