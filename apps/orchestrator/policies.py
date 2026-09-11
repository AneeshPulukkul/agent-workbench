"""Policy hook — Python policy interface (ADR-003, Spec §10.2).

Bootstrap defaults (kept for backwards compat):
  read-only auto-allow, writes need approval, destructive denied.

Plus :func:`evaluate_action`, a richer contract-typed evaluator used by the
bounded graph: tenant isolation, senior-operator gating for prod
rollback/failover, deny-by-default for destructive.
"""

from __future__ import annotations

from dataclasses import dataclass

POLICY_RULE_VERSION = "1.0"


@dataclass
class PolicyDecision:
    allowed: bool
    requires_approval: bool
    reason: str
    rule_version: str = POLICY_RULE_VERSION


def evaluate(tool_name: str, side_effect: str) -> PolicyDecision:
    """Bootstrap default: read-only auto-allow, writes need approval, destructive denied."""
    if side_effect == "none":
        return PolicyDecision(True, False, "read-only tool")
    if side_effect == "destructive":
        return PolicyDecision(False, False, "destructive tools denied by default")
    return PolicyDecision(True, True, f"{tool_name} requires human approval")


# ---------------------------------------------------------------------------
# Contract-typed evaluator for the orchestrator graph
# ---------------------------------------------------------------------------

_SENIOR_TOOLS = {"deployment.rollback", "database.failover"}
_DESTRUCTIVE_TOOLS = {"deployment.scale_down", "database.failover", "service.disable"}


def evaluate_action(
    *,
    tool_name: str,
    side_effect: str,
    tenant_id: str | None = None,
    resource_tenant_id: str | None = None,
    env: str = "dev",
    scopes: list[str] | None = None,
    run_id: str | None = None,
    trace_id: str | None = None,
    correlation_id: str | None = None,
) -> object:
    """Return a packages.contracts PolicyDecision (pure function)."""
    from packages.contracts import PolicyDecision as ContractDecision

    scopes = list(scopes or [])
    if tenant_id is not None and resource_tenant_id is not None and tenant_id != resource_tenant_id:
        return ContractDecision(
            allowed=False,
            requires_approval=False,
            reason="tenant mismatch -> deny",
            required_scopes=[],
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            correlation_id=correlation_id,
            rule_version=POLICY_RULE_VERSION,
        )
    if side_effect == "destructive" or tool_name in _DESTRUCTIVE_TOOLS:
        return ContractDecision(
            allowed=False,
            requires_approval=False,
            reason="destructive tools denied by default",
            required_scopes=[],
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            correlation_id=correlation_id,
            rule_version=POLICY_RULE_VERSION,
        )
    if side_effect == "none":
        return ContractDecision(
            allowed=True,
            requires_approval=False,
            reason="read-only tool auto-allowed",
            required_scopes=[],
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            correlation_id=correlation_id,
            rule_version=POLICY_RULE_VERSION,
        )
    if tool_name in _SENIOR_TOOLS and env == "prod":
        return ContractDecision(
            allowed=True,
            requires_approval=True,
            reason=f"{tool_name} in prod requires senior-operator approval",
            required_scopes=["case.approve", "senior-operator"],
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            correlation_id=correlation_id,
            rule_version=POLICY_RULE_VERSION,
        )
    return ContractDecision(
        allowed=True,
        requires_approval=True,
        reason=f"{tool_name} requires human approval",
        required_scopes=["case.approve"],
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=trace_id,
        correlation_id=correlation_id,
        rule_version=POLICY_RULE_VERSION,
    )


__all__ = ["POLICY_RULE_VERSION", "PolicyDecision", "evaluate", "evaluate_action"]
