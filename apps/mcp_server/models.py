"""Strict Pydantic input models for MCP tools (extra=forbid)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

_TENANT = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
_SERVICE = Field(min_length=1, max_length=128)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class QueryMetricsInput(_Strict):
    tenant_id: str | None = _TENANT
    service: str = _SERVICE
    metric: str = Field(min_length=1, max_length=128)
    start_time: AwareDatetime
    end_time: AwareDatetime
    aggregation: Literal["average", "p95", "p99", "max", "min", "sum", "count"] = "average"


class QueryLogsInput(_Strict):
    tenant_id: str | None = _TENANT
    service: str = _SERVICE
    query: str = Field(min_length=1, max_length=2000)
    start_time: AwareDatetime | None = None
    end_time: AwareDatetime | None = None
    limit: int = Field(default=100, ge=1, le=1000)


class GetHealthInput(_Strict):
    tenant_id: str | None = _TENANT
    service: str = _SERVICE


class SearchInput(_Strict):
    tenant_id: str | None = _TENANT
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)


class GetRunbookInput(_Strict):
    tenant_id: str | None = _TENANT
    runbook_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")


class SimulateInput(_Strict):
    tenant_id: str | None = _TENANT
    action: str = Field(min_length=1, max_length=500)
    parameters: dict[str, Any] = Field(default_factory=dict)
    target_service: str | None = Field(default=None, min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class RollbackInput(_Strict):
    tenant_id: str | None = _TENANT
    deployment: str = Field(min_length=1, max_length=128)
    target_release: str | None = Field(default=None, min_length=1, max_length=128)
    reason: str | None = Field(default=None, min_length=1, max_length=1000)
    dry_run: bool = True
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class GetTraceInput(_Strict):
    tenant_id: str | None = _TENANT
    service: str = _SERVICE
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)


class GetCurrentReleaseInput(_Strict):
    tenant_id: str | None = _TENANT
    service: str = _SERVICE


class TicketCreateInput(_Strict):
    tenant_id: str | None = _TENANT
    title: str = Field(min_length=1, max_length=500)
    severity: str = Field(default="medium", min_length=1, max_length=32)
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    service: str | None = Field(default=None, min_length=1, max_length=128)
    dry_run: bool = False
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


INPUT_MODELS: dict[str, type[_Strict]] = {
    "telemetry.query_metrics": QueryMetricsInput,
    "telemetry.query_logs": QueryLogsInput,
    "telemetry.get_trace": GetTraceInput,
    "service.get_health": GetHealthInput,
    "knowledge.search": SearchInput,
    "knowledge.get_runbook": GetRunbookInput,
    "deployment.get_current_release": GetCurrentReleaseInput,
    "remediation.simulate": SimulateInput,
    "deployment.rollback": RollbackInput,
    "ticket.create": TicketCreateInput,
}

__all__ = [
    "INPUT_MODELS",
    "GetCurrentReleaseInput",
    "GetHealthInput",
    "GetRunbookInput",
    "GetTraceInput",
    "QueryLogsInput",
    "QueryMetricsInput",
    "RollbackInput",
    "SearchInput",
    "SimulateInput",
    "TicketCreateInput",
]
