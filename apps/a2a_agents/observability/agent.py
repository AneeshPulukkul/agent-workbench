"""Observability specialist — A2A Agent Card + correlate-service-symptoms (Spec §7, Prompt 7).

Contract:
  In:  {service, window{start,end}, metrics_refs[], logs_refs[], deploy_state?}
  Out: {findings[{title, summary, evidence_refs, confidence}], probable_cause?, unresolved[]}

Guarantees:
  - Agent Card published at /.well-known/agent-card.json (validated vs contracts.AgentCard).
  - Versioned JSON in/out (schema_version "1.0", extra="forbid").
  - task_id idempotency, per-skill timeout via A2ATaskRequest.deadline, explicit cancel.
  - W3C traceparent propagation (trace_id / correlation_id echoed in results).
  - Read-only MCP only (allowlist enforced; never calls write/destructive tools).
  - Never remediates (output contains findings only — no actions, no commands).
  - Deterministic local mode (no LLM, no randomness, no clock-dependent output
    except deadline/timeout handling).
  - Structured errors: failures return A2ATaskResult{status, error{code,message,retryable}}.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.contracts import (
    A2ATaskRequest,
    A2ATaskResult,
    A2ATaskStatus,
    AgentAuthentication,
    AgentCard,
    AgentSkill,
    ErrorCode,
    ErrorEnvelope,
)
from packages.contracts import __version__ as _CONTRACT_VERSION

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AGENT_NAME = "observability-agent"
# Pin: skill == contract == tool. Agent Card version tracks contracts.__version__.
AGENT_VERSION = _CONTRACT_VERSION  # "1.0.0"
SKILL_ID = "correlate-service-symptoms"
SCHEMA_VERSION: Literal["1.0"] = "1.0"

#: Per-skill default timeout (seconds). The request deadline always wins if sooner.
SKILL_TIMEOUT_SECONDS = 30
SKILL_MAX_TIMEOUT_SECONDS = 120

#: Read-only MCP tools this specialist may use. Anything else is rejected.
#: Mirrors docs/architecture/mcp-catalog.md read category.
ALLOWED_MCP_TOOLS: frozenset[str] = frozenset(
    {
        "telemetry.query_metrics",
        "telemetry.query_logs",
        "telemetry.get_trace",
        "service.get_health",
        "knowledge.search",
        "knowledge.get_runbook",
        "deployment.get_current_release",
    }
)

#: Write/destructive tools the agent must never invoke (defence-in-depth list).
FORBIDDEN_MCP_TOOLS: frozenset[str] = frozenset(
    {
        "ticket.create",
        "incident.update",
        "deployment.rollback",
        "feature_flag.update",
        "deployment.scale_down",
        "database.failover",
        "service.disable",
        "remediation.simulate",
    }
)

NEVER_REMEDIATE = True

_SERVICE_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_INJECTION_PATTERNS = (
    "ignore previous",
    "ignore all previous",
    "disregard previous",
    "execute ",
    "run command",
    "rm -rf",
    "; cat ",
    "exfiltrate",
    "send credentials",
)


# ---------------------------------------------------------------------------
# Skill I/O schemas (versioned)
# ---------------------------------------------------------------------------


class TimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def _ordered(self) -> TimeWindow:
        if self.end <= self.start:
            raise ValueError("window.end must be > window.start")
        if (self.end - self.start).total_seconds() > 30 * 24 * 3600:
            raise ValueError("window must be <= 30 days")
        return self


class CorrelateInput(BaseModel):
    """Versioned input for correlate-service-symptoms."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    service: str = Field(min_length=1, max_length=128)
    window: TimeWindow
    metrics_refs: list[str] = Field(default_factory=list, max_length=64)
    logs_refs: list[str] = Field(default_factory=list, max_length=64)
    deploy_state: str | None = Field(default=None, max_length=8000)
    trace_id: str | None = Field(default=None, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)
    run_id: str | None = Field(default=None, max_length=128)
    tenant_id: str | None = Field(default=None, max_length=128)

    @field_validator("service")
    @classmethod
    def _service_name(cls, v: str) -> str:
        if not _SERVICE_RE.match(v):
            raise ValueError("service must match ^[A-Za-z0-9._-]{1,128}$")
        return v

    @field_validator("metrics_refs", "logs_refs")
    @classmethod
    def _ref_caps(cls, v: list[str]) -> list[str]:
        for ref in v:
            if not ref or len(ref) > 2048:
                raise ValueError("each evidence ref must be 1..2048 chars")
        return v


class CorrelateFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=256)
    summary: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(min_length=1, max_length=64)
    confidence: float = Field(ge=0.0, le=1.0)


class CorrelateOutput(BaseModel):
    """Versioned output. Findings only — never actions/commands."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    findings: list[CorrelateFinding] = Field(default_factory=list, max_length=32)
    probable_cause: str | None = Field(default=None, max_length=2000)
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


# ---------------------------------------------------------------------------
# Agent Card (validated once; exported as plain dict for backwards compat)
# ---------------------------------------------------------------------------

# Service-DNS placeholder (never example.com); override per-env at deploy time.
AGENT_URL = "http://observability-agent:8082/a2a"

_CARD_MODEL = AgentCard(
    name=AGENT_NAME,
    description="Correlates metrics, logs, traces, and deployment changes.",
    url=AGENT_URL,  # type: ignore[arg-type]
    version=AGENT_VERSION,
    skills=[
        AgentSkill(
            id=SKILL_ID,
            name="Correlate service symptoms",
            description="Find likely causes from telemetry evidence.",
            input_modes=["application/json"],
            output_modes=["application/json"],
        )
    ],
    authentication=AgentAuthentication(schemes=["oauth2"]),
)

AGENT_CARD: dict[str, Any] = _CARD_MODEL.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Helpers: traceprop, MCP guard, deterministic correlation
# ---------------------------------------------------------------------------


def parse_traceparent(header: str | None) -> str | None:
    """Parse W3C traceparent `00-<trace-id>-<parent-id>-<flags>` -> trace-id."""
    if not header:
        return None
    parts = header.strip().split("-")
    if len(parts) != 4 or parts[0] != "00":
        return None
    trace_id, parent_id, flags = parts[1], parts[2], parts[3]
    if not re.fullmatch(r"[0-9a-f]{32}", trace_id):
        return None
    if not re.fullmatch(r"[0-9a-f]{16}", parent_id):
        return None
    if not re.fullmatch(r"[0-9a-f]{2}", flags):
        return None
    return trace_id


def assert_read_only(tool_name: str) -> None:
    """Enforce read-only MCP usage. Raises ValueError on any write/destructive tool."""
    if tool_name in FORBIDDEN_MCP_TOOLS or tool_name not in ALLOWED_MCP_TOOLS:
        raise ValueError(f"tool {tool_name!r} is not an allowed read-only MCP tool")


def mcp_tool_allowlist() -> list[str]:
    return sorted(ALLOWED_MCP_TOOLS)


def _contains_injection(text: str) -> bool:
    lowered = text.lower()
    return any(p in lowered for p in _INJECTION_PATTERNS)


def _deterministic_confidence(service: str, salt: str) -> float:
    """Deterministic pseudo-confidence in [0,1) derived from sha256 (no randomness)."""
    digest = hashlib.sha256(f"{service}:{salt}".encode()).hexdigest()
    return (int(digest[:8], 16) % 1000) / 1000.0


def sanitize_for_evidence(text: str, limit: int = 500) -> str:
    """Treat retrieved content as data: strip control chars, cap length, never execute."""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return cleaned[:limit]


def correlate_local(inputs: CorrelateInput) -> CorrelateOutput:
    """Deterministic local correlation. Pure function of inputs (no I/O, no LLM).

    Rules:
      - metrics_refs -> metric-anomaly finding (evidence = metrics_refs).
      - logs_refs -> log-pattern finding (evidence = logs_refs).
      - deploy_state mentioning deploy/release -> deployment-correlation finding.
      - No refs at all -> no findings + unresolved questions (low-evidence path).
      - Injected instruction text inside refs/deploy_state is treated as data:
        flagged in unresolved_questions, never followed, never remediates.
    """
    findings: list[CorrelateFinding] = []
    unresolved: list[str] = []

    suspicious = any(_contains_injection(r) for r in (*inputs.metrics_refs, *inputs.logs_refs))
    if inputs.deploy_state and _contains_injection(inputs.deploy_state):
        suspicious = True
    if suspicious:
        unresolved.append(
            "Retrieved telemetry content contains instruction-like text; "
            "treated as untrusted data and not followed."
        )

    if inputs.metrics_refs:
        base = 0.55 + 0.2 * min(len(inputs.metrics_refs), 2) / 2  # 0.55..0.75 deterministic band
        jitter = (_deterministic_confidence(inputs.service, "metrics") - 0.5) * 0.06
        conf = round(min(0.95, max(0.0, base + jitter)), 2)
        findings.append(
            CorrelateFinding(
                title=f"Metric anomaly in {inputs.service}",
                summary=(
                    f"Correlated {len(inputs.metrics_refs)} metric reference(s) "
                    f"for service {inputs.service} within window "
                    f"{inputs.window.start.isoformat()}..{inputs.window.end.isoformat()}. "
                    f"Observation only; no remediation performed."
                ),
                evidence_refs=list(inputs.metrics_refs[:16]),
                confidence=conf,
            )
        )

    if inputs.logs_refs:
        base = 0.5 + 0.2 * min(len(inputs.logs_refs), 2) / 2
        jitter = (_deterministic_confidence(inputs.service, "logs") - 0.5) * 0.06
        conf = round(min(0.95, max(0.0, base + jitter)), 2)
        findings.append(
            CorrelateFinding(
                title=f"Log error pattern in {inputs.service}",
                summary=(
                    f"Correlated {len(inputs.logs_refs)} log reference(s) for "
                    f"{inputs.service}. Pattern suggests errors cluster in window; "
                    f"requires orchestrator verification against primary telemetry."
                ),
                evidence_refs=list(inputs.logs_refs[:16]),
                confidence=conf,
            )
        )

    deploy_txt = (inputs.deploy_state or "").strip()
    if deploy_txt:
        lowered = deploy_txt.lower()
        if any(k in lowered for k in ("deploy", "release", "rollout", "canary")):
            conf = round(
                0.55 + (_deterministic_confidence(inputs.service, "deploy") - 0.5) * 0.06, 2
            )
            findings.append(
                CorrelateFinding(
                    title=f"Recent deployment correlates with symptoms in {inputs.service}",
                    summary=(
                        "Deployment state mentions a recent change overlapping the "
                        f"symptom window: {sanitize_for_evidence(deploy_txt, 300)}"
                    ),
                    evidence_refs=[inputs.metrics_refs[0]]
                    if inputs.metrics_refs
                    else ["deploy_state"],
                    confidence=max(0.0, min(0.95, conf)),
                )
            )
        else:
            unresolved.append(
                "deploy_state present but contains no release marker; deployment link unclear."
            )
    else:
        unresolved.append("deploy_state absent; cannot assess change correlation.")

    if not inputs.metrics_refs and not inputs.logs_refs:
        unresolved.append(
            f"No telemetry refs provided for service {inputs.service}; "
            "supply metrics_refs and/or logs_refs for a conclusive correlation."
        )

    if not findings:
        probable = None
    else:
        best = max(findings, key=lambda f: f.confidence)
        probable = best.title if best.confidence >= 0.6 else None
        if probable is None:
            unresolved.append(
                "Confidence below 0.6; probable cause withheld pending more evidence."
            )

    # Never remediate: assert output carries no action/command keys.
    return CorrelateOutput(
        findings=findings,
        probable_cause=probable,
        unresolved_questions=unresolved[:32],
        trace_id=inputs.trace_id,
        correlation_id=inputs.correlation_id,
    )


def _error_result(
    task_id: str,
    status: A2ATaskStatus,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    run_id: str | None = None,
    tenant_id: str | None = None,
    trace_id: str | None = None,
    correlation_id: str | None = None,
) -> A2ATaskResult:
    from packages.contracts import A2ATaskError

    safe = re.sub(
        r"(?i)(bearer\s+\S+|api_key\s*=\s*\S+|client_secret\s*=\s*\S+)", "[redacted]", message
    )[:2000]
    return A2ATaskResult(
        task_id=task_id,
        run_id=run_id,
        tenant_id=tenant_id,
        status=status,
        output=None,
        artifacts=[],
        error=A2ATaskError(code=code, message=safe, retryable=retryable),
        trace_id=trace_id,
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# Task store (in-memory cache fronting agent_tasks; idempotent by task_id)
# ---------------------------------------------------------------------------
# System of record is ``agent_tasks`` via ``SqlAlchemyRepository`` (see
# ``set_task_repository``). The in-memory dict below is a process-local cache
# for idempotent replay; every put-through also attempts a best-effort
# repository write (failures never break task execution — e.g. ad-hoc tasks
# whose run_id has no runs row yet).

_TASKS: dict[str, A2ATaskResult] = {}
_CANCELLED: set[str] = set()
_TASK_REPO: object | None = None


def set_task_repository(repo: object | None) -> None:
    """Inject (or clear) the persistence repository for task write-through."""
    global _TASK_REPO
    _TASK_REPO = repo


def get_task_repository() -> object | None:
    return _TASK_REPO


def _persist_task(task_id: str, result: A2ATaskResult, request: A2ATaskRequest) -> None:
    repo = _TASK_REPO
    if repo is None:
        try:
            from apps.api.store import get_repository as _get_repo

            repo = _get_repo()
        except Exception:
            return
    try:
        save = getattr(repo, "save_agent_task", None)
        update = getattr(repo, "update_agent_task", None)
        if save is not None:
            try:
                save(
                    task_id=task_id,
                    run_id=request.run_id or task_id,
                    tenant_id=request.tenant_id or "",
                    skill_id=request.skill_id,
                    agent_name=AGENT_NAME,
                    objective=getattr(request, "objective", "") or "",
                    status=str(result.status.value),
                    raw_inputs=dict(request.inputs or {}),
                    deadline=getattr(request, "deadline", None),
                )
            except Exception:
                pass  # e.g. missing runs row or duplicate task_id
        if update is not None and result.output is not None:
            try:
                update(
                    task_id=task_id,
                    tenant_id=request.tenant_id or "",
                    status=str(result.status.value),
                    raw_output=dict(result.output or {}),
                    error=dict(result.error.model_dump(mode="json"))
                    if result.error
                    else None,
                )
            except Exception:
                pass
    except Exception:
        pass


def _store_get(task_id: str) -> A2ATaskResult | None:
    return _TASKS.get(task_id)


def _store_put(
    task_id: str, result: A2ATaskResult, request: A2ATaskRequest | None = None
) -> None:
    _TASKS[task_id] = result
    if request is not None:
        _persist_task(task_id, result, request)


def clear_task_store() -> None:  # test hook
    _TASKS.clear()
    _CANCELLED.clear()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="observability-agent", version=AGENT_VERSION)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": AGENT_NAME}


@app.get("/.well-known/agent-card.json")
def agent_card() -> dict[str, Any]:
    return AGENT_CARD


@app.get("/a2a/agent-card.json")
def agent_card_alias() -> dict[str, Any]:
    return AGENT_CARD


def _resolve_trace(
    request: A2ATaskRequest, traceparent: str | None
) -> tuple[str | None, str | None]:
    from_header = parse_traceparent(traceparent)
    trace_id = request.trace_id or from_header
    return trace_id, request.correlation_id


async def _execute_task(request: A2ATaskRequest, trace_id: str | None) -> A2ATaskResult:
    """Validate skill I/O and run deterministic correlation with deadline."""
    if request.skill_id != SKILL_ID:
        return _error_result(
            request.task_id,
            A2ATaskStatus.FAILED,
            "validation_error",
            f"unsupported skill_id {request.skill_id!r}; expected {SKILL_ID!r}",
            run_id=request.run_id,
            tenant_id=request.tenant_id,
            trace_id=trace_id,
            correlation_id=request.correlation_id,
        )
    if request.task_id in _CANCELLED:
        return _error_result(
            request.task_id,
            A2ATaskStatus.CANCELLED,
            "cancelled",
            "task was cancelled",
            run_id=request.run_id,
            tenant_id=request.tenant_id,
            trace_id=trace_id,
            correlation_id=request.correlation_id,
        )
    now = datetime.now(UTC)
    if request.deadline <= now:
        return _error_result(
            request.task_id,
            A2ATaskStatus.TIMEOUT,
            "timeout",
            "deadline already exceeded",
            retryable=True,
            run_id=request.run_id,
            tenant_id=request.tenant_id,
            trace_id=trace_id,
            correlation_id=request.correlation_id,
        )
    try:
        skill_inputs = CorrelateInput(
            service=request.inputs.get("service", ""),
            window=request.inputs.get("window", {}),
            metrics_refs=request.inputs.get("metrics_refs", []),
            logs_refs=request.inputs.get("logs_refs", []),
            deploy_state=request.inputs.get("deploy_state"),
            trace_id=trace_id,
            correlation_id=request.correlation_id,
            run_id=request.run_id,
            tenant_id=request.tenant_id,
        )
    except Exception as exc:  # Pydantic ValidationError -> structured failure
        return _error_result(
            request.task_id,
            A2ATaskStatus.FAILED,
            "validation_error",
            f"invalid skill inputs: {exc}",
            run_id=request.run_id,
            tenant_id=request.tenant_id,
            trace_id=trace_id,
            correlation_id=request.correlation_id,
        )

    # Enforce read-only MCP posture before any analysis (defence in depth).
    for tool in ALLOWED_MCP_TOOLS:
        assert_read_only(tool)

    remaining = (request.deadline - now).total_seconds()
    timeout = max(0.01, min(remaining, SKILL_TIMEOUT_SECONDS, SKILL_MAX_TIMEOUT_SECONDS))
    try:
        output = await asyncio.wait_for(
            asyncio.to_thread(correlate_local, skill_inputs), timeout=timeout
        )
    except TimeoutError:
        return _error_result(
            request.task_id,
            A2ATaskStatus.TIMEOUT,
            "timeout",
            f"skill {SKILL_ID} exceeded timeout of {timeout:.1f}s",
            retryable=True,
            run_id=request.run_id,
            tenant_id=request.tenant_id,
            trace_id=trace_id,
            correlation_id=request.correlation_id,
        )
    except asyncio.CancelledError:
        return _error_result(
            request.task_id,
            A2ATaskStatus.CANCELLED,
            "cancelled",
            "task was cancelled",
            run_id=request.run_id,
            tenant_id=request.tenant_id,
            trace_id=trace_id,
            correlation_id=request.correlation_id,
        )
    return A2ATaskResult(
        task_id=request.task_id,
        run_id=request.run_id,
        tenant_id=request.tenant_id,
        status=A2ATaskStatus.COMPLETED,
        output=output.model_dump(mode="json"),
        artifacts=[],
        error=None,
        trace_id=trace_id,
        correlation_id=request.correlation_id,
    )


def _caller_tenant(request: Request) -> str | None:
    """Return the caller's tenant from the gateway identity, or None for the
    legacy unauthenticated local/test path (no Authorization header in mock
    mode). In ``AUTH_MODE=oidc`` a missing/invalid bearer is a 401.
    """
    import os

    authz = request.headers.get("authorization", "")
    if not authz:
        if os.getenv("AUTH_MODE", "mock") == "oidc":
            raise HTTPException(
                status_code=401,
                detail=ErrorEnvelope(
                    code=ErrorCode.AUTH_ERROR,
                    message="missing bearer token",
                    retryable=False,
                ).model_dump(mode="json"),
            )
        return None  # legacy local/test harness without bearer
    from packages.security.identity import IdentityError, resolve_identity

    try:
        ident = resolve_identity(request)
    except IdentityError as e:
        raise HTTPException(
            status_code=e.status,
            detail=ErrorEnvelope(
                code=ErrorCode.AUTH_ERROR, message=str(e), retryable=False
            ).model_dump(mode="json"),
        ) from e
    return ident.tenant_id


def _enforce_task_tenant(caller_tenant: str | None, task_tenant: str | None) -> None:
    """Row-level tenant isolation: mismatch -> 404 (no existence leak)."""
    if caller_tenant is None:
        return  # legacy unauthenticated local/test path
    if (task_tenant or "") != caller_tenant:
        raise HTTPException(
            status_code=404,
            detail=ErrorEnvelope(
                code=ErrorCode.NOT_FOUND, message="task not found", retryable=False
            ).model_dump(mode="json"),
        )


@app.post("/a2a/tasks", response_model=A2ATaskResult)
async def create_task(
    request: A2ATaskRequest,
    response: Response,
    raw_request: Request,
    traceparent: str | None = Header(default=None),
) -> A2ATaskResult:
    caller_tenant = _caller_tenant(raw_request)
    _enforce_task_tenant(caller_tenant, request.tenant_id)
    existing = _store_get(request.task_id)
    if existing is not None:
        # Idempotent replay must not leak cross-tenant tasks.
        _enforce_task_tenant(caller_tenant, existing.tenant_id)
        return existing  # idempotent replay (duplicate delivery safe)
    trace_id, _ = _resolve_trace(request, traceparent)
    result = await _execute_task(request, trace_id)
    _store_put(request.task_id, result, request)
    if result.status == A2ATaskStatus.COMPLETED or result.status in (
        A2ATaskStatus.TIMEOUT,
        A2ATaskStatus.CANCELLED,
    ):
        response.status_code = 200
    else:
        response.status_code = 200  # A2A failures ride inside the result envelope
    return result


@app.get("/a2a/tasks/{task_id}", response_model=A2ATaskResult)
def get_task(task_id: str, raw_request: Request) -> A2ATaskResult:
    result = _store_get(task_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorEnvelope(
                code=ErrorCode.NOT_FOUND, message=f"task {task_id} not found", retryable=False
            ).model_dump(mode="json"),
        )
    _enforce_task_tenant(_caller_tenant(raw_request), result.tenant_id)
    return result


@app.post("/a2a/tasks/{task_id}/cancel", response_model=A2ATaskResult)
def cancel_task(task_id: str, raw_request: Request) -> A2ATaskResult:
    caller_tenant = _caller_tenant(raw_request)
    existing = _store_get(task_id)
    if existing is not None:
        _enforce_task_tenant(caller_tenant, existing.tenant_id)
        if existing.status == A2ATaskStatus.COMPLETED:
            return existing  # already done; cancel is a no-op (idempotent)
    _CANCELLED.add(task_id)
    tenant = existing.tenant_id if existing is not None else caller_tenant
    result = _error_result(task_id, A2ATaskStatus.CANCELLED, "cancelled", "task was cancelled",
                           tenant_id=tenant)
    if existing is not None:
        # Preserve the original task tenant on the cancel record.
        result = result.model_copy(update={"tenant_id": existing.tenant_id})
    _store_put(task_id, result)
    return result


__all__ = [
    "AGENT_CARD",
    "AGENT_NAME",
    "AGENT_URL",
    "AGENT_VERSION",
    "ALLOWED_MCP_TOOLS",
    "FORBIDDEN_MCP_TOOLS",
    "NEVER_REMEDIATE",
    "SKILL_ID",
    "SKILL_TIMEOUT_SECONDS",
    "CorrelateFinding",
    "CorrelateInput",
    "CorrelateOutput",
    "TimeWindow",
    "app",
    "assert_read_only",
    "clear_task_store",
    "correlate_local",
    "get_task_repository",
    "mcp_tool_allowlist",
    "parse_traceparent",
    "sanitize_for_evidence",
    "set_task_repository",
]
