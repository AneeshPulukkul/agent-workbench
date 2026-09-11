"""Core tool executor: validation, authZ, idempotency, audit, OTel, redaction."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from opentelemetry import trace
from pydantic import ValidationError

from apps.mcp_server.catalog import TOOL_METADATA, WRITE_TOOLS
from apps.mcp_server.models import INPUT_MODELS
from apps.mcp_server.redact import output_too_large, redact, redact_for_audit, sha256_hex
from apps.mcp_server.security import AuthContext, ForbiddenError, check_tenant
from apps.mcp_server.store import AUDIT_LOG, IDEMPOTENCY_STORE, INJECTED_DELAYS
from packages.contracts.tools import AuthorizationDecision, ToolInvocation, ToolInvocationStatus

log = structlog.get_logger(__name__)
tracer = trace.get_tracer("agent-mcp-server")

# --- durable audit sink (system of record: audit_records) --------------------
# In-memory AUDIT_LOG below is retained as a local tail for /audit debugging;
# the durable record is ``audit_records`` via this repository hook. Wire it at
# startup (e.g. ``set_audit_repository(get_repository())``); when unset,
# execution still succeeds (local/test) but only the in-memory tail exists.

_AUDIT_REPO: object | None = None


def set_audit_repository(repo: object | None) -> None:
    """Inject (or clear) the persistence repository for audit write-through."""
    global _AUDIT_REPO
    _AUDIT_REPO = repo


def get_audit_repository() -> object | None:
    return _AUDIT_REPO


def _persist_audit_record(
    *,
    tenant_id: str,
    actor: str,
    tool_name: str,
    run_id: str,
    invocation_id: str,
    decision: AuthorizationDecision,
    status: ToolInvocationStatus,
    input_hash: str,
    output_hash: str | None,
    redacted_input: dict[str, Any],
    error: dict[str, Any] | None,
    trace_id: str | None,
) -> None:
    """Best-effort write-through to ``audit_records`` (never breaks execution)."""
    repo = _AUDIT_REPO
    if repo is None:
        # Lazy default: reuse the gateway repository when available so the
        # MCP gateway persists without explicit wiring.
        try:
            from apps.api.store import get_repository as _get_repo

            repo = _get_repo()
        except Exception:
            return
    try:
        write_audit = getattr(repo, "write_audit")  # noqa: B009 -- duck-typed repo, attr may not exist
        # run_id is ephemeral for ad-hoc tool calls (no runs row), so store
        # with run_id=None and carry run/invocation ids in the redacted
        # decision payload. audit_records.redacted decision keeps redaction.
        write_audit(
            run_id=None,
            tenant_id=tenant_id,
            actor=actor,
            action=f"mcp.tool.{tool_name}",
            decision={
                "tool_name": tool_name,
                "run_id": run_id,
                "invocation_id": invocation_id,
                "authorization_decision": decision.value,
                "status": status.value,
                "input_hash": input_hash,
                "output_hash": output_hash,
                # Size-capped audit payloads: oversize inputs become a hash
                # reference so audit rows stay bounded (OUTPUT_MAX_BYTES).
                "redacted_input": redact_for_audit(redacted_input),
                "error": redact_for_audit(dict(error)) if error else None,
            },
            trace_id=trace_id,
        )
    except Exception:
        log.warning("mcp.audit.persist_failed", tool=tool_name)


class MCPToolError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


# --- mock handlers -----------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


async def _maybe_delay(tool: str) -> None:
    delay = INJECTED_DELAYS.get(tool, 0)
    if delay:
        await asyncio.sleep(delay)


async def _h_query_metrics(args: dict[str, Any]) -> dict[str, Any]:
    await _maybe_delay("telemetry.query_metrics")
    return {
        "service": args["service"],
        "metric": args["metric"],
        "aggregation": args.get("aggregation", "average"),
        "window": {"start": str(args["start_time"]), "end": str(args["end_time"])},
        "points": [{"t": f"2026-09-11T12:0{i}:00Z", "v": 90.0 + i} for i in range(5)],
        "mode": "mock",
    }


async def _h_query_logs(args: dict[str, Any]) -> dict[str, Any]:
    await _maybe_delay("telemetry.query_logs")
    limit = int(args.get("limit", 100))
    n = min(limit, 3)
    return {
        "service": args["service"],
        "query": args["query"],
        "logs": [
            {"ts": _iso_now(), "level": "ERROR", "msg": f"mock log {i} for {args['query']}"}
            for i in range(n)
        ],
        "mode": "mock",
    }


async def _h_get_health(args: dict[str, Any]) -> dict[str, Any]:
    await _maybe_delay("service.get_health")
    return {
        "service": args["service"],
        "status": "degraded",
        "latency_ms_p99": 420,
        "checks": {"http": "ok", "db": "ok", "queue": "lagging"},
        "mode": "mock",
    }


async def _h_search(args: dict[str, Any]) -> dict[str, Any]:
    await _maybe_delay("knowledge.search")
    return {
        "query": args["query"],
        "results": [
            {"id": "rb-001", "title": "Checkout error-rate runbook", "snippet": "check release..."},
            {"id": "kb-042", "title": "Latency triage", "snippet": "compare deploys..."},
        ][: int(args.get("top_k", 5))],
        "mode": "mock",
    }


async def _h_get_runbook(args: dict[str, Any]) -> dict[str, Any]:
    await _maybe_delay("knowledge.get_runbook")
    from apps.mcp_server.content import get_runbook

    rb = get_runbook(args["runbook_id"])
    if rb is None:
        raise MCPToolError("not_found", f"runbook {args['runbook_id']} not found")
    return {"mode": "mock", "untrusted": True, **rb}


async def _h_simulate(args: dict[str, Any]) -> dict[str, Any]:
    await _maybe_delay("remediation.simulate")
    return {
        "action": args["action"],
        "target_service": args.get("target_service"),
        "risk": "medium",
        "blast_radius": "single-service",
        "steps": [f"would execute: {args['action']} (simulated)"],
        "executed": False,
        "dry_run": True,
        "mode": "mock",
    }


async def _h_get_trace(args: dict[str, Any]) -> dict[str, Any]:
    await _maybe_delay("telemetry.get_trace")
    trace_id = args.get("trace_id") or "trace-mock-1"
    return {
        "service": args["service"],
        "trace_id": trace_id,
        "spans": [
            {"span_id": "s1", "operation": "GET /checkout", "duration_ms": 120},
            {"span_id": "s2", "operation": "db.query", "duration_ms": 85},
        ],
        "ref": f"trace://t/{args['service']}/{trace_id}",
        "mode": "mock",
    }


async def _h_get_current_release(args: dict[str, Any]) -> dict[str, Any]:
    await _maybe_delay("deployment.get_current_release")
    return {
        "service": args["service"],
        "release": "2026.09.10",
        "ref": f"resource://releases/{args['service']}",
        "mode": "mock",
    }


async def _h_ticket_create(args: dict[str, Any]) -> dict[str, Any]:
    await _maybe_delay("ticket.create")
    dry_run = bool(args.get("dry_run", False))
    return {
        "title": args["title"],
        "severity": args.get("severity", "medium"),
        "dry_run": dry_run,
        "executed": not dry_run,
        "ticket_id": "ticket-mock-1" if not dry_run else None,
        "ref": "ticket://t/1",
        "mode": "mock",
    }


async def _h_rollback(args: dict[str, Any]) -> dict[str, Any]:
    await _maybe_delay("deployment.rollback")
    dry_run = bool(args.get("dry_run", True))
    return {
        "deployment": args["deployment"],
        "target_release": args.get("target_release", "previous-stable"),
        "dry_run": dry_run,
        "executed": False,  # never acts locally
        "mock": True,
        "message": "dry-run plan (no action taken)"
        if dry_run
        else "approved mock rollback (no real action locally)",
        "mode": "mock",
    }


HANDLERS = {
    "telemetry.query_metrics": _h_query_metrics,
    "telemetry.query_logs": _h_query_logs,
    "telemetry.get_trace": _h_get_trace,
    "service.get_health": _h_get_health,
    "knowledge.search": _h_search,
    "knowledge.get_runbook": _h_get_runbook,
    "deployment.get_current_release": _h_get_current_release,
    "remediation.simulate": _h_simulate,
    "deployment.rollback": _h_rollback,
    "ticket.create": _h_ticket_create,
}


# --- executor -----------------------------------------------------------------


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


async def execute_tool(
    tool_name: str,
    raw_args: dict[str, Any],
    auth: AuthContext,
    *,
    idempotency_key: str | None = None,
    run_id: str | None = None,
    timeout_override: float | None = None,
) -> dict[str, Any]:
    meta = TOOL_METADATA.get(tool_name)
    if meta is None:
        raise MCPToolError("not_found", f"unknown tool {tool_name}")
    started = datetime.now(UTC)
    invocation_id = _new_id("inv")
    eff_run_id = run_id or _new_id("run")
    redacted_in = redact(raw_args)
    input_hash = sha256_hex(redacted_in)
    dry_run = bool(raw_args.get("dry_run", True))
    key = idempotency_key or raw_args.get("idempotency_key")
    status = ToolInvocationStatus.SUCCEEDED
    decision = AuthorizationDecision.ALLOWED
    result: dict[str, Any] = {}
    error: dict[str, Any] | None = None
    code = "ok"

    def _audit(out_hash: str | None) -> None:
        span_ctx = trace.get_current_span().get_span_context()
        trace_id = format(span_ctx.trace_id, "032x") if span_ctx.is_valid else None
        AUDIT_LOG.append(
            ToolInvocation(
                invocation_id=invocation_id,
                run_id=eff_run_id,
                tenant_id=auth.tenant_id,
                tool_name=tool_name,
                tool_version=meta.version,
                input_hash=input_hash,
                redacted_input=redacted_in,  # type: ignore[arg-type]
                output_hash=out_hash,
                status=status,
                authorization_decision=decision,
                idempotency_key=key if tool_name in WRITE_TOOLS else None,
                dry_run=dry_run if tool_name in WRITE_TOOLS else False,
                started_at=started,
                completed_at=datetime.now(UTC),
                trace_id=trace_id,
                error=error,
            )
        )
        # Durable write-through (system of record); in-memory tail above stays
        # for /audit debugging. Redaction preserved via redact().
        _persist_audit_record(
            tenant_id=auth.tenant_id,
            actor=auth.user_id,
            tool_name=tool_name,
            run_id=eff_run_id,
            invocation_id=invocation_id,
            decision=decision,
            status=status,
            input_hash=input_hash,
            output_hash=out_hash,
            redacted_input=dict(redacted_in),
            error=error,
            trace_id=trace_id,
        )

    with tracer.start_as_current_span(f"mcp.tool.{tool_name}") as span:
        span.set_attribute("workbench.run_id", eff_run_id)
        span.set_attribute("workbench.tool.name", tool_name)
        span.set_attribute("workbench.tool.side_effect", meta.side_effect.value)
        span.set_attribute("workbench.tool.version", meta.version)
        try:
            # authZ (tenant + scopes)
            try:
                check_tenant(auth, raw_args.get("tenant_id"))
                auth.require_scope(list(meta.required_scopes))
            except ForbiddenError as e:
                decision = AuthorizationDecision.DENIED
                raise MCPToolError("forbidden", str(e)) from e

            # strict validation
            model = INPUT_MODELS[tool_name]
            try:
                parsed = model(**raw_args)
            except ValidationError as e:
                raise MCPToolError(
                    "validation_error",
                    f"invalid input: {e.errors()[0]['msg']}",
                    details={"errors": e.errors()[:5]},
                ) from e
            args = parsed.model_dump(mode="json")
            args["tenant_id"] = auth.tenant_id

            # write gating: approval + idempotency key
            if tool_name in WRITE_TOOLS:
                decision = AuthorizationDecision.REQUIRES_APPROVAL
                if not dry_run and not auth.approved:
                    raise MCPToolError(
                        "approval_required",
                        f"{tool_name} requires human approval (dry_run=false)",
                    )
                decision = AuthorizationDecision.ALLOWED
                if not dry_run and not key:
                    raise MCPToolError(
                        "validation_error",
                        "idempotency_key required when dry_run=false",
                    )
                if key:
                    seen = IDEMPOTENCY_STORE.get(f"{tool_name}:{key}")
                    if seen is not None:
                        if seen["input_hash"] != input_hash:
                            raise MCPToolError(
                                "conflict",
                                "idempotency key already used with different input",
                            )
                        cached_result = seen["result"]
                        result: dict[str, Any] = (
                            dict(cached_result) if isinstance(cached_result, dict) else {}
                        )
                        result["deduplicated"] = True
                        _audit(sha256_hex(redact(result)))
                        log.info("mcp.tool.deduplicated", tool=tool_name, key=key)
                        return result

            timeout = timeout_override if timeout_override is not None else meta.timeout_seconds
            try:
                result = await asyncio.wait_for(HANDLERS[tool_name](args), timeout)
            except TimeoutError as e:
                status = ToolInvocationStatus.TIMEOUT
                raise MCPToolError("timeout", f"{tool_name} timed out after {timeout}s") from e

            if output_too_large(result):
                raise MCPToolError("tool_error", "tool result oversize; rejected")
            result = redact(result)
            out_hash = sha256_hex(result)
            if key and tool_name in WRITE_TOOLS and f"{tool_name}:{key}" not in IDEMPOTENCY_STORE:
                IDEMPOTENCY_STORE[f"{tool_name}:{key}"] = {
                    "input_hash": input_hash,
                    "result": dict(result),
                }
            _audit(out_hash)
            span.set_attribute("workbench.policy.decision", str(decision.value))
            log.info("mcp.tool.ok", tool=tool_name, tenant=auth.tenant_id)
            return result
        except MCPToolError as e:
            code = e.code
            if status != ToolInvocationStatus.TIMEOUT:
                status = ToolInvocationStatus.FAILED
            error = {"code": e.code, "message": e.message}
            if code in ("forbidden", "auth_error"):
                decision = AuthorizationDecision.DENIED
            _audit(None)
            span.set_attribute("workbench.policy.decision", str(decision.value))
            span.set_attribute("error", True)
            log.info("mcp.tool.error", tool=tool_name, code=code)
            raise


__all__ = [
    "HANDLERS",
    "MCPToolError",
    "execute_tool",
    "get_audit_repository",
    "set_audit_repository",
    "tracer",
]
