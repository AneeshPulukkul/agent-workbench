"""Run lifecycle contracts (Spec §4.1 + docs/architecture/domain-model.md).

Covers: RunStatus, AgentRequest, Run, Finding, EvidenceReference,
ProposedAction, AgentResult.

Conventions (all contract modules):
- Pydantic v2, explicit types, ``extra="forbid"`` on every top-level model.
- ``schema_version: Literal["1.0"]`` on every model (URL major + body version).
- ``tenant_id`` / ``run_id`` / ``trace_id`` / ``correlation_id`` present
  wherever they are relevant for isolation, replay, and trace propagation.
- No unbounded model text is coerced into an executable command:
  ``ProposedAction.input`` is a validated mapping (size-capped) and must be
  checked against the target tool's JSON Schema + policy before execution.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION: Literal["1.0"] = "1.0"

TenantId = Field(
    min_length=1,
    max_length=128,
    pattern=r"^[A-Za-z0-9._-]+$",
    description="Tenant scope. Enforced on every row; mismatch -> deny.",
)
RunId = Field(
    min_length=1,
    max_length=128,
    pattern=r"^[A-Za-z0-9._-]+$",
    description="Durable run identifier (e.g. run_123).",
)


class RunStatus(StrEnum):
    """Canonical run lifecycle (domain-model.md)."""

    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SeverityLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceKind(StrEnum):
    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"
    DOC = "doc"
    RUNBOOK = "runbook"
    AGENT = "agent"


class EvidenceReference(BaseModel):
    """Pointer to redacted evidence. Raw content lives behind audit policy only."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    ref_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    kind: EvidenceKind
    uri: str = Field(min_length=1, max_length=2048)
    excerpt_hash: str | None = Field(default=None, min_length=1, max_length=256)
    span_ref: str | None = Field(default=None, max_length=256)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("uri")
    @classmethod
    def _uri_scheme(cls, v: str) -> str:
        allowed = ("resource://", "telemetry://", "trace://", "https://", "a2a://")
        if not v.startswith(allowed):
            raise ValueError(f"uri must start with one of {allowed}")
        return v


class Finding(BaseModel):
    """Structured diagnosis unit. Every material finding cites >=1 evidence ref."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    finding_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    summary: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    confidence: float = Field(ge=0.0, le=1.0)
    severity: SeverityLevel | None = None
    trace_id: str | None = Field(default=None, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)

    @field_validator("evidence_refs")
    @classmethod
    def _non_empty_refs(cls, v: list[str]) -> list[str]:
        for ref in v:
            if not ref or len(ref) > 128:
                raise ValueError("each evidence ref must be 1..128 chars")
        return v


class AgentRequest(BaseModel):
    """Run creation request (POST /v1/runs). Tenant comes from auth token."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    tenant_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    user_id: str = Field(min_length=1, max_length=256)
    objective: str = Field(min_length=1, max_length=8192)
    context: dict[str, Any] = Field(default_factory=dict)
    max_tool_calls: int = Field(default=20, ge=1, le=100)
    max_model_calls: int = Field(default=10, ge=1, le=50)
    max_cost_usd: float = Field(default=2.0, ge=0.0, le=1000.0)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    trace_id: str | None = Field(default=None, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)

    @field_validator("context")
    @classmethod
    def _cap_context(cls, v: dict[str, Any]) -> dict[str, Any]:
        # api-contracts.md: context <= 64KB. Approximate via key count + repr size.
        if len(v) > 64:
            raise ValueError("context must have <= 64 keys")
        import json

        size = len(json.dumps(v, default=str))
        if size > 65536:
            raise ValueError("context must be <= 64KB serialized")
        return v


class RunBudget(BaseModel):
    """Explicit budget block persisted on the run (runs.budget_json)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    max_tool_calls: int = Field(ge=1, le=100)
    max_model_calls: int = Field(ge=1, le=50)
    max_cost_usd: float = Field(ge=0.0, le=1000.0)
    consumed_tool_calls: int = Field(default=0, ge=0)
    consumed_model_calls: int = Field(default=0, ge=0)
    consumed_cost_usd: float = Field(default=0.0, ge=0.0)


class Run(BaseModel):
    """Durable run row (runs table). State persisted after every transition."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    tenant_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    created_by: str = Field(min_length=1, max_length=256)
    objective: str = Field(min_length=1, max_length=8192)
    status: RunStatus = RunStatus.CREATED
    current_state: str = Field(default="created", min_length=1, max_length=128)
    budget: RunBudget
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    trace_id: str | None = Field(default=None, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)

    @field_validator("completed_at")
    @classmethod
    def _terminal_requires_completed_at(cls, v: AwareDatetime | None, info) -> AwareDatetime | None:
        # Light cross-field guidance enforced at orchestrator level; keep soft here.
        return v


class ProposedAction(BaseModel):
    """Consequential action proposal. NEVER built from raw model text directly.

    The orchestrator must validate ``input`` against the target tool's JSON
    Schema and evaluate policy + human approval before execution.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    action_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$")
    reason: str = Field(min_length=1, max_length=2000)
    input: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel
    requires_approval: bool = True
    rollback: str | None = Field(default=None, max_length=2000)
    dry_run: bool = False
    idempotency_key: str = Field(min_length=1, max_length=128)
    trace_id: str | None = Field(default=None, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)

    @field_validator("input")
    @classmethod
    def _cap_input(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > 50:
            raise ValueError("action input must have <= 50 keys")
        import json

        if len(json.dumps(v, default=str)) > 32768:
            raise ValueError("action input must be <= 32KB serialized")
        return v

    @field_validator("requires_approval", mode="after")
    @classmethod
    def _high_risk_needs_approval(cls, v: bool, info) -> bool:
        risk = info.data.get("risk")
        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL) and not v:
            raise ValueError("high/critical risk actions must require approval")
        return v


class AgentResult(BaseModel):
    """Final structured synthesis for a run."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=128)
    answer: str = Field(min_length=1, max_length=16000)
    findings: list[Finding] = Field(default_factory=list, max_length=64)
    proposed_actions: list[ProposedAction] = Field(default_factory=list, max_length=16)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=32)
    trace_id: str | None = Field(default=None, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)

    @field_validator("unresolved_questions")
    @classmethod
    def _cap_questions(cls, v: list[str]) -> list[str]:
        for q in v:
            if not q or len(q) > 1000:
                raise ValueError("each unresolved question must be 1..1000 chars")
        return v


__all__ = [
    "SCHEMA_VERSION",
    "AgentRequest",
    "AgentResult",
    "EvidenceKind",
    "EvidenceReference",
    "Finding",
    "ProposedAction",
    "RiskLevel",
    "Run",
    "RunBudget",
    "RunStatus",
    "SeverityLevel",
]
