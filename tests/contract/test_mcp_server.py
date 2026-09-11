"""MCP gateway contract tests: valid/invalid/unauthorized/timeout/duplicate keys.

Run: pytest tests/contract/test_mcp_server.py -q
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.mcp_server.catalog import TOOL_METADATA
from apps.mcp_server.executor import MCPToolError, execute_tool
from apps.mcp_server.security import AuthContext
from apps.mcp_server.server import app
from apps.mcp_server.store import AUDIT_LOG, IDEMPOTENCY_STORE, INJECTED_DELAYS, reset_state
from packages.contracts.tools import ToolMetadata

client = TestClient(app)

READER = {"X-Tenant-ID": "tenant_a", "X-Scopes": "tools.read"}
WRITER = {
    "X-Tenant-ID": "tenant_a",
    "X-Scopes": "tools.read,tools.write.deployment.rollback",
    "X-Approved": "true",
}
NOW = "2026-09-11T11:00:00Z"
LATER = "2026-09-11T12:00:00Z"


@pytest.fixture(autouse=True)
def _clean():
    reset_state()
    yield
    reset_state()


def _auth(**kw) -> AuthContext:
    base = {"tenant_id": "tenant_a", "scopes": ["tools.read"]}
    base.update(kw)
    return AuthContext(**base)  # type: ignore[arg-type]


def _metrics_args(**kw):
    args = {
        "service": "checkout-api",
        "metric": "error_rate",
        "start_time": NOW,
        "end_time": LATER,
    }
    args.update(kw)
    return args


# --- metadata ----------------------------------------------------------------


def test_tool_metadata_published_and_valid():
    assert set(TOOL_METADATA) == {
        "telemetry.query_metrics",
        "telemetry.query_logs",
        "telemetry.get_trace",
        "service.get_health",
        "knowledge.search",
        "knowledge.get_runbook",
        "deployment.get_current_release",
        "remediation.simulate",
        "deployment.rollback",
        "ticket.create",
    }
    for meta in TOOL_METADATA.values():
        assert isinstance(meta, ToolMetadata)
        assert meta.version == "1.0.0"
    rb = TOOL_METADATA["deployment.rollback"]
    assert rb.approval_required is True
    assert rb.supports_dry_run is True
    assert rb.category.value == "write" and rb.side_effect.value == "external_write"
    assert rb.required_scopes == ["tools.write.deployment.rollback"]
    assert TOOL_METADATA["telemetry.query_metrics"].category.value == "read"


def test_tools_endpoint_lists_metadata():
    r = client.get("/tools")
    assert r.status_code == 200
    body = r.json()
    assert "deployment.rollback" in body["tools"]
    assert any(m["approval_required"] for m in body["metadata"])


# --- valid paths ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_query_metrics():
    out = await execute_tool("telemetry.query_metrics", _metrics_args(), _auth())
    assert out["service"] == "checkout-api" and out["points"]
    assert len(AUDIT_LOG) == 1
    inv = AUDIT_LOG[0]
    assert inv.tool_name == "telemetry.query_metrics" and inv.status.value == "succeeded"
    assert inv.redacted_input["service"] == "checkout-api"


@pytest.mark.asyncio
async def test_valid_rollback_dry_run_default():
    auth = _auth(scopes=["tools.read", "tools.write.deployment.rollback"])
    out = await execute_tool(
        "deployment.rollback",
        {"deployment": "checkout-api"},
        auth,
    )
    assert out["dry_run"] is True and out["executed"] is False


def test_rest_invoke_valid():
    r = client.post(
        "/tools/service.get_health/invoke",
        json={"args": {"service": "checkout-api"}},
        headers=READER,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "degraded"


# --- invalid ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_rejected_strict():
    with pytest.raises(MCPToolError) as e:
        await execute_tool("telemetry.query_metrics", {"service": "x"}, _auth())
    assert e.value.code == "validation_error"
    with pytest.raises(MCPToolError) as e2:
        await execute_tool("telemetry.query_metrics", {**_metrics_args(), "bogus": 1}, _auth())
    assert e2.value.code == "validation_error"


def test_rest_invoke_invalid_is_422():
    r = client.post(
        "/tools/telemetry.query_metrics/invoke",
        json={"args": {"service": "x"}},
        headers=READER,
    )
    assert r.status_code == 422


# --- unauthorized ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthorized_scope_denied_and_audited():
    with pytest.raises(MCPToolError) as e:
        await execute_tool(
            "deployment.rollback",
            {"deployment": "checkout-api", "dry_run": True},
            _auth(scopes=["tools.read"]),  # missing write scope
        )
    assert e.value.code == "forbidden"
    assert AUDIT_LOG and AUDIT_LOG[-1].authorization_decision.value == "denied"


@pytest.mark.asyncio
async def test_tenant_mismatch_denied():
    with pytest.raises(MCPToolError) as e:
        await execute_tool(
            "service.get_health",
            {"service": "checkout-api", "tenant_id": "tenant_b"},
            _auth(tenant_id="tenant_a"),
        )
    assert e.value.code == "forbidden"


def test_rest_missing_tenant_is_401():
    r = client.post("/tools/service.get_health/invoke", json={"args": {"service": "x"}})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_approval_required_for_real_rollback():
    auth = _auth(scopes=["tools.read", "tools.write.deployment.rollback"], approved=False)
    with pytest.raises(MCPToolError) as e:
        await execute_tool(
            "deployment.rollback",
            {"deployment": "checkout-api", "dry_run": False, "idempotency_key": "k-1"},
            auth,
        )
    assert e.value.code == "approval_required"


# --- timeout --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_path():
    INJECTED_DELAYS["telemetry.query_metrics"] = 0.2
    with pytest.raises(MCPToolError) as e:
        await execute_tool(
            "telemetry.query_metrics", _metrics_args(), _auth(), timeout_override=0.05
        )
    assert e.value.code == "timeout"
    assert AUDIT_LOG[-1].status.value == "timeout"


# --- idempotency -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_keys_dedupe_and_conflict():
    auth = _auth(scopes=["tools.read", "tools.write.deployment.rollback"], approved=True)
    args = {"deployment": "checkout-api", "dry_run": False, "idempotency_key": "dup-1"}
    first = await execute_tool("deployment.rollback", args, auth)
    assert first.get("deduplicated") is None
    second = await execute_tool("deployment.rollback", args, auth)
    assert second["deduplicated"] is True
    with pytest.raises(MCPToolError) as e:
        await execute_tool(
            "deployment.rollback",
            {"deployment": "other-svc", "dry_run": False, "idempotency_key": "dup-1"},
            auth,
        )
    assert e.value.code == "conflict"
    assert len(IDEMPOTENCY_STORE) == 1


@pytest.mark.asyncio
async def test_missing_idempotency_key_rejected_when_not_dry_run():
    auth = _auth(scopes=["tools.read", "tools.write.deployment.rollback"], approved=True)
    with pytest.raises(MCPToolError) as e:
        await execute_tool(
            "deployment.rollback", {"deployment": "checkout-api", "dry_run": False}, auth
        )
    assert e.value.code == "validation_error"


# --- redaction / resources / prompts ----------------------------------------------


@pytest.mark.asyncio
async def test_redaction_in_audit():
    out = await execute_tool(
        "remediation.simulate",
        {"action": "restart", "parameters": {"api_key": "shhh", "n": 1}},
        _auth(),
    )
    assert out  # handler echoes nothing secret
    assert AUDIT_LOG[-1].redacted_input["parameters"]["api_key"] == "[REDACTED]"


def test_resources_and_prompts():
    assert client.get("/resources/services/checkout-api").status_code == 200
    assert client.get("/resources/services/nope").status_code == 404
    rb = client.get("/resources/runbooks/rb-001")
    assert rb.status_code == 200 and rb.json()["untrusted"] is True
    p = client.get("/prompts/investigate-incident", params={"service": "s", "symptom": "y"})
    assert p.status_code == 200 and "s" in p.json()["text"]
    bad = client.get("/prompts/investigate-incident", params={"service": "s"})
    assert bad.status_code == 422
    assert set(client.get("/prompts").json()["prompts"]) == {
        "investigate-incident",
        "summarize-evidence",
        "prepare-change-review",
    }
