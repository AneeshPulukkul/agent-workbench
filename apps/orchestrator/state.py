"""Durable run state for the bounded investigation graph (Spec §5 + domain-model.md).

Persisted after every transition; drives resume/replay and budget enforcement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class BudgetLimits:
    max_model_calls: int = 10
    max_tool_calls: int = 20
    max_delegations: int = 3
    max_duration_seconds: float = 600.0
    max_cost_usd: float = 2.0
    # Additive token/delegation limits, mirroring apps.orchestrator.runtime.BudgetLimits.
    max_input_tokens: int = 60_000
    max_output_tokens: int = 20_000
    max_delegation_depth: int = 3


@dataclass
class BudgetUsage:
    model_calls: int = 0
    tool_calls: int = 0
    delegations: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    delegation_depth: int = 0


@dataclass
class RunState:
    run_id: str
    tenant_id: str = "tenant_a"
    objective: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    status: str = "created"  # created|running|waiting_for_approval|completed|failed|cancelled
    current_state: str = "classify"
    budget: dict[str, Any] = field(default_factory=dict)
    limits: BudgetLimits = field(default_factory=BudgetLimits)
    usage: BudgetUsage = field(default_factory=BudgetUsage)
    started_at: datetime = field(default_factory=utcnow)
    trace_id: str | None = None
    correlation_id: str | None = None
    # Workflow artefacts (JSON-serialisable dicts)
    classification: dict[str, Any] = field(default_factory=dict)
    plan: list[str] = field(default_factory=list)
    resources: dict[str, Any] = field(default_factory=dict)
    mcp_evidence: list[dict[str, Any]] = field(default_factory=list)
    a2a_result: dict[str, Any] | None = None
    a2a_fallback: bool = False
    findings: list[dict[str, Any]] = field(default_factory=list)
    proposed_actions: list[dict[str, Any]] = field(default_factory=list)
    policy_decisions: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    outcome: str | None = None  # clarify|more_info|denied|rejected|completed|failed|cancelled
    error: dict[str, Any] | None = None
    conflict: str | None = None
    retried_reads: bool = False
    event_sequence: int = 0
    cancelled: bool = False


__all__ = ["BudgetLimits", "BudgetUsage", "RunState", "utcnow"]
