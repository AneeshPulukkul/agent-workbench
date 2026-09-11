"""Tool allowlists: which principals may invoke which tools (Spec §10.3).

Defense-in-depth behind scope authZ: even a scoped principal can only invoke
tools on its role allowlist. Retrieved model/A2A text can never widen this.
"""

from __future__ import annotations

import re

from packages.security.identity import Identity

TOOL_NAME_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")

DEFAULT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "telemetry.query_metrics",
        "telemetry.query_logs",
        "service.get_health",
        "knowledge.search",
        "knowledge.get_runbook",
        "remediation.simulate",
    }
)

ALLOWLIST_BY_ROLE: dict[str, frozenset[str]] = {
    "analyst": DEFAULT_ALLOWLIST,
    "operator": DEFAULT_ALLOWLIST | frozenset({"deployment.rollback"}),
    "senior-operator": DEFAULT_ALLOWLIST | frozenset({"deployment.rollback", "database.failover"}),
    "agent": DEFAULT_ALLOWLIST,  # A2A/MCP service principals: read-only
}

# Scope -> extra tools granted beyond the role allowlist.
SCOPE_TOOL_GRANTS: dict[str, frozenset[str]] = {
    "tools.write.deployment.rollback": frozenset({"deployment.rollback"}),
    "tools.write.*": frozenset({"deployment.rollback"}),
    "*": frozenset(
        {
            "telemetry.query_metrics",
            "telemetry.query_logs",
            "service.get_health",
            "knowledge.search",
            "knowledge.get_runbook",
            "remediation.simulate",
            "deployment.rollback",
            "database.failover",
        }
    ),
}


class AllowlistError(Exception):
    def __init__(self, message: str, *, code: str = "tool_not_allowed") -> None:
        super().__init__(message)
        self.code = code


def roles_for(identity: Identity) -> set[str]:
    roles = {"analyst"}
    if "senior-operator" in identity.scopes:
        roles.add("senior-operator")
    if any(s.startswith("tools.write.") for s in identity.scopes) or "*" in identity.scopes:
        roles.add("operator")
    if identity.mode in ("service", "agent"):
        roles.add("agent")
    return roles


def allowed_tools_for(identity: Identity) -> frozenset[str]:
    allowed: set[str] = set()
    for role in roles_for(identity):
        allowed |= set(ALLOWLIST_BY_ROLE.get(role, frozenset()))
    for scope in identity.scopes:
        allowed |= set(SCOPE_TOOL_GRANTS.get(scope, frozenset()))
        if scope.endswith(".*"):
            prefix = scope[:-2]
            for tool in DEFAULT_ALLOWLIST | {"deployment.rollback", "database.failover"}:
                if tool.startswith(prefix + "."):
                    allowed.add(tool)
    return frozenset(allowed)


def is_tool_allowed(tool_name: str, identity: Identity) -> bool:
    if not TOOL_NAME_RE.match(tool_name):
        return False
    return tool_name in allowed_tools_for(identity)


def validate_tool_call(tool_name: str, identity: Identity) -> str:
    """Allowlist gate. Raises AllowlistError (caller maps to 403 envelope)."""
    if not TOOL_NAME_RE.match(tool_name):
        raise AllowlistError(f"invalid tool name: {tool_name!r}", code="validation_error")
    if not is_tool_allowed(tool_name, identity):
        raise AllowlistError(
            f"tool {tool_name} not on allowlist for roles {sorted(roles_for(identity))}"
        )
    return tool_name


__all__ = [
    "ALLOWLIST_BY_ROLE",
    "DEFAULT_ALLOWLIST",
    "AllowlistError",
    "allowed_tools_for",
    "is_tool_allowed",
    "roles_for",
    "validate_tool_call",
]
