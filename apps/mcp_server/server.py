"""MCP Tool Gateway — official MCP SDK (Streamable HTTP) + FastAPI mirrors.

Transport: ``mcp.server.fastmcp.FastMCP.streamable_http_app()`` mounted at ``/mcp``.
REST mirrors (``/tools``, ``/resources/...``, ``/prompts/...``) exist for UI/debug
and contract tests; the MCP endpoint is the normative interface.

Security: Origin validation, per-request tenant authZ, scope checks, strict
Pydantic validation, idempotency keys for writes, audit (ToolInvocation) + OTel
spans, redaction of secrets. ``deployment.rollback`` is mock-only locally.
"""

import contextvars
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from apps.mcp_server.catalog import TOOL_METADATA
from apps.mcp_server.content import PROMPTS, get_runbook, get_service, render_prompt
from apps.mcp_server.executor import MCPToolError, execute_tool
from apps.mcp_server.security import (
    AuthContext,
    AuthError,
    ForbiddenError,
    from_headers,
    from_identity,
)
from apps.mcp_server.store import AUDIT_LOG
from packages.security.identity import validate_auth_config

_current_auth: contextvars.ContextVar[AuthContext | None] = contextvars.ContextVar(
    "mcp_auth", default=None
)

ALLOWED_ORIGINS = {
    o.strip()
    for o in os.getenv("MCP_ALLOWED_ORIGINS", "http://localhost,http://testserver").split(",")
    if o.strip()
}

# --- FastMCP registration (official SDK, Streamable HTTP) --------------------

try:  # graceful when SDK not installed (REST + executor still work)
    from mcp.server.fastmcp import FastMCP

    fastmcp: FastMCP | None = FastMCP("agent-mcp-server")
    assert fastmcp is not None  # narrowed: None only when SDK import fails

    def _req_auth() -> AuthContext:
        auth = _current_auth.get()
        if auth is None:
            raise AuthError("missing gateway identity")
        return auth

    @fastmcp.tool(name="telemetry.query_metrics")  # type: ignore[misc]
    async def _t_query_metrics(
        service: str,
        metric: str,
        start_time: str,
        end_time: str,
        aggregation: str = "average",
    ) -> dict[str, Any]:
        return await execute_tool(
            "telemetry.query_metrics",
            {
                "service": service,
                "metric": metric,
                "start_time": start_time,
                "end_time": end_time,
                "aggregation": aggregation,
            },
            _req_auth(),
        )

    @fastmcp.tool(name="telemetry.query_logs")  # type: ignore[misc]
    async def _t_query_logs(service: str, query: str, limit: int = 100) -> dict[str, Any]:
        return await execute_tool(
            "telemetry.query_logs",
            {"service": service, "query": query, "limit": limit},
            _req_auth(),
        )

    @fastmcp.tool(name="service.get_health")  # type: ignore[misc]
    async def _t_get_health(service: str) -> dict[str, Any]:
        return await execute_tool("service.get_health", {"service": service}, _req_auth())

    @fastmcp.tool(name="knowledge.search")  # type: ignore[misc]
    async def _t_search(query: str, top_k: int = 5) -> dict[str, Any]:
        return await execute_tool("knowledge.search", {"query": query, "top_k": top_k}, _req_auth())

    @fastmcp.tool(name="knowledge.get_runbook")  # type: ignore[misc]
    async def _t_get_runbook(runbook_id: str) -> dict[str, Any]:
        return await execute_tool("knowledge.get_runbook", {"runbook_id": runbook_id}, _req_auth())

    @fastmcp.tool(name="remediation.simulate")  # type: ignore[misc]
    async def _t_simulate(action: str, target_service: str = "") -> dict[str, Any]:
        args: dict[str, Any] = {"action": action}
        if target_service:
            args["target_service"] = target_service
        return await execute_tool("remediation.simulate", args, _req_auth())

    @fastmcp.tool(name="telemetry.get_trace")  # type: ignore[misc]
    async def _t_get_trace(service: str, trace_id: str = "") -> dict[str, Any]:
        args: dict[str, Any] = {"service": service}
        if trace_id:
            args["trace_id"] = trace_id
        return await execute_tool("telemetry.get_trace", args, _req_auth())

    @fastmcp.tool(name="deployment.get_current_release")  # type: ignore[misc]
    async def _t_get_current_release(service: str) -> dict[str, Any]:
        return await execute_tool(
            "deployment.get_current_release", {"service": service}, _req_auth()
        )

    @fastmcp.tool(name="ticket.create")  # type: ignore[misc]
    async def _t_ticket_create(
        title: str,
        severity: str = "medium",
        dry_run: bool = False,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"title": title, "severity": severity, "dry_run": dry_run}
        if idempotency_key:
            args["idempotency_key"] = idempotency_key
        return await execute_tool("ticket.create", args, _req_auth())

    @fastmcp.tool(name="deployment.rollback")  # type: ignore[misc]
    async def _t_rollback(
        deployment: str, dry_run: bool = True, idempotency_key: str = ""
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"deployment": deployment, "dry_run": dry_run}
        if idempotency_key:
            args["idempotency_key"] = idempotency_key
        return await execute_tool("deployment.rollback", args, _req_auth())

    @fastmcp.resource("resource://services/{name}")  # type: ignore[misc]
    async def _r_service(name: str) -> str:
        svc = get_service(name)
        if svc is None:
            raise ValueError(f"unknown service {name}")
        import json

        return json.dumps(svc)

    @fastmcp.resource("resource://runbooks/{id}")  # type: ignore[misc]
    async def _r_runbook(id: str) -> str:
        rb = get_runbook(id)
        if rb is None:
            raise ValueError(f"unknown runbook {id}")
        return str(rb["body_md"])

    @fastmcp.prompt(name="investigate-incident")  # type: ignore[misc]
    async def _p_investigate(service: str, symptom: str) -> str:
        return render_prompt("investigate-incident", {"service": service, "symptom": symptom})

    @fastmcp.prompt(name="summarize-evidence")  # type: ignore[misc]
    async def _p_summarize(run_id: str) -> str:
        return render_prompt("summarize-evidence", {"run_id": run_id})

    @fastmcp.prompt(name="prepare-change-review")  # type: ignore[misc]
    async def _p_change(change_id: str, action: str, risk: str = "medium") -> str:
        return render_prompt(
            "prepare-change-review", {"change_id": change_id, "action": action, "risk": risk}
        )

