"""OTel tracing setup + span helpers (W3C trace context throughout).

Stack: OTel SDK -> Collector (OTLP) -> Jaeger + Prometheus + logs.
Spans: HTTP server -> run lifecycle -> workflow transition -> agent
invocation -> model call -> retrieval -> MCP tool -> A2A task ->
policy decision -> approval-wait -> DB op. ``run_id`` on every span;
``tenant_id`` only as hash (or omitted) per privacy default.

Graceful degradation: if the OTLP exporter is not installed, falls back to
a no-op/console exporter so unit tests never require a collector.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

from . import conventions as C

try:
    from opentelemetry import propagate, trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )
    from opentelemetry.trace import SpanKind, Status, StatusCode

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - offline fallback
    propagate = None  # type: ignore[assignment]
    trace = None  # type: ignore[assignment]
    _OTEL_AVAILABLE = False

_initialized_for: str | None = None
_tracer = None


def is_available() -> bool:
    return _OTEL_AVAILABLE


def init_tracing(
    service_name: str = "agent-api",
    otlp_endpoint: str | None = None,
    *,
    enable_console: bool = False,
    in_memory: bool = False,
) -> Any:
    """Configure the global TracerProvider (idempotent per service name).

    Returns the TracerProvider (or None when OTel is unavailable).
    ``in_memory=True`` attaches an InMemorySpanExporter for tests and
    returns (provider, exporter).
    """
    global _initialized_for, _tracer
    if not _OTEL_AVAILABLE:
        return None
    assert trace is not None
    endpoint = otlp_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    # Reuse existing provider if already initialized for this service.
    existing = trace.get_tracer_provider()
    if _initialized_for == service_name and not in_memory:
        _tracer = trace.get_tracer(service_name)
        return existing
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    exporter = None
    if in_memory:
        try:
            from opentelemetry.sdk.trace.export import InMemorySpanExporter
        except ImportError:
            from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # type: ignore[no-redef]
                InMemorySpanExporter,
            )

        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    elif endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        except Exception:
            # Exporter package missing/blocked -> console fallback, never crash.
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif enable_console or os.getenv("OTEL_TRACES_CONSOLE", "").lower() in ("1", "true"):
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    # already set in this process (e.g. tests)
    with suppress(Exception):
        trace.set_tracer_provider(provider)
    _initialized_for = service_name
    try:
        # Prefer the fresh provider's tracer so in_memory tests capture spans
        # even when a global provider was already set.
        _tracer = provider.get_tracer(service_name)
    except Exception:
        _tracer = trace.get_tracer(service_name)
    if in_memory:
        return provider, exporter
    return provider


def get_tracer(name: str = "workbench"):
    if not _OTEL_AVAILABLE:
        return _NoopTracer()
    assert trace is not None
    global _tracer
    if _tracer is None:
        try:
            _tracer = trace.get_tracer(name)
        except Exception:
            return _NoopTracer()
    return _tracer


class _NoopSpan:
    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *a: Any) -> bool:
        return False

    def set_attribute(self, *a: Any, **k: Any) -> None: ...
    def set_status(self, *a: Any, **k: Any) -> None: ...
    def record_exception(self, *a: Any, **k: Any) -> None: ...
    def add_event(self, *a: Any, **k: Any) -> None: ...
    def update_name(self, *a: Any, **k: Any) -> None: ...
    def end(self, *a: Any, **k: Any) -> None: ...
    def get_span_context(self):  # type: ignore[no-untyped-def]
        class _Ctx:
            trace_id = 0
            span_id = 0

        return _Ctx()


class _NoopTracer:
    def start_as_current_span(self, *a: Any, **k: Any):  # type: ignore[no-untyped-def]
        return _NoopSpan()

    def start_span(self, *a: Any, **k: Any):  # type: ignore[no-untyped-def]
        return _NoopSpan()


# ---------------------------------------------------------------------------
# Propagation (W3C traceparent)
# ---------------------------------------------------------------------------


def inject_headers(carrier: dict | None = None) -> dict:
    """Inject current W3C trace context into an outgoing header carrier."""
    carrier = {} if carrier is None else carrier
    if not _OTEL_AVAILABLE or propagate is None:
        return carrier
    with suppress(Exception):
        propagate.inject(carrier)
    return carrier


def extract_context(carrier: dict | None):
    """Extract W3C context from incoming headers (returns Context or None)."""
    if not _OTEL_AVAILABLE or propagate is None:
        return None
    try:
        return propagate.extract(carrier or {})
    except Exception:
        return None


def current_trace_id() -> str | None:
    if not _OTEL_AVAILABLE or trace is None:
        return None
    try:
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx is None or not getattr(ctx, "trace_id", 0):
            return None
        return format(ctx.trace_id, "032x")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Core span helper
# ---------------------------------------------------------------------------


@contextmanager
def span(
    name: str,
    *,
    run_id: str | None = None,
    tenant_id: str | None = None,
    attributes: dict[str, Any] | None = None,
    kind: Any = None,
) -> Iterator[Any]:
    """Start a span with workbench base attrs (run_id + tenant hash).

    Never records raw tenant_id, prompts, or secrets.
    """
    tracer = get_tracer()
    attrs: dict[str, Any] = {}
    if run_id:
        attrs[C.RUN_ID] = run_id
    th = C.hash_tenant(tenant_id) if tenant_id else None
    if th:
        attrs[C.TENANT_HASH] = th
    if attributes:
        for k, v in attributes.items():
            if C.is_secret_key(k):
                attrs[k] = C.REDACTED
            else:
                attrs[k] = v
    kwargs: dict[str, Any] = {"attributes": attrs}
    if kind is not None and _OTEL_AVAILABLE:
        kwargs["kind"] = kind
    try:
        cm = tracer.start_as_current_span(name, **kwargs)
    except Exception:
        # If tracer misbehaves, still yield a noop span body.
        yield _NoopSpan()
        return
    with cm as s:
        yield s


def _status_ok(span_obj: Any) -> None:
    with suppress(Exception):
        span_obj.set_status(Status(StatusCode.OK))  # type: ignore[union-attr]


def _status_error(span_obj: Any, message: str) -> None:
    with suppress(Exception):
        span_obj.set_status(Status(StatusCode.ERROR, C.scrub_message(message)))  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Domain span helpers (one per otel-plan.md span)
# ---------------------------------------------------------------------------


@contextmanager
def run_span(
    run_id: str, *, tenant_id: str | None = None, status: str | None = None
) -> Iterator[Any]:
    attrs = {C.RUN_STATUS: status} if status else None
    with span(f"run {run_id}", run_id=run_id, tenant_id=tenant_id, attributes=attrs) as s:
        yield s


@contextmanager
def workflow_span(state: str, *, run_id: str | None = None) -> Iterator[Any]:
    with span(
        f"workflow.{state}", run_id=run_id, attributes={"workbench.workflow.state": state}
    ) as s:
        yield s


@contextmanager
def agent_span(agent_name: str, *, run_id: str | None = None) -> Iterator[Any]:
    with span(f"agent {agent_name}", run_id=run_id, attributes={C.AGENT_NAME: agent_name}) as s:
        yield s


@contextmanager
def model_span(
    *,
    run_id: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    operation: str = "chat",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
) -> Iterator[Any]:
    attrs = C.genai_attrs(
        operation=operation,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )
    with span("model.call", run_id=run_id, attributes=attrs) as s:
        yield s


@contextmanager
def mcp_tool_span(
    tool_name: str,
    *,
    run_id: str | None = None,
    side_effect: str | None = None,
    dry_run: bool | None = None,
    version: str | None = None,
) -> Iterator[Any]:
    attrs: dict[str, Any] = {C.TOOL_NAME: tool_name}
    if side_effect:
        attrs[C.TOOL_SIDE_EFFECT] = side_effect
    if dry_run is not None:
        attrs[C.TOOL_DRY_RUN] = bool(dry_run)
    if version:
        attrs[C.TOOL_VERSION] = version
    started = time.monotonic()
    with span(f"mcp {tool_name}", run_id=run_id, attributes=attrs) as s:
        try:
            yield s
            _status_ok(s)
        except Exception as exc:
            _status_error(s, str(exc))
            raise
        finally:
            with suppress(Exception):
                s.set_attribute("workbench.tool.latency_s", round(time.monotonic() - started, 4))


@contextmanager
def a2a_span(
    task_id: str,
    *,
    run_id: str | None = None,
    agent_name: str | None = None,
    skill_id: str | None = None,
) -> Iterator[Any]:
    attrs: dict[str, Any] = {C.TASK_ID: task_id}
    if agent_name:
        attrs[C.AGENT_NAME] = agent_name
    if skill_id:
        attrs[C.SKILL_ID] = skill_id
    started = time.monotonic()
    with span(f"a2a {task_id}", run_id=run_id, attributes=attrs) as s:
        try:
            yield s
            _status_ok(s)
        except Exception as exc:
            _status_error(s, str(exc))
            raise
        finally:
            with suppress(Exception):
                s.set_attribute("workbench.a2a.latency_s", round(time.monotonic() - started, 4))


@contextmanager
def policy_span(
    *,
    run_id: str | None = None,
    tool_name: str | None = None,
    decision: str | None = None,
    reason: str | None = None,
) -> Iterator[Any]:
    attrs: dict[str, Any] = {}
    if tool_name:
        attrs[C.TOOL_NAME] = tool_name
    if decision:
        attrs[C.POLICY_DECISION] = decision
    if reason:
        attrs[C.POLICY_REASON] = C.scrub_message(reason)[:500]
    with span("policy.decision", run_id=run_id, attributes=attrs) as s:
        yield s


@contextmanager
def approval_wait_span(approval_id: str, *, run_id: str | None = None) -> Iterator[Any]:
    with span("approval.wait", run_id=run_id, attributes={C.APPROVAL_ID: approval_id}) as s:
        yield s


@contextmanager
def db_span(op: str, *, run_id: str | None = None, table: str | None = None) -> Iterator[Any]:
    attrs: dict[str, Any] = {"db.operation": op}
    if table:
        attrs["db.sql.table"] = table
    with span(f"db {op}", run_id=run_id, attributes=attrs) as s:
        yield s


@contextmanager
def http_server_span(
    route: str, *, run_id: str | None = None, method: str | None = None
) -> Iterator[Any]:
    attrs: dict[str, Any] = {"http.route": route}
    if method:
        attrs["http.request.method"] = method
    kind = SpanKind.SERVER if _OTEL_AVAILABLE else None
    with span(route, run_id=run_id, attributes=attrs, kind=kind) as s:
        yield s


__all__ = [
    "a2a_span",
    "agent_span",
    "approval_wait_span",
    "current_trace_id",
    "db_span",
    "extract_context",
    "get_tracer",
    "http_server_span",
    "init_tracing",
    "inject_headers",
    "is_available",
    "mcp_tool_span",
    "model_span",
    "policy_span",
    "run_span",
    "span",
    "workflow_span",
]
