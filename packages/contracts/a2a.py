"""A2A delegation contracts (Spec §7 + docs/architecture/a2a-cards.md).

Orchestrator depends only on Agent Card + skills + auth + I/O schemas +
lifecycle + timeouts. Specialist output is untrusted evidence until correlated.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import AnyHttpUrl, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION: Literal["1.0"] = "1.0"


class A2ATaskStatus(StrEnum):
    PENDING = "pending"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class A2ATaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    task_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    skill_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    agent_name: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=8192)
    inputs: dict[str, Any] = Field(default_factory=dict)
    callback_url: AnyHttpUrl | None = None
    deadline: AwareDatetime
    requester: str = Field(min_length=1, max_length=256)
    trace_id: str | None = Field(default=None, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)


class A2ATaskError(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2000)
    retryable: bool = False


class A2ATaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    task_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=128)
    status: A2ATaskStatus
    output: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list, max_length=32)
    error: A2ATaskError | None = None
    trace_id: str | None = Field(default=None, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _status_payload_consistency(self) -> A2ATaskResult:
        if self.status == A2ATaskStatus.COMPLETED and self.error is not None:
            raise ValueError("completed results must not carry error")
        if self.status in (A2ATaskStatus.FAILED, A2ATaskStatus.TIMEOUT) and self.error is None:
            raise ValueError("failed/timeout results must carry error")
        return self


class AgentSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=2000)
    input_modes: list[str] = Field(default_factory=lambda: ["application/json"], max_length=8)
    output_modes: list[str] = Field(default_factory=lambda: ["application/json"], max_length=8)


class AgentAuthentication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    schemes: list[str] = Field(min_length=1, max_length=8)


class AgentCard(BaseModel):
    """Specialist Agent Card (Spec §7.1)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9-]+$")
    description: str = Field(min_length=1, max_length=2000)
    url: AnyHttpUrl
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$", min_length=1, max_length=32)
    skills: list[AgentSkill] = Field(min_length=1, max_length=32)
    authentication: AgentAuthentication


__all__ = [
    "SCHEMA_VERSION",
    "A2ATaskError",
    "A2ATaskRequest",
    "A2ATaskResult",
    "A2ATaskStatus",
    "AgentAuthentication",
    "AgentCard",
    "AgentSkill",
]
