"""Approval + policy contracts (Spec §10.2 + domain-model.md)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .run import RiskLevel

SCHEMA_VERSION: Literal["1.0"] = "1.0"


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Approval(BaseModel):
    """Human decision row. Terminal actions check this row, not the event stream."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    approval_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    tenant_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    action_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    requested_by: str = Field(min_length=1, max_length=256)
    approver: str | None = Field(default=None, min_length=1, max_length=256)
    decision: ApprovalDecision = ApprovalDecision.PENDING
    reason: str | None = Field(default=None, max_length=2000)
    requested_at: AwareDatetime
    decided_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    trace_id: str | None = Field(default=None, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _decision_consistency(self) -> "Approval":
        terminal = (ApprovalDecision.APPROVED, ApprovalDecision.REJECTED, ApprovalDecision.EXPIRED)
        if self.decision in terminal:
            if self.decided_at is None:
                raise ValueError("terminal approvals require decided_at")
            if self.decision in (ApprovalDecision.APPROVED, ApprovalDecision.REJECTED) and not self.approver:
                raise ValueError("approved/rejected decisions require approver")
            if self.decided_at < self.requested_at:
                raise ValueError("decided_at must be >= requested_at")
        else:
            if self.decided_at is not None:
                raise ValueError("pending approvals must not have decided_at")
        if self.expires_at is not None and self.expires_at < self.requested_at:
            raise ValueError("expires_at must be >= requested_at")
        return self


class ApprovalDecisionRequest(BaseModel):
    """Body for POST /v1/runs/{id}/approvals/{approval_id}/decide."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    decision: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=2000)
    approver: str = Field(min_length=1, max_length=256)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class PolicyDecision(BaseModel):
    """Pure function of (principal, action, context). Span + audit record each."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    rule_version: str = Field(
        default="1.0",
        min_length=1,
        max_length=32,
        pattern=r"^\d+\.\d+$",
        description="Policy rule-bundle version (additive; default 1.0).",
    )
    allowed: bool
    requires_approval: bool
    reason: str = Field(min_length=1, max_length=2000)
    required_scopes: list[str] = Field(default_factory=list, max_length=16)
    max_impact: RiskLevel | None = None
    tenant_id: str | None = Field(default=None, min_length=1, max_length=128)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    trace_id: str | None = Field(default=None, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _deny_is_terminal(self) -> "PolicyDecision":
        if not self.allowed and self.requires_approval:
            raise ValueError("denied decisions must not require approval (deny is terminal)")
        return self


__all__ = [
    "SCHEMA_VERSION",
    "ApprovalDecision",
    "Approval",
    "ApprovalDecisionRequest",
    "PolicyDecision",
]
