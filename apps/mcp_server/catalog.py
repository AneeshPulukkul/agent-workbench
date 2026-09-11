"""ToolMetadata catalog — single source of truth (skill==contract==tool, v1.0.0)."""

from __future__ import annotations

from packages.contracts.tools import ToolMetadata

TOOL_VERSION = "1.0.0"


def _meta(**kw) -> ToolMetadata:
    return ToolMetadata(**kw)


TOOL_METADATA: dict[str, ToolMetadata] = {
    "telemetry.query_metrics": _meta(
        name="telemetry.query_metrics",
        description="Query mock metrics for a service over a time window.",
        category="read",
        side_effect="none",
        idempotent=True,
        timeout_seconds=30,
        required_scopes=["tools.read"],
        approval_required=False,
        supports_dry_run=False,
        owner="telemetry",
        version=TOOL_VERSION,
    ),
    "telemetry.query_logs": _meta(
        name="telemetry.query_logs",
        description="Query mock logs for a service.",
        category="read",
        side_effect="none",
        idempotent=True,
        timeout_seconds=30,
        required_scopes=["tools.read"],
        approval_required=False,
        supports_dry_run=False,
        owner="telemetry",
        version=TOOL_VERSION,
    ),
    "telemetry.get_trace": _meta(
        name="telemetry.get_trace",
        description="Get a mock distributed trace by id (read-only).",
        category="read",
        side_effect="none",
        idempotent=True,
        timeout_seconds=30,
        required_scopes=["tools.read"],
        approval_required=False,
        supports_dry_run=False,
        owner="telemetry",
        version=TOOL_VERSION,
    ),
    "service.get_health": _meta(
        name="service.get_health",
        description="Get mock health for a service.",
        category="read",
        side_effect="none",
        idempotent=True,
        timeout_seconds=15,
        required_scopes=["tools.read"],
        approval_required=False,
        supports_dry_run=False,
        owner="platform",
        version=TOOL_VERSION,
    ),
    "knowledge.search": _meta(
        name="knowledge.search",
        description="Search mock knowledge base.",
        category="read",
        side_effect="none",
        idempotent=True,
        timeout_seconds=15,
        required_scopes=["tools.read"],
        approval_required=False,
        supports_dry_run=False,
        owner="knowledge",
        version=TOOL_VERSION,
    ),
    "knowledge.get_runbook": _meta(
        name="knowledge.get_runbook",
        description="Get runbook markdown (UNTRUSTED content).",
        category="read",
        side_effect="none",
        idempotent=True,
        timeout_seconds=15,
        required_scopes=["tools.read"],
        approval_required=False,
        supports_dry_run=False,
        owner="knowledge",
        version=TOOL_VERSION,
    ),
    "deployment.get_current_release": _meta(
        name="deployment.get_current_release",
        description="Get mock current release for a service (read-only).",
        category="read",
        side_effect="none",
        idempotent=True,
        timeout_seconds=15,
        required_scopes=["tools.read"],
        approval_required=False,
        supports_dry_run=False,
        owner="deployments",
        version=TOOL_VERSION,
    ),
    "remediation.simulate": _meta(
        name="remediation.simulate",
        description="Dry-run simulation of a remediation action. Never executes.",
        category="analysis",
        side_effect="none",
        idempotent=True,
        timeout_seconds=60,
        required_scopes=["tools.read"],
        approval_required=False,
        supports_dry_run=True,
        owner="remediation",
        version=TOOL_VERSION,
    ),
    "deployment.rollback": _meta(
        name="deployment.rollback",
        description="Mock rollback. dry_run=true default; never acts locally.",
        category="write",
        side_effect="external_write",
        idempotent=True,
        timeout_seconds=60,
        required_scopes=["tools.write.deployment.rollback"],
        approval_required=True,
        supports_dry_run=True,
        owner="deployments",
        version=TOOL_VERSION,
    ),
    "ticket.create": _meta(
        name="ticket.create",
        description="Create a mock ops ticket (propose-only; approval-gated).",
        category="write",
        side_effect="external_write",
        idempotent=False,
        timeout_seconds=60,
        required_scopes=["tools.write.ticket.create"],
        approval_required=True,
        supports_dry_run=True,
        owner="platform",
        version=TOOL_VERSION,
    ),
}

WRITE_TOOLS = {"deployment.rollback", "ticket.create"}
READ_TOOLS = {
    "telemetry.query_metrics",
    "telemetry.query_logs",
    "telemetry.get_trace",
    "service.get_health",
    "knowledge.search",
    "knowledge.get_runbook",
    "deployment.get_current_release",
}

# Per-tool timeouts (seconds) — single source for orchestrator + gateway.
# Orchestrator MUST import this mapping instead of hard-coding MCP_TIMEOUT.
TOOL_TIMEOUTS: dict[str, float] = {
    name: float(meta.timeout_seconds) for name, meta in TOOL_METADATA.items()
}

__all__ = ["READ_TOOLS", "TOOL_METADATA", "TOOL_TIMEOUTS", "TOOL_VERSION", "WRITE_TOOLS"]
