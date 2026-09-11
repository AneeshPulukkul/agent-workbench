"""Unit: runtime, router, budgets, validation, timeout/retry, cancellation.

Sync wrappers around async code (asyncio.run) so no pytest-asyncio needed.
"""

from __future__ import annotations

import asyncio

import pytest

from apps.orchestrator.runtime import (
    BudgetExceeded,
    BudgetLimits,
    BudgetTracker,
    Classification,
    FakeRuntime,
    ModelRouter,
    ModelTask,
    ModelValidationError,
    call_with_retry,
    retry_allowed_for_tool,
    validate_structured,
)


def test_router_task_mapping():
    r = ModelRouter(fast_model="fast-m", reasoning_model="deep-m", summary_model="sum-m")
    assert r.route(ModelTask.FAST) == "fast-m"
    assert r.route(ModelTask.REASONING) == "deep-m"
    assert r.route(ModelTask.SUMMARY) == "sum-m"
    assert r.route("fast") == "fast-m"


def test_router_from_env(monkeypatch):
    monkeypatch.setenv("FAST_MODEL", "f1")
    monkeypatch.setenv("REASONING_MODEL", "r1")
    monkeypatch.setenv("SUMMARY_MODEL", "s1")
    assert ModelRouter.from_env().route("reasoning") == "r1"


def test_budget_model_calls_enforced():
    t = BudgetTracker(BudgetLimits(max_model_calls=2, max_cost_usd=1000.0,
                                   max_input_tokens=10**9, max_output_tokens=10**9))
    t.record_model(input_tokens=10, output_tokens=5, cost_usd=0.01)
    t.record_model(input_tokens=10, output_tokens=5, cost_usd=0.01)
    with pytest.raises(BudgetExceeded):
        t.record_model(input_tokens=10, output_tokens=5, cost_usd=0.01)


def test_budget_tool_cost_token_delegation_duration():
    t = BudgetTracker(BudgetLimits(max_tool_calls=1, max_cost_usd=1.0,
                                   max_input_tokens=100, max_output_tokens=100,
                                   max_delegation_depth=1, max_duration_s=3600.0))
    t.record_tool()
    with pytest.raises(BudgetExceeded):
        t.record_tool()
    with pytest.raises(BudgetExceeded):
        BudgetTracker(BudgetLimits(max_cost_usd=0.0)).record_model(cost_usd=0.5)
    with pytest.raises(BudgetExceeded):
        BudgetTracker(BudgetLimits(max_input_tokens=5)).record_model(input_tokens=50)
    with pytest.raises(BudgetExceeded):
        BudgetTracker(BudgetLimits(max_output_tokens=5)).record_model(output_tokens=50)
    d = BudgetTracker(BudgetLimits(max_delegation_depth=0))
    with pytest.raises(BudgetExceeded):
        d.record_delegation()
    # duration
    t2 = BudgetTracker(BudgetLimits(max_duration_s=-1.0))  # already exceeded
    with pytest.raises(BudgetExceeded):
        t2.check()


def test_budget_from_dict_seeds_consumed():
    t = BudgetTracker.from_budget_dict(
        {"max_model_calls": 5, "max_tool_calls": 5, "max_cost_usd": 2.0,
         "consumed_model_calls": 4, "consumed_tool_calls": 1, "consumed_cost_usd": 0.5}
    )
    snap = t.snapshot()
    assert snap.model_calls == 4 and snap.tool_calls == 1
    assert snap.cost_usd == pytest.approx(0.5)


def test_structured_validation_rejects_bad():
    with pytest.raises(ModelValidationError):
        validate_structured({"category": "x"}, Classification)  # missing confidence
    ok = validate_structured(
        {"category": "incident", "confidence": 0.9, "reasoning": "r"}, Classification)
    assert ok.category == "incident"


def test_fake_runtime_deterministic():
    async def go():
        rt = FakeRuntime()
        c1 = await rt.classify("Investigate elevated checkout API error rate")
        c2 = await rt.classify("Investigate elevated checkout API error rate")
        assert c1.category == c2.category == "incident"
        assert c1.confidence == c2.confidence
        p1 = await rt.plan("Investigate elevated checkout API error rate")
        p2 = await rt.plan("Investigate elevated checkout API error rate")
        assert p1.steps == p2.steps and len(p1.steps) >= 4
        res = await rt.synthesize("obj", run_id="run_1", tenant_id="t1")
        assert res.answer and res.schema_version == "1.0"
        s = await rt.summarize("x" * 2000)
        assert len(s) <= 500
        snap = rt.tracker.snapshot()
        assert snap.model_calls == 5  # classify x2 + plan x2 + synthesize... wait summarize too
    # classify x2, plan x2, synthesize x1, summarize x1 = 6
    async def go2():
        rt = FakeRuntime()
        await rt.classify("Investigate elevated checkout API error rate")
        await rt.classify("Investigate elevated checkout API error rate")
        await rt.plan("Investigate elevated checkout API error rate")
        await rt.plan("Investigate elevated checkout API error rate")
        await rt.synthesize("obj", run_id="run_1", tenant_id="t1")
        await rt.summarize("x" * 2000)
        assert rt.tracker.snapshot().model_calls == 6
    asyncio.run(go2())
    # budget consumed
    rt = FakeRuntime(tracker=BudgetTracker(BudgetLimits(
        max_model_calls=1, max_cost_usd=100.0,
        max_input_tokens=10**9, max_output_tokens=10**9)))
    async def over():
        await rt.classify("error rate spike")
        await rt.classify("error rate spike")
    with pytest.raises(BudgetExceeded):
        asyncio.run(over())


def test_never_retry_writes():
    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        asyncio.run(call_with_retry(flaky, max_retries=3, timeout_s=2.0, is_write=True))
    assert attempts["n"] == 1  # single attempt for writes


def test_retry_reads_until_success():
    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("transient")
        return "ok"

    out = asyncio.run(call_with_retry(
        flaky, max_retries=3, timeout_s=2.0, is_write=False, base_backoff_s=0.001))
    assert out == "ok" and attempts["n"] == 3


def test_retry_tool_category_gate():
    assert retry_allowed_for_tool("read") is True
    assert retry_allowed_for_tool("analysis") is True
    assert retry_allowed_for_tool("write") is False
    assert retry_allowed_for_tool("destructive") is False


def test_cancellation_propagates():
    async def go():
        rt = FakeRuntime()
        ev = asyncio.Event()
        ev.set()
        with pytest.raises(asyncio.CancelledError):
            await rt.classify("error spike", cancellation=ev)
        with pytest.raises(asyncio.CancelledError):
            await rt.summarize("hello", cancellation=ev)
    asyncio.run(go())
