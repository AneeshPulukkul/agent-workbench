"""Unit tests: bounded orchestrator graph + worker (Prompt 8).

Deterministic per-branch coverage with fake MCP/A2A/policy/model clients.
No network, no LLM, no sleeps.
"""

from __future__ import annotations

import datetime
from datetime import UTC

import pytest

from apps.orchestrator.graph import (
    CircuitBreaker,
    CircuitOpenError,
    FakeA2AClient,
    FakeMCPClient,
    FakeModelClient,
    FakePolicyClient,
    GraphDeps,
    InMemoryPersistence,
    OrchestratorGraph,
    build_proposed_action,
    validate_tool_call,
)
from apps.orchestrator.state import BudgetLimits, RunState
from apps.worker.worker import InMemoryRunQueue, RunJob


def _state(run_id: str = "run_t1", **over) -> RunState:
    from apps.orchestrator.state import BudgetLimits, BudgetUsage

    base = dict(
        run_id=run_id,
        tenant_id="tenant_a",
        objective="Investigate elevated checkout API error rate",
        context={"service": "checkout-api", "env": "dev"},
        trace_id="trace-1",
        correlation_id="corr-1",
        status="created",
        current_state="classify",
        budget={},
        limits=BudgetLimits(),
        usage=BudgetUsage(),
        started_at=datetime.now(UTC),
        trace_id="trace-1",
        correlation_id="corr-1",
        classification={},
        plan=[],
        resources={},
        mcp_evidence=[],
        a2a_result=None,
        a2a_fallback=False,
        findings=[],
        proposed_actions=[],
        policy_decisions=[],
        approvals=[],
        unresolved=[],
        outcome=None,
        error=None,
        conflict=None,
        retried_reads=False,
        event_sequence=0,
        cancelled=False,
    )
    base.update(over)
    return RunState(**base)


def _graph(store=None, mcp=None, a2a=None, policy=None, limits=None) -> OrchestratorGraph:
    return OrchestratorGraph(
        GraphDeps(
            mcp=mcp or FakeMCPClient(),
            a2a=a2a or FakeA2AClient(),
            policy=policy or FakePolicyClient(),
            store=store or InMemoryPersistence(),
            model=FakeModelClient(),
            limits=limits or BudgetLimits(),
        )
    )


# -- happy path ------------------------------------------------------------


