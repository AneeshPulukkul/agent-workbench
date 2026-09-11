"""Unit: telemetry conventions/tracing/metrics — redaction + spans + no leakage."""

from __future__ import annotations

import os

from packages.telemetry import conventions as C
from packages.telemetry import instrument, metrics, tracing


def test_redaction_never_leaks_secrets():
    red = C.redact_mapping({"token": "abc", "service": "checkout-api", "nested": {"password": "x"}})
    assert red["token"] == "[redacted]"
    assert red["service"] == "checkout-api"
    assert red["nested"]["password"] == "[redacted]"
    assert C.scrub_message("call with Bearer abc123 done") == "call with [redacted] done"
    # Fake-secret fixtures stay redacted.
    assert C.redact_mapping({"api_key": "sk-fake-123"}) == {"api_key": "[redacted]"}


def test_tenant_hashed_not_raw():
    h = C.hash_tenant("tenant_a")
    assert h is not None and "tenant_a" not in h
    assert C.hash_tenant("tenant_a") == h  # deterministic


def test_content_capture_opt_in_only(monkeypatch):
    monkeypatch.delenv("OTEL_CONTENT_CAPTURE", raising=False)
    assert C.content_capture_enabled() is False
    assert C.maybe_content("secret prompt") is None
    monkeypatch.setenv("OTEL_CONTENT_CAPTURE", "dev-only")
    assert C.content_capture_enabled() is True
    assert C.maybe_content("hello Bearer abc123") == "hello [redacted]"


def test_genai_attrs_have_no_prompts():
    attrs = C.genai_attrs(
        operation="chat", provider="x", model="m", input_tokens=10, output_tokens=5
    )
    assert "gen_ai.operation.name" in attrs
    assert not any("prompt" in k or "completion" in k for k in attrs)


def test_spans_carry_run_id_and_hash(monkeypatch):
    monkeypatch.delenv("OTEL_CONTENT_CAPTURE", raising=False)
    _provider, exporter = tracing.init_tracing("test-svc", in_memory=True)
    with tracing.mcp_tool_span("telemetry.query_metrics", run_id="run_1", side_effect="none"):
        pass
    with tracing.a2a_span("task_1", run_id="run_1", agent_name="obs"):
        pass
    with tracing.policy_span(run_id="run_1", tool_name="ticket.create", decision="approval"):
        pass
    with tracing.approval_wait_span("appr_1", run_id="run_1"):
        pass
    with tracing.db_span("insert", run_id="run_1", table="run_events"):
        pass
    spans = {s.name: dict(s.attributes or {}) for s in exporter.get_finished_spans()}
    assert spans["mcp telemetry.query_metrics"]["workbench.run_id"] == "run_1"
    assert spans["mcp telemetry.query_metrics"]["workbench.tool.name"] == "telemetry.query_metrics"
    # No raw prompts/secrets on any span.
    for attrs in spans.values():
        blob = str(attrs)
        assert "Bearer" not in blob and "sk-" not in blob


def test_traceparent_propagates():
    carrier: dict = {}
    tracing.inject_headers(carrier)  # must not raise without a collector
    assert isinstance(carrier, dict)


def test_metrics_recorders():
    metrics.reset_for_tests()
    metrics.record_run_started()
    metrics.record_run_finished("completed")
    metrics.record_tool_call("telemetry.query_metrics", "succeeded", 0.2)
    metrics.record_a2a_call("obs", "completed", 0.5)
    metrics.record_policy_decision("approval")
    metrics.record_approval_wait(12.0, "approved")
    metrics.record_sse_reconnect()
    metrics.record_budget_exhausted("max_cost_usd")
    snap = metrics.snapshot()
    assert any("workbench.runs.started" in k for k in snap["counts"])
    assert any("workbench.tool.calls" in k for k in snap["counts"])
    assert "workbench.approval.wait" in snap["histograms"]


def test_instrument_wrappers_record_metrics():
    metrics.reset_for_tests()
    with instrument.instrument_mcp_tool("knowledge.search", {"query": "q"}, run_id="run_1"):
        pass
    with instrument.instrument_a2a_task("t1", run_id="run_1", agent_name="obs"):
        pass
    with instrument.instrument_policy_check("ticket.create", run_id="run_1"):
        instrument.record_policy_outcome(True, True)
    snap = metrics.snapshot()
    assert any("workbench.tool.calls" in k for k in snap["counts"])
    assert any("workbench.a2a.calls" in k for k in snap["counts"])
    assert any("workbench.policy.decisions" in k for k in snap["counts"])


def test_no_secret_leakage_in_redacted_tool_args():
    args = {"service": "checkout-api", "token": "super-secret"}
    red = C.redact_mapping(args)
    assert red["token"] == "[redacted]"
    # Original untouched, redacted copy used for spans.
    assert args["token"] == "super-secret"
    with instrument.instrument_mcp_tool("telemetry.query_metrics", args, run_id="r"):
        pass
    _ = os.getenv("OTEL_CONTENT_CAPTURE", "")
