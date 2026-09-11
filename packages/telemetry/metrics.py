"""OTel metrics (otel-plan.md § Metrics).

Counters/histograms:
- runs started/completed by status, run duration
- model calls / tokens / cost per run
- tool success/failure + latency by tool
- A2A latency / timeout rate
- policy allow/deny/approval rate
- approval wait p50/p95 (histogram)
- SSE reconnects
- budget-exhaustion count

Falls back to in-process recorders when the OTel metrics SDK/exporter is
unavailable so unit tests never need a collector. :func:`snapshot` exposes
the in-process counters for assertions.
"""

from __future__ import annotations

import os
import threading
from typing import Any

try:
    from opentelemetry import metrics as _otel_metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        PeriodicExportingMetricReader,
    )

    _METRICS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _otel_metrics = None  # type: ignore[assignment]
    _METRICS_AVAILABLE = False

_lock = threading.Lock()
_meter = None
_instruments: dict[str, Any] = {}
_local_counts: dict[str, float] = {}
_local_hist_values: dict[str, list[float]] = {}
_initialized = False


def is_available() -> bool:
    return _METRICS_AVAILABLE


def init_metrics(service_name: str = "agent-api", *, console: bool = False) -> Any:
    """Configure SDK MeterProvider with OTLP reader when an endpoint is set."""
    global _meter, _initialized
    if not _METRICS_AVAILABLE:
        return None
    assert _otel_metrics is not None
    if _initialized and _meter is not None:
        return _meter
    readers = []
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )

            readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint)))
        except Exception:
            pass
    if console or os.getenv("OTEL_METRICS_CONSOLE", "").lower() in ("1", "true"):
        readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter()))
    if readers:
        try:
            from opentelemetry.sdk.resources import Resource

            provider = MeterProvider(
                resource=Resource.create({"service.name": service_name}),
                metric_readers=readers,
            )
            _otel_metrics.set_meter_provider(provider)
        except Exception:
            pass
    try:
        _meter = _otel_metrics.get_meter(service_name)
    except Exception:
        _meter = None
    _initialized = True
    return _meter


def _counter(name: str, description: str = "") -> Any:
    if not _METRICS_AVAILABLE or _meter is None:
        return None
    if name in _instruments:
        return _instruments[name]
    try:
        inst = _meter.create_counter(name, description=description)
    except Exception:
        return None
    _instruments[name] = inst
    return inst


def _histogram(name: str, description: str = "", unit: str = "") -> Any:
    if not _METRICS_AVAILABLE or _meter is None:
        return None
    if name in _instruments:
        return _instruments[name]
    try:
        inst = _meter.create_histogram(name, description=description, unit=unit)
    except Exception:
        return None
    _instruments[name] = inst
    return inst


def _add_counter(name: str, value: float, attrs: dict | None, description: str = "") -> None:
    key = name + "|" + ",".join(f"{k}={v}" for k, v in sorted((attrs or {}).items()))
    with _lock:
        _local_counts[key] = _local_counts.get(key, 0.0) + value
    inst = _counter(name, description)
    if inst is not None:
        try:
            inst.add(value, attrs or {})
        except Exception:
            pass


def _record_hist(
    name: str, value: float, attrs: dict | None, description: str = "", unit: str = ""
) -> None:
    with _lock:
        _local_hist_values.setdefault(name, []).append(value)
    inst = _histogram(name, description, unit)
    if inst is not None:
        try:
            inst.record(value, attrs or {})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public recorders
# ---------------------------------------------------------------------------


def record_run_started(run_id: str = "") -> None:
    _add_counter("workbench.runs.started", 1, {"status": "started"}, "Runs started")


def record_run_finished(status: str) -> None:
    _add_counter("workbench.runs.finished", 1, {"status": status}, "Runs finished by status")


def record_run_duration(duration_s: float, status: str) -> None:
    _record_hist(
        "workbench.run.duration", duration_s, {"status": status}, "Run wall-clock duration", "s"
    )


def record_model_call(model: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
    _add_counter("workbench.model.calls", 1, {"model": model}, "Model calls")
    _add_counter(
        "workbench.model.tokens", input_tokens + output_tokens, {"model": model}, "Model tokens"
    )
    _add_counter("workbench.model.cost_usd", cost_usd, {"model": model}, "Model cost USD")


def record_tool_call(tool_name: str, status: str, latency_s: float) -> None:
    _add_counter("workbench.tool.calls", 1, {"tool": tool_name, "status": status}, "Tool calls")
    _record_hist(
        "workbench.tool.latency",
        latency_s,
        {"tool": tool_name, "status": status},
        "Tool latency",
        "s",
    )


def record_a2a_call(agent_name: str, status: str, latency_s: float) -> None:
    _add_counter("workbench.a2a.calls", 1, {"agent": agent_name, "status": status}, "A2A calls")
    _record_hist("workbench.a2a.latency", latency_s, {"agent": agent_name}, "A2A latency", "s")


def record_policy_decision(decision: str) -> None:
    _add_counter("workbench.policy.decisions", 1, {"decision": decision}, "Policy decisions")


def record_approval_wait(wait_s: float, decision: str = "") -> None:
    _record_hist(
        "workbench.approval.wait", wait_s, {"decision": decision}, "Approval wait time", "s"
    )


def record_sse_reconnect(run_id: str = "") -> None:
    _add_counter("workbench.sse.reconnects", 1, {}, "SSE reconnects")


def record_budget_exhausted(budget: str) -> None:
    _add_counter("workbench.budget.exhausted", 1, {"budget": budget}, "Budget exhaustion")


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def snapshot() -> dict[str, Any]:
    """Return in-process counters/histograms (for unit tests)."""
    with _lock:
        return {
            "counts": dict(_local_counts),
            "histograms": {k: list(v) for k, v in _local_hist_values.items()},
        }


def reset_for_tests() -> None:
    with _lock:
        _local_counts.clear()
        _local_hist_values.clear()


__all__ = [
    "is_available",
    "init_metrics",
    "record_run_started",
    "record_run_finished",
    "record_run_duration",
    "record_model_call",
    "record_tool_call",
    "record_a2a_call",
    "record_policy_decision",
    "record_approval_wait",
    "record_sse_reconnect",
    "record_budget_exhausted",
    "snapshot",
    "reset_for_tests",
]