except ImportError:  # pragma: no cover
    fastmcp = None


# --- FastAPI app --------------------------------------------------------------


def create_app() -> FastAPI:
    # Fail-closed startup: AUTH_MODE=oidc requires OIDC_ISSUER_URL/AUDIENCE.
    validate_auth_config()
    app = FastAPI(title="MCP Tool Gateway (mock)", version="0.1.0")

    @app.middleware("http")
    async def _origin_and_auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        from packages.security.identity import Identity, resolve_identity

        origin = request.headers.get("origin", "")
        if origin and origin not in ALLOWED_ORIGINS and "localhost" not in origin:
            return JSONResponse({"code": "forbidden", "message": "bad origin"}, status_code=403)
        try:
            gateway_identity: Identity | None = None
            try:
                gateway_identity = resolve_identity(request)
            except Exception:
                gateway_identity = None
            _current_auth.set(
                from_identity(gateway_identity)
                if gateway_identity is not None
                and (request.url.path.startswith("/mcp") or request.url.path.startswith("/tools/"))
                else None
            )
        except AuthError:
            _current_auth.set(None)
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "mcp-server"}

    @app.get("/tools")
    def tools() -> dict[str, object]:
        return {
            "tools": sorted(TOOL_METADATA.keys()),
            "mode": "mock",
            "metadata": [m.model_dump(mode="json") for m in TOOL_METADATA.values()],
        }

    @app.get("/tools/{tool_name}")
    def tool_metadata(tool_name: str) -> dict[str, object]:
        meta = TOOL_METADATA.get(tool_name)
        if meta is None:
            raise HTTPException(404, f"unknown tool {tool_name}")
        return meta.model_dump(mode="json")  # type: ignore[return-value]

    class InvokeBody(BaseModel):
        model_config = ConfigDict(extra="allow")

        args: dict[str, Any] = {}
        idempotency_key: str | None = None
        run_id: str | None = None

    def _auth_or_401(request: Request) -> AuthContext:
        try:
            gateway_identity = getattr(request.state, "identity", None)
            from packages.security.identity import Identity as _Identity

            ident = gateway_identity if isinstance(gateway_identity, _Identity) else None
            if ident is not None:
                claimed = request.headers.get("x-tenant-id", "").strip()
                if claimed and claimed != ident.tenant_id:
                    raise ForbiddenError("tenant mismatch")
                return from_identity(ident)
            return from_headers(dict(request.headers))
        except (AuthError, ForbiddenError) as e:
            status = 403 if isinstance(e, ForbiddenError) else 401
            raise HTTPException(status, str(e)) from e

    @app.post("/tools/{tool_name}/invoke")
    async def invoke_tool(
        tool_name: str,
        body: InvokeBody,
        request: Request,
        x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
    ) -> dict[str, Any]:
        auth = _auth_or_401(request)
        try:
            return await execute_tool(
                tool_name,
                body.args,
                auth,
                idempotency_key=x_idempotency_key or body.idempotency_key,
                run_id=body.run_id,
            )
        except MCPToolError as e:
            status = {
                "validation_error": 422,
                "auth_error": 401,
                "forbidden": 403,
                "not_found": 404,
                "conflict": 409,
                "approval_required": 409,
                "policy_denied": 403,
                "timeout": 504,
            }.get(e.code, 500)
            raise HTTPException(status, {"code": e.code, "message": e.message}) from e

    @app.get("/resources/services/{name}")
    def read_service(name: str) -> dict[str, object]:
        svc = get_service(name)
        if svc is None:
            raise HTTPException(404, f"unknown service {name}")
        return svc

    @app.get("/resources/runbooks/{runbook_id}")
    def read_runbook(runbook_id: str) -> dict[str, object]:
        rb = get_runbook(runbook_id)
        if rb is None:
            raise HTTPException(404, f"unknown runbook {runbook_id}")
        return {"untrusted": True, **rb}

    @app.get("/prompts")
    def list_prompts() -> dict[str, object]:
        return {"prompts": sorted(PROMPTS.keys())}

    @app.get("/prompts/{name}")
    def get_prompt(name: str, request: Request) -> dict[str, object]:
        spec = PROMPTS.get(name)
        if spec is None:
            raise HTTPException(404, f"unknown prompt {name}")
        params = dict(request.query_params)
        try:
            text = render_prompt(name, params)
        except (KeyError, ValueError) as e:
            raise HTTPException(422, str(e)) from e
        return {"name": name, "text": text}

    @app.get("/audit")
    def audit_tail(limit: int = 20) -> dict[str, object]:
        return {"entries": [a.model_dump(mode="json") for a in AUDIT_LOG[-limit:]]}

    if fastmcp is not None:
        app.mount("/mcp", fastmcp.streamable_http_app())

    return app


app = create_app()