def test_happy_path_pauses_then_completes_after_approval() -> None:
    store = InMemoryPersistence()
    g = _graph(store=store)
    st = _state()
    out = g.run(st)
    assert out.status == "waiting_for_approval"
    assert out.current_state == "approval_pause"
    assert any(e["type"] == "approval.required" for e in store.events)
    assert any(e["type"] == "agent.delegated" for e in store.events)
    assert any(e["type"] == "finding.created" for e in store.events)
    # persist-after-each-transition: saves grow with events
    assert len(store.saves) >= 8
    seqs = [e["sequence"] for e in store.events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert all(e["trace_id"] == "trace-1" for e in store.events)

    done = g.resume_after_approval(out, decision="approved", approver="lead")
    assert done.status == "completed"
    assert done.outcome == "completed"
    types = [e["type"] for e in store.events]
    assert "approval.received" in types and "run.completed" in types
    assert done.result["findings"]  # type: ignore[attr-defined]


def test_approval_rejected_completes_without_execution() -> None:
    store = InMemoryPersistence()
    mcp = FakeMCPClient()
    g = _graph(store=store, mcp=mcp)
    st = g.run(_state("run_rej"))
    assert st.status == "waiting_for_approval"
    writes_before = [c for c in mcp.calls if c[0] in ("ticket.create", "deployment.rollback")]
    assert writes_before == []
    done = g.resume_after_approval(st, decision="rejected", approver="lead", reason="too risky")
    assert done.outcome == "rejected" and done.status == "completed"
    writes_after = [c for c in mcp.calls if c[0] in ("ticket.create", "deployment.rollback")]
    assert writes_after == []  # rejected -> never executed


# -- branches ---------------------------------------------------------------


def test_clarify_on_insufficient_context() -> None:
    store = InMemoryPersistence()
    g = _graph(store=store)
    st = g.run(_state("run_clar", objective="hi", context={}))
    assert st.outcome == "clarify" and st.status == "completed"
    assert st.unresolved
    assert store.events[-1]["type"] == "run.completed"


def test_more_info_on_low_confidence() -> None:
    store = InMemoryPersistence()
    g = _graph(store=store, a2a=FakeA2AClient(mode="low_confidence"))
    st = g.run(_state("run_low"))
    assert st.outcome == "more_info" and st.status == "completed"
    assert st.unresolved


def test_policy_denied_explains_and_skips_execution() -> None:
    store = InMemoryPersistence()
    mcp = FakeMCPClient()
    g = _graph(store=store, mcp=mcp, policy=FakePolicyClient(mode="deny_all"))
    st = g.run(_state("run_den"))
    assert st.outcome == "denied" and st.status == "completed"
    assert not [c for c in mcp.calls if c[0] in ("ticket.create", "deployment.rollback")]
    assert any("denied" in str(e).lower() for e in store.events)


def test_a2a_timeout_fallback_degrades_confidence() -> None:
    store = InMemoryPersistence()
    g = _graph(store=store, a2a=FakeA2AClient(mode="timeout"))
    st = g.run(_state("run_fb"))
    assert st.a2a_fallback is True
    assert any("timed out" in q.lower() or "specialist" in q.lower() for q in st.unresolved)
    # degraded synthesis caps confidence -> more_info terminal (bounded, audited)
    assert st.outcome == "more_info"


def test_a2a_malformed_output_treated_as_untrusted() -> None:
    g = _graph(a2a=FakeA2AClient(mode="malformed"))
    st = g.run(_state("run_mal"))
    assert st.a2a_fallback is True
    assert any("validation" in q.lower() or "untrusted" in q.lower() for q in st.unresolved)


def test_conflict_reported_not_silently_chosen() -> None:
    mcp = FakeMCPClient(health_status="healthy")
    g = _graph(mcp=mcp, a2a=FakeA2AClient(mode="conflict"))
    st = g.run(_state("run_conf"))
    assert st.conflict is not None
    assert all(float(f["confidence"]) <= 0.6 for f in st.findings)


# -- budgets / timeouts / cancel / circuit -----------------------------------


def test_budget_exceeded_fails_bounded() -> None:
    store = InMemoryPersistence()
    g = _graph(
        store=store,
        limits=BudgetLimits(
            max_tool_calls=1,
            max_model_calls=10,
            max_delegations=3,
            max_cost_usd=100.0,
            max_duration_seconds=600.0,
        ),
    )
    st = _state("run_bud")
    st.limits = BudgetLimits(
        max_tool_calls=1,
        max_model_calls=10,
        max_delegations=3,
        max_cost_usd=100.0,
        max_duration_seconds=600.0,
    )
    out = g.run(st)
    assert out.status == "failed" and out.outcome == "failed"
    assert out.error and out.error["code"] == "budget_exceeded"
    assert store.events[-1]["type"] == "run.failed"


def test_cancel_is_cooperative_and_propagates() -> None:
    store = InMemoryPersistence()
    a2a = FakeA2AClient()
    g = _graph(store=store, a2a=a2a)
    st = _state("run_can")
    st.cancelled = True
    out = g.run(st)
    assert out.status == "cancelled" and out.outcome == "cancelled"
    assert store.events[-1]["type"] == "run.cancelled"
    assert f"{st.run_id}-obs-1" in a2a.cancelled


def test_circuit_breaker_opens_and_graph_degrades() -> None:
    cb = CircuitBreaker(threshold=2)
    cb.record_failure("a2a")
    cb.guard("a2a")  # not open yet after 1 failure
    cb.record_failure("a2a")
    with pytest.raises(CircuitOpenError):
        cb.guard("a2a")
    # graph-level: pre-opened breaker forces fallback path
    g = _graph(a2a=FakeA2AClient(mode="timeout"))
    g.breaker.record_failure("a2a")
    g.breaker.record_failure("a2a")
    g.breaker.record_failure("a2a")
    st = g.run(_state("run_cb"))
    assert st.a2a_fallback is True


def test_safe_retries_only_idempotent_reads() -> None:
    class FlakyRead:
        def __init__(self):
            self.calls = 0

        def invoke(self, tool_name, args, *, timeout_s):
            validate_tool_call(tool_name, args)
            if tool_name == "telemetry.query_metrics":
                self.calls += 1
                if self.calls == 1:
                    raise TimeoutError("transient")
                return {
                    "service": "checkout-api",
                    "error_rate": 0.1,
                    "ref": "telemetry://m/checkout-api/err",
                }
            return FakeMCPClient().invoke(tool_name, args, timeout_s=timeout_s)

    flaky = FlakyRead()
    g = _graph(mcp=flaky)  # type: ignore[arg-type]
    st = g.run(_state("run_retry"))
    assert flaky.calls == 2  # read retried
    assert st.mcp_evidence  # recovered

    class FailWrite:
        def __init__(self):
            self.writes = 0

        def invoke(self, tool_name, args, *, timeout_s):
            validate_tool_call(tool_name, args)
            if tool_name in ("ticket.create", "deployment.rollback"):
                self.writes += 1
                raise RuntimeError("write failed")
            return FakeMCPClient().invoke(tool_name, args, timeout_s=timeout_s)

    failing = FailWrite()
    store = InMemoryPersistence()
    g2 = OrchestratorGraph(
        GraphDeps(
            mcp=failing,
            a2a=FakeA2AClient(),  # type: ignore[arg-type]
            policy=FakePolicyClient(),
            store=store,
            model=FakeModelClient(),
        )
    )
    st2 = g2.run(_state("run_wfail"))
    assert st2.status == "waiting_for_approval"
    done = g2.resume_after_approval(st2, decision="approved")
    assert done.status == "completed"
    assert failing.writes == 1  # writes never auto-retried


# -- never exec model text ----------------------------------------------------


def test_never_exec_model_text_as_command() -> None:
    with pytest.raises(ValueError):
        validate_tool_call("rm -rf /;", {})
    with pytest.raises(ValueError):
        validate_tool_call("deployment.rollback", {"cmd": "rm -rf / && reboot"})
    with pytest.raises(ValueError):
        build_proposed_action(
            run_id="r", tenant_id="t", tool_name="evil.tool; rm", reason="x", tool_input={}
        )
    action = build_proposed_action(
        run_id="run_x",
        tenant_id="tenant_a",
        tool_name="ticket.create",
        reason="track",
        tool_input={"title": "t"},
    )
    assert action["tool_name"] == "ticket.create"
    assert action["requires_approval"] is True
    assert action["idempotency_key"]


# -- worker --------------------------------------------------------------------


def test_worker_enqueue_process_approve() -> None:
    q = InMemoryRunQueue()
    q.enqueue(
        RunJob(
            run_id="run_w1",
            objective="Investigate elevated checkout API error rate",
            context={"service": "checkout-api"},
        )
    )
    paused = q.process_one(q.make_graph())
    assert paused is not None and paused.status == "waiting_for_approval"
    done = q.decide_approval("run_w1", decision="approved")
    assert done.status == "completed"


def test_worker_cancel() -> None:
    q = InMemoryRunQueue()
    q.enqueue(
        RunJob(
            run_id="run_w2",
            objective="Investigate elevated checkout API error rate",
            context={"service": "checkout-api"},
        )
    )
    assert q.request_cancel("run_w2") is True
    st = q.process_one(q.make_graph())
    assert st is not None and st.status == "cancelled"
