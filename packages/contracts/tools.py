"""MCP tool contracts (Spec §6.2 + docs/architecture/mcp-catalog.md)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION: Literal["1.0"] = "1.0"


class ToolCategory(StrEnum):
    READ = "read"
    ANALYSIS = "analysis"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class SideEffect(StrEnum):
    NONE = "none"
    EXTERNAL_WRITE = "external_write"
    DESTRUCTIVE = "destructive"


class ToolInvocationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class AuthorizationDecision(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    REQUIRES_APPROVAL = "requires_approval"


class ToolMetadata(BaseModel):
    """Published per tool beyond its JSON Schema. Pinned by skill==contract==tool."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    name: str = Field(
        min_length=1, max_length=128, pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$",
        description="Namespaced tool name, e.g. telemetry.query_metrics.",
    )
    description: str = Field(min_length=1, max_length=2000)
    category: ToolCategory
    side_effect: SideEffect
    idempotent: bool
    timeout_seconds: int = Field(ge=1, le=600)
    required_scopes: list[str] = Field(default_factory=list, max_length=16)
    approval_required: bool
    supports_dry_run: bool
    owner: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=32, pattern=r"^\d+\.\d+\.\d+$")

    @model_validator(mode="after")
    def _category_policy(self) -> "ToolMetadata":
        if self.category in (ToolCategory.WRITE, ToolCategory.DESTRUCTIVE) and not self.approval_required:
            raise ValueError("write/destructive tools must require approval")
        if self.category == ToolCategory.READ and self.side_effect != SideEffect.NONE:
            raise ValueError("read tools must have side_effect=none")
        if self.side_effect == SideEffect.DESTRUCTIVE and self.category != ToolCategory.DESTRUCTIVE:
            raise ValueError("destructive side_effect requires category=destructive")
        if self.side_effect == SideEffect.EXTERNAL_WRITE and self.category not in (
            ToolCategory.WRITE, ToolCategory.DESTRUCTIVE, ToolCategory.ANALYSIS,
        ):
            raise ValueError("external_write side_effect requires write/destructive/analysis category")
        return self


class ToolInvocation(BaseModel):
    """Audit row for one tool call (tool_invocations table). Hashes + redacted only."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    invocation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    tenant_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    tool_name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$")
    tool_version: str = Field(min_length=1, max_length=32, pattern=r"^\d+\.\d+\.\d+$")
    input_hash: str = Field(min_length=1, max_length=256)
    redacted_input: dict[str, Any] = Field(default_factory=dict)
    output_hash: str | None = Field(default=None, min_length=1, max_length=256)
    status: ToolInvocationStatus
    authorization_decision: AuthorizationDecision | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    dry_run: bool = False
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    trace_id: str | None = Field(default=None, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)
    error: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _completed_after_started(self) -> "ToolInvocation":
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at must be >= started_at")
        if self.status in (
            ToolInvocationStatus.SUCCEEDED, ToolInvocationStatus.FAILED,
            ToolInvocationStatus.TIMEOUT, ToolInvocationStatus.CANCELLED,
        ) and self.completed_at is None:
            raise ValueError("terminal invocations require completed_at")
        return self


__all__ = [
    "SCHEMA_VERSION",
    "ToolCategory",
    "SideEffect",
    "ToolInvocationStatus",
    "AuthorizationDecision",
    "ToolMetadata",
    "ToolInvocation",
]
