"""Consistent error envelope across REST / MCP / A2A / SSE (domain-model.md)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION: Literal["1.0"] = "1.0"


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"
    AUTH_ERROR = "auth_error"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    MODEL_ERROR = "model_error"
    TOOL_ERROR = "tool_error"
    A2A_ERROR = "a2a_error"
    POLICY_DENIED = "policy_denied"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_EXPIRED = "approval_expired"
    BUDGET_EXCEEDED = "budget_exceeded"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"
    UNAVAILABLE = "unavailable"


class ErrorEnvelope(BaseModel):
    """Safe message (no secrets); redacted details only."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    code: ErrorCode
    message: str = Field(min_length=1, max_length=2000)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=128)
    retryable: bool = False
    details: dict[str, Any] | None = None
    trace_id: str | None = Field(default=None, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)

    @field_validator("message")
    @classmethod
    def _no_secret_markers(cls, v: str) -> str:
        lowered = v.lower()
        for marker in ("bearer ", "api_key", "apikey", "client_secret", "-----begin"):
            if marker in lowered:
                raise ValueError("error message must not contain secret material")
        return v

    @field_validator("details")
    @classmethod
    def _cap_details(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return v
        if len(v) > 16:
            raise ValueError("details must have <= 16 keys")
        import json

        if len(json.dumps(v, default=str)) > 8192:
            raise ValueError("details must be <= 8KB serialized")
        return v


__all__ = ["SCHEMA_VERSION", "ErrorCode", "ErrorEnvelope"]
