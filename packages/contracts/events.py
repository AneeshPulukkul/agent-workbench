"""Canonical internal event model (Spec §4.2). Mapped to AG-UI at the boundary."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION: Literal["1.0"] = "1.0"


class EventType(StrEnum):
    RUN_STARTED = "run.started"
    MESSAGE_DELTA = "message.delta"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    AGENT_DELEGATED = "agent.delegated"
    FINDING_CREATED = "finding.created"
    APPROVAL_REQUIRED = "approval.required"
    APPROVAL_RECEIVED = "approval.received"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"


class AgentEvent(BaseModel):
    """Persist-then-publish. (run_id, sequence) is unique; sequence orders replay."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    event_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    tenant_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    sequence: int = Field(ge=0, le=1_000_000)
    type: EventType
    timestamp: AwareDatetime
    data: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = Field(default=None, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)
    sensitive: bool = False
    safe_for_ui: bool = False

    @model_validator(mode="after")
    def _sensitive_never_ui_safe(self) -> "AgentEvent":
        if self.sensitive and self.safe_for_ui:
            raise ValueError("sensitive events must not be marked safe_for_ui")
        return self


__all__ = ["SCHEMA_VERSION", "EventType", "AgentEvent"]
