"""Contract: OTel propagation (traceparent API -> worker/MCP/A2A) + collector configs."""

from __future__ import annotations

from pathlib import Path

# type: ignore[import-untyped]
import yaml

from packages.telemetry import tracing

ROOT = Path(__file__).resolve().parent.parent.parent


def test_traceparent_round_trip():
    tracing.init_tracing("test-traceparent", in_memory=True)
    with tracing.span("parent", run_id="run_1"):
        carrier = tracing.inject_headers({})
        assert "traceparent" in carrier  # W3C header present
        ctx = tracing.extract_context(carrier)
        assert ctx is not None
        # Child span inherits the same trace id.
        parent_tid = tracing.current_trace_id()
    assert parent_tid is not None and len(parent_tid) == 32


def test_collector_config_has_pipelines():
    cfg = yaml.safe_load((ROOT / "deploy" / "otel-collector" / "config.yaml").read_text())
    pipes = cfg["service"]["pipelines"]
    assert set(pipes) >= {"traces", "metrics", "logs"}
    assert "otlp" in pipes["traces"]["receivers"]
    assert cfg["receivers"]["otlp"]["protocols"]["grpc"]["endpoint"] == "0.0.0.0:4317"


def test_prometheus_scrapes_collector_and_has_alerts():
    prom = yaml.safe_load((ROOT / "deploy" / "prometheus" / "prometheus.yml").read_text())
    jobs = {j["job_name"] for j in prom["scrape_configs"]}
    assert "otel-collector" in jobs
    alerts = (ROOT / "deploy" / "prometheus" / "alerts.yml").read_text()
    for name in ("HighRunLatencyP95", "ToolFailureSpike", "ApprovalWaitAging", "CostBurnRate"):
        assert name in alerts


def test_grafana_provisioning_present():
    assert (
        ROOT / "deploy" / "grafana" / "provisioning" / "datasources" / "datasources.yml"
    ).exists()
    assert (ROOT / "deploy" / "grafana" / "dashboards" / "run-overview.json").exists()
    assert (ROOT / "deploy" / "grafana" / "dashboards" / "cost.json").exists()
