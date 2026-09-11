"""Instrumentation entry-points for API / orchestrator / MCP / A2A / policy / DB.

All helpers are thin wrappers over :mod:`packages.telemetry.tracing` and
:mod:`packages.telemetry.metrics` so business code only imports this module.

Each helper:
- propagates W3C trace context,
- sets ``workbench.run_id`` + hashed tenant,
- records the matching metric,
- never captures prompts/secrets (see conventions.maybe_content).
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
from contextlib import contextmanager, suppress
from typing import Any

from . import conventions as conventions
from . import metrics as metrics
from . import tracing as tracing

# ---------------------------------------------------------------------------
# API (FastAPI)
# ---------------------------------------------------------------------------


def instrument_api(app: Any, service_name: str = "agent-api") -> Any:
    """Attach OTel FastAPI instrumentation when installed + a trace-id header.

    Safe no-op when ``opentelemetry-instrumentation-fastapi`` is missing.
    """
    tracing.init_tracing(service_name)
    metrics.init_metrics(service_name)
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        with suppress(Exception):
            FastAPIInstrumentor().instrument_app(app)
    except ImportError:
        pass

    # Always add X-Request-ID echo + traceparent passthrough middleware.
    try:
        from fastapi.responses import JSONResponse  # noqa: F401
        from starlette.middleware.base import BaseHTTPMiddleware

        class _TraceMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
                import uuid

                req_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
                with tracing.http_server_span(
                    request.url.path,
                    method=request.method,
                    run_id=request.path_params.get("run_id"),
                ):
                    resp = await call_next(request)
                resp.headers["X-Request-ID"] = req_id
                tid = tracing.current_trace_id()
                if tid:
                    resp.headers["X-Trace-ID"] = tid
                return resp

        app.add_middleware(_TraceMiddleware)
    except Exception:
        pass
    return app


# ---------------------------------------------------------------------------
# Orchestrator / workflow
# ---------------------------------------------------------------------------


@contextmanager
def instrument_run(run_id: str, tenant_id: str | None = None):
    started = time.monotonic()
    metrics.record_run_started(run_id)
    with tracing.run_span(run_id, tenant_id=tenant_id):
        try:
            yield
            metrics.record_run_finished("completed")
        except Exception:
            metrics.record_run_finished("failed")
            raise
        finally:
            with suppress(Exception):
                metrics.record_run_duration(time.monotonic() - started, "run")


@contextmanager
def instrument_workflow_step(state: str, run_id: str | None = None):
    with tracing.workflow_span(state, run_id=run_id):
        yield


def instrumented_step(state_attr: str = "current_state"):
    """Decorator for OrchestratorGraph step methods (optional use)."""

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(self, state, *a, **k):  # type: ignore[no-untyped-def]
            run_id = getattr(state, "run_id", None)
            cur = getattr(state, state_attr, "unknown")
            with instrument_workflow_step(str(cur), run_id=run_id):
                return fn(self, state, *a, **k)

        return wrapper

    return deco


# ---------------------------------------------------------------------------
# Model calls (GenAI attrs behind adapter)
# ---------------------------------------------------------------------------


@contextmanager
def instrument_model_call(
    *,
    run_id: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    operation: str = "chat",
):
    started = time.monotonic()
    with tracing.model_span(
        run_id=run_id, model=model, provider=provider, operation=operation
    ) as s:
        yield s
    elapsed = time.monotonic() - started
    with suppress(Exception):
        s.set_attribute("workbench.model.latency_s", round(elapsed, 4))  # type: ignore[union-attr]


def record_model_usage(
    *, model: str, input_tokens: int, output_tokens: int, cost_usd: float
) -> None:
    metrics.record_model_call(model, input_tokens, output_tokens, cost_usd)


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


@contextmanager
def instrument_mcp_tool(
    tool_name: str,
    args: dict | None = None,
    *,
    run_id: str | None = None,
    side_effect: str | None = None,
    version: str | None = None,
):
    _ = conventions.redact_mapping(dict(args or {}))  # redacted copy; never attached raw
    started = time.monotonic()
    status = "succeeded"
    with tracing.mcp_tool_span(tool_name, run_id=run_id, side_effect=side_effect, version=version):
        try:
            yield
        except Exception:
            status = "failed"
            raise
        finally:
            with suppress(Exception):
                metrics.record_tool_call(tool_name, status, time.monotonic() - started)


def trace_mcp_invoke(fn: Callable) -> Callable:
    """Decorator for sync/async MCP invoke functions with (tool_name, args)."""

    @functools.wraps(fn)
    def sync_wrapper(tool_name: str, args: dict | None = None, *a: Any, **k: Any):  # type: ignore[no-untyped-def]
        run_id = k.get("run_id")
        with instrument_mcp_tool(tool_name, args, run_id=run_id):
            return fn(tool_name, args, *a, **k)

    @functools.wraps(fn)
    async def async_wrapper(tool_name: str, args: dict | None = None, *a: Any, **k: Any):  # type: ignore[no-untyped-def]
        run_id = k.get("run_id")
        with instrument_mcp_tool(tool_name, args, run_id=run_id):
            return await fn(tool_name, args, *a, **k)

    if getattr(fn, "__code__", None) and "await" in str(fn.__code__.co_flags):
        return async_wrapper
    import asyncio

    if asyncio.iscoroutinefunction(fn):
        return async_wrapper
    return sync_wrapper


# ---------------------------------------------------------------------------
# A2A delegation
# ---------------------------------------------------------------------------


@contextmanager
def instrument_a2a_task(
    task_id: str,
    *,
    run_id: str | None = None,
    agent_name: str | None = None,
    skill_id: str | None = None,
):
    tracing.inject_headers()
    started = time.monotonic()
    status = "completed"
    with tracing.a2a_span(task_id, run_id=run_id, agent_name=agent_name, skill_id=skill_id):
        try:
            yield
        except Exception as exc:
            status = "timeout" if isinstance(exc, TimeoutError) else "failed"
            raise
        finally:
            with suppress(Exception):
                metrics.record_a2a_call(agent_name or "unknown", status, time.monotonic() - started)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@contextmanager
def instrument_policy_check(tool_name: str, *, run_id: str | None = None):
    with tracing.policy_span(run_id=run_id, tool_name=tool_name) as s:
        yield s


def record_policy_outcome(allowed: bool, requires_approval: bool) -> None:
    if not allowed:
        metrics.record_policy_decision("deny")
    elif requires_approval:
        metrics.record_policy_decision("approval")
    else:
        metrics.record_policy_decision("allow")


# ---------------------------------------------------------------------------
# Approval wait
# ---------------------------------------------------------------------------


@contextmanager
def instrument_approval_wait(approval_id: str, *, run_id: str | None = None):
    started = time.monotonic()
    with tracing.approval_wait_span(approval_id, run_id=run_id):
        yield
    with suppress(Exception):
        metrics.record_approval_wait(time.monotonic() - started)


# ---------------------------------------------------------------------------
# DB ops
# ---------------------------------------------------------------------------


@contextmanager
def instrument_db(op: str, table: str, *, run_id: str | None = None):
    with tracing.db_span(op, run_id=run_id, table=table):
        yield


__all__ = [
    "conventions",
    "instrument_a2a_task",
    "instrument_api",
    "instrument_approval_wait",
    "instrument_db",
    "instrument_mcp_tool",
    "instrument_model_call",
    "instrument_policy_check",
    "instrument_run",
    "instrument_workflow_step",
    "instrumented_step",
    "metrics",
    "record_model_usage",
    "record_policy_outcome",
    "trace_mcp_invoke",
    "tracing",
]
