"""Bounded investigation graph (Spec §5, Prompt 8).

Canonical workflow::

    classify -> plan -> resources -> mcp_reads -> a2a_delegate -> correlate
      -> findings -> propose -> policy -> approval_pause
      -> execute_approved -> verify -> complete

Branches:
  - insufficient context -> clarify (terminal, completed with unresolved question)
  - low confidence        -> more_info (terminal, completed with unresolved)
  - policy denied         -> explain (terminal completed, nothing executed)
  - approval rejected     -> complete-with-rejection (nothing executed)

Properties (all enforced + unit-tested):
  - Persist after every transition (Store.save) and emit a canonical
    AgentEvent (Store.append) on every transition.
  - Budgets enforced before each billable step: max model calls, tool calls,
    delegations, wall-clock duration, cost USD. Exceeding -> run.failed with
    BUDGET_EXCEEDED.
  - Timeouts per MCP/A2A call + overall deadline; circuit-breaker per
    dependency (opens after N consecutive failures, forces degraded fallback).
  - Cooperative cancellation checked at every transition; in-flight A2A/MCP
    work is abandoned and run.cancelled is emitted.
  - Safe retries: idempotent READ tools retried (bounded, deterministic);
    write/destructive tools are never auto-retried.
  - Never exec model text as command: every ProposedAction is built from an
    allowlisted tool registry entry, validated against name regex + JSON size
    caps + policy; raw model strings suggesting shell/unknown tools are
    rejected and recorded as unresolved.
  - Specialist (A2A) output is untrusted: schema-validated, conflict-checked
    against primary MCP evidence, never trusted blindly.

The graph is a plain deterministic state machine (no LLM network calls).
Production wiring (LangGraphRuntime via AgentRuntime, LiteLLM router,
PG persistence) wraps this core without changing its semantics.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from packages.contracts import ErrorCode, EventType

from .state import BudgetLimits, RunState, utcnow

# ---------------------------------------------------------------------------
# Canonical states
# ---------------------------------------------------------------------------

STATES = [
    "classify",
    "plan",
    "resources",
    "mcp_reads",
    "a2a_delegate",
    "correlate",
    "findings",
    "propose",
    "policy",
    "approval_pause",
    "execute_approved",
    "verify",
    "complete",
]


def describe() -> str:
    return " -> ".join(STATES)


# ---------------------------------------------------------------------------
# Budgets / costs / timeouts
# ---------------------------------------------------------------------------
# Timeouts are single-sourced from apps.mcp_server.catalog (v1.0.0).
# MCP_TIMEOUT_SECONDS is retained as a deprecated backwards-compat alias
# (max catalog timeout); new code MUST use mcp_timeout_for(tool_name).

MODEL_COST_USD = 0.05
TOOL_COST_USD = 0.01
DELEGATION_COST_USD = 0.10

from apps.mcp_server.catalog import TOOL_METADATA as _CATALOG_METADATA  # noqa: E402
from apps.mcp_server.catalog import TOOL_TIMEOUTS as _CATALOG_TIMEOUTS  # noqa: E402

TOOL_TIMEOUTS: dict[str, float] = dict(_CATALOG_TIMEOUTS)
MCP_TIMEOUT_SECONDS = max(TOOL_TIMEOUTS.values()) if TOOL_TIMEOUTS else 60.0
MCP_TIMEOUTS = TOOL_TIMEOUTS  # alias for parity tests / callers


def mcp_timeout_for(tool_name: str) -> float:
    """Per-tool MCP timeout (seconds) from the catalog single source."""
    return float(TOOL_TIMEOUTS.get(tool_name, MCP_TIMEOUT_SECONDS))


A2A_TIMEOUT_SECONDS = 30.0
MAX_READ_RETRIES = 2
CIRCUIT_BREAKER_THRESHOLD = 3
LOW_CONFIDENCE_THRESHOLD = 0.5

TOOL_NAME_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
SHELL_HINT_RE = re.compile(r"(rm\s+-rf|;\s*cat\s|&&|\|\||`|\$\(|exec\s*\()", re.IGNORECASE)


class BudgetExceededError(RuntimeError):
    def __init__(self, message: str, *, budget: str = "unknown"):
        super().__init__(message)
        self.budget = budget


class RunCancelledError(RuntimeError):
    pass


class CircuitOpenError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Tool registry (allowlist). skill == contract == tool pin target.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    name: str
    category: str  # read|analysis|write|destructive
    side_effect: str  # none|external_write|destructive
    idempotent: bool
    approval_required: bool
    timeout_seconds: float = 30.0
    version: str = "1.0.0"


def _spec_from_catalog(name: str) -> ToolSpec:
    meta = _CATALOG_METADATA[name]
    return ToolSpec(
        name=meta.name,
        category=meta.category.value,
        side_effect=meta.side_effect.value,
        idempotent=meta.idempotent,
        approval_required=meta.approval_required,
        timeout_seconds=float(meta.timeout_seconds),
        version=meta.version,
    )


# Allowlist mirrors the catalog exactly (catalog is single source of truth).
# Decision documented in docs/architecture/mcp-catalog.md: the three graph-only
# tools (telemetry.get_trace, deployment.get_current_release, ticket.create)
# were added to the catalog (additive), and catalog-only remediation.simulate
# was added here — so catalog == graph.
TOOL_REGISTRY: dict[str, ToolSpec] = {name: _spec_from_catalog(name) for name in _CATALOG_METADATA}

READ_TOOLS = [n for n, s in TOOL_REGISTRY.items() if s.category == "read"]


def validate_tool_call(tool_name: str, args: dict[str, Any]) -> ToolSpec:
    """Reject anything that is not an allowlisted tool invocation.

    This is the 'never exec model text as command' gate: unknown names,
    shell metacharacters, and oversize payloads are rejected before any
    policy check or execution.
    """
    if not TOOL_NAME_RE.match(tool_name):
        raise ValueError(f"tool_name {tool_name!r} is not an allowlisted tool (regex mismatch)")
    if SHELL_HINT_RE.search(tool_name) or SHELL_HINT_RE.search(str(args)):
        raise ValueError("refusing to execute model text as command: shell metacharacters detected")
    spec = TOOL_REGISTRY.get(tool_name)
    if spec is None:
        raise ValueError(f"tool {tool_name!r} is not in the allowlisted registry")
    import json

    if len(json.dumps(args, default=str)) > 32768:
        raise ValueError("tool args exceed 32KB cap")
    if len(args) > 50:
        raise ValueError("tool args exceed 50 keys")
    return spec


def build_proposed_action(
    *,
    run_id: str,
    tenant_id: str,
    tool_name: str,
    reason: str,
    tool_input: dict[str, Any],
    risk: str = "medium",
    rollback: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build a validated ProposedAction dict (never from raw model text directly)."""
    from packages.contracts import ProposedAction

    spec = validate_tool_call(tool_name, tool_input)
    requires_approval = (
        True if spec.approval_required else spec.category in ("write", "destructive")
    )
    if risk in ("high", "critical"):
        requires_approval = True
    key_src = f"{run_id}:{tool_name}:{sorted(tool_input.items(), key=lambda kv: kv[0])!r}"
    idem = "idem-" + hashlib.sha256(key_src.encode()).hexdigest()[:16]
    action = ProposedAction(
        action_id=f"{run_id}-{tool_name.replace('.', '-')}-{idem[-6:]}",
        run_id=run_id,
        tenant_id=tenant_id,
        tool_name=tool_name,  # type: ignore[arg-type]
        reason=reason[:2000],
        input=tool_input,
        risk=risk,  # type: ignore[arg-type]
        requires_approval=requires_approval,
        rollback=rollback,
        dry_run=dry_run,
        idempotency_key=idem,
    )
    return action.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Protocols (injectable for deterministic tests / prod wiring)
# ---------------------------------------------------------------------------


class MCPClient(Protocol):
    def invoke(
        self, tool_name: str, args: dict[str, Any], *, timeout_s: float
    ) -> dict[str, Any]: ...


class A2AClient(Protocol):
    def delegate(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]: ...


class PolicyClient(Protocol):
    def evaluate(self, action: dict[str, Any], *, env: str = "dev") -> dict[str, Any]: ...


class Store(Protocol):
    def save(self, state: RunState) -> None: ...

    def append(self, event: dict[str, Any]) -> None: ...


class ModelClient(Protocol):
    def classify(self, objective: str, context: dict[str, Any]) -> dict[str, Any]: ...

    def plan(self, objective: str, classification: dict[str, Any]) -> list[str]: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


# ---------------------------------------------------------------------------
# Default fakes (deterministic; used in tests + local mode)
# ---------------------------------------------------------------------------


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeModelClient:
    """Deterministic classifier/planner: pure function of (objective, context)."""

    def classify(self, objective: str, context: dict[str, Any]) -> dict[str, Any]:
        obj = (objective or "").strip()
        if len(obj) < 8:
            return {
                "category": "unknown",
                "needs_clarification": True,
                "reason": "objective too short",
            }
        lowered = obj.lower()
        if "checkout" in lowered or "error rate" in lowered or "incident" in lowered:
            category = "incident"
        elif "deploy" in lowered or "rollback" in lowered:
            category = "change"
        else:
            category = "investigation"
        if not context.get("service") and "service" not in lowered:
            return {
                "category": category,
                "needs_clarification": True,
                "reason": "missing service context",
            }
        return {
            "category": category,
            "needs_clarification": False,
            "reason": f"{category} investigation",
        }

    def plan(self, objective: str, classification: dict[str, Any]) -> list[str]:
        return [
            "retrieve service metadata",
            "query read-only telemetry (metrics/logs/health)",
            "delegate to observability specialist",
            "correlate specialist output with primary evidence",
            "propose approval-gated actions",
        ]


class FakeMCPClient:
    """Deterministic MCP reads. Flags drive failure-injection tests."""

    def __init__(
        self,
        *,
        fail_tools: dict[str, str] | None = None,  # tool -> "timeout"|"error"|"malformed"
        health_status: str = "degraded",
    ):
        self.fail_tools = dict(fail_tools or {})
        self.health_status = health_status
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def invoke(self, tool_name: str, args: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        validate_tool_call(tool_name, args)
        self.calls.append((tool_name, dict(args)))
        mode = self.fail_tools.get(tool_name)
        if mode == "timeout":
            raise TimeoutError(f"tool {tool_name} timed out")
        if mode == "error":
            raise RuntimeError(f"tool {tool_name} failed")
        if mode == "malformed":
            return {"unexpected_blob": object()}  # caller must reject safely
        service = str(args.get("service", "checkout-api"))
        if tool_name == "telemetry.query_metrics":
            return {"service": service, "error_rate": 0.18, "ref": f"telemetry://m/{service}/err"}
        if tool_name == "telemetry.query_logs":
            return {"service": service, "errors": 42, "ref": f"telemetry://l/{service}/err"}
        if tool_name == "service.get_health":
            return {
                "service": service,
                "status": self.health_status,
                "ref": f"telemetry://h/{service}",
            }
        if tool_name == "deployment.get_current_release":
            return {
                "service": service,
                "release": "2026.09.10",
                "ref": f"resource://releases/{service}",
            }
        if tool_name == "telemetry.get_trace":
            trace_id = str(args.get("trace_id", "trace-mock-1"))
            return {
                "service": service,
                "trace_id": trace_id,
                "ref": f"trace://t/{service}/{trace_id}",
            }
        if tool_name == "remediation.simulate":
            return {
                "action": str(args.get("action", "restart")),
                "executed": False,
                "dry_run": True,
                "ref": "simulate://s/1",
            }
        if tool_name in ("ticket.create", "deployment.rollback"):
            return {"ok": True, "dry_run": bool(args.get("dry_run", False)), "ref": "ticket://t/1"}
        return {"ok": True}


class FakeA2AClient:
    """Deterministic A2A delegation. Flags drive failure-injection tests."""

    def __init__(self, *, mode: str = "ok", confidence: float = 0.8):
        assert mode in ("ok", "timeout", "failed", "low_confidence", "conflict", "malformed")
        self.mode = mode
        self.confidence = confidence
        self.calls: list[dict[str, Any]] = []
        self.cancelled: list[str] = []

    def delegate(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        self.calls.append(dict(request))
        task_id = str(request.get("task_id", "task_1"))
        if self.mode == "timeout":
            raise TimeoutError("A2A delegation timed out")
        if self.mode == "failed":
            return {
                "task_id": task_id,
                "status": "failed",
                "error": {
                    "code": "a2a_error",
                    "message": "specialist unavailable",
                    "retryable": True,
                },
            }
        if self.mode == "malformed":
            return {"task_id": task_id, "status": "completed", "output": {"bogus": 1}}
        if self.mode == "low_confidence":
            return {
                "task_id": task_id,
                "status": "completed",
                "output": {
                    "findings": [],
                    "probable_cause": None,
                    "unresolved_questions": ["insufficient telemetry refs"],
                },
            }
        if self.mode == "conflict":
            return {
                "task_id": task_id,
                "status": "completed",
                "output": {
                    "findings": [
                        {
                            "title": "All healthy",
                            "summary": "No anomaly detected",
                            "evidence_refs": ["a2a://obs/1"],
                            "confidence": 0.9,
                        }
                    ],
                    "probable_cause": "All healthy",
                    "unresolved_questions": [],
                },
            }
        conf = self.confidence
        return {
            "task_id": task_id,
            "status": "completed",
            "output": {
                "findings": [
                    {
                        "title": "Metric anomaly in checkout-api",
                        "summary": "Elevated error rate correlates with recent release",
                        "evidence_refs": ["telemetry://m/checkout-api/err"],
                        "confidence": conf,
                    }
                ],
                "probable_cause": "Metric anomaly in checkout-api",
                "unresolved_questions": [],
            },
        }

    def cancel(self, task_id: str) -> None:
        self.cancelled.append(task_id)


class FakePolicyClient:
    def __init__(self, *, mode: str = "approve_all"):
        assert mode in ("approve_all", "deny_rollback", "deny_all")
        self.mode = mode

    def evaluate(self, action: dict[str, Any], *, env: str = "dev") -> dict[str, Any]:
        from .policies import evaluate_action

        tool = str(action.get("tool_name", ""))
        spec = TOOL_REGISTRY.get(tool)
        side_effect = spec.side_effect if spec else "external_write"
        if self.mode == "deny_all":
            return {
                "allowed": False,
                "requires_approval": False,
                "reason": "denied by test policy",
                "required_scopes": [],
            }
        if self.mode == "deny_rollback" and tool == "deployment.rollback":
            return {
                "allowed": False,
                "requires_approval": False,
                "reason": "rollback denied in test policy",
                "required_scopes": [],
            }
        decision = evaluate_action(tool_name=tool, side_effect=side_effect, env=env)
        assert hasattr(decision, "model_dump")
        return decision.model_dump(mode="json")  # type: ignore[attr-defined]


class InMemoryPersistence:
    """Test/local persistence: records saves + events for assertions."""

    def __init__(self) -> None:
        self.saves: list[RunState] = []
        self.events: list[dict[str, Any]] = []

    def save(self, state: RunState) -> None:
        from dataclasses import replace

        self.saves.append(replace(state))

    def append(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))

    def event_types(self) -> list[str]:
        return [str(e.get("type")) for e in self.events]


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    def __init__(self, threshold: int = CIRCUIT_BREAKER_THRESHOLD):
        self.threshold = threshold
        self.failures: dict[str, int] = {}
        self.opened: set[str] = set()

    def guard(self, key: str) -> None:
        if key in self.opened:
            raise CircuitOpenError(f"circuit open for {key}")

    def record_success(self, key: str) -> None:
        self.failures[key] = 0
        self.opened.discard(key)

    def record_failure(self, key: str) -> None:
        n = self.failures.get(key, 0) + 1
        self.failures[key] = n
        if n >= self.threshold:
            self.opened.add(key)


# ---------------------------------------------------------------------------
# Orchestrator graph
# ---------------------------------------------------------------------------


@dataclass
class GraphDeps:
    mcp: MCPClient = field(default_factory=FakeMCPClient)
    a2a: A2AClient = field(default_factory=FakeA2AClient)
    policy: PolicyClient = field(default_factory=FakePolicyClient)
    store: Store = field(default_factory=InMemoryPersistence)
    model: ModelClient = field(default_factory=FakeModelClient)
    clock: Clock = field(default_factory=SystemClock)
    limits: BudgetLimits = field(default_factory=BudgetLimits)


class OrchestratorGraph:
    """Deterministic bounded workflow executor."""

    def __init__(self, deps: GraphDeps | None = None):
        self.deps = deps or GraphDeps()
        self.breaker = CircuitBreaker()

    # -- events / persistence -------------------------------------------
    def _emit(
        self,
        state: RunState,
        type: str,
        data: dict[str, Any],
        *,
        sensitive: bool = False,
        safe_for_ui: bool = True,
    ) -> None:
        from packages.contracts import AgentEvent

        state.event_sequence += 1
        ev = AgentEvent(
            event_id=f"{state.run_id}-e{state.event_sequence}",
            run_id=state.run_id,
            tenant_id=state.tenant_id,
            sequence=state.event_sequence,
            type=type,  # type: ignore[arg-type]
            timestamp=utcnow(),
            data=data,
            trace_id=state.trace_id,
            correlation_id=state.correlation_id,
            sensitive=sensitive,
            safe_for_ui=(safe_for_ui and not sensitive),
        )
        self.deps.store.append(ev.model_dump(mode="json"))

    def _transition(
        self, state: RunState, next_state: str, event_type: str, data: dict[str, Any] | None = None
    ) -> None:
        state.current_state = next_state
        self._emit(state, event_type, {"state": next_state, **(data or {})})
        self.deps.store.save(state)

    def _check_cancel(self, state: RunState) -> None:
        if state.cancelled:
            raise RunCancelledError("run was cancelled")

    def _check_budgets(self, state: RunState) -> None:
        lim = state.limits
        use = state.usage
        if use.model_calls > lim.max_model_calls:
            raise BudgetExceededError("max_model_calls exceeded", budget="model_calls")
        if use.tool_calls > lim.max_tool_calls:
            raise BudgetExceededError("max_tool_calls exceeded", budget="tool_calls")
        if use.delegations > lim.max_delegations:
            raise BudgetExceededError("max_delegations exceeded", budget="delegations")
        if getattr(use, "input_tokens", 0) > getattr(lim, "max_input_tokens", 60_000):
            raise BudgetExceededError("max_input_tokens exceeded", budget="input_tokens")
        if getattr(use, "output_tokens", 0) > getattr(lim, "max_output_tokens", 20_000):
            raise BudgetExceededError("max_output_tokens exceeded", budget="output_tokens")
        depth = max(use.delegations, getattr(use, "delegation_depth", 0))
        if depth > getattr(lim, "max_delegation_depth", lim.max_delegations):
            raise BudgetExceededError("max_delegation_depth exceeded", budget="delegation_depth")
        if use.cost_usd > lim.max_cost_usd + 1e-9:
            raise BudgetExceededError("max_cost_usd exceeded", budget="cost")
        elapsed = (self.deps.clock.now() - state.started_at).total_seconds()
        if elapsed > lim.max_duration_seconds:
            raise BudgetExceededError("max_duration exceeded", budget="duration")

    def _spend_model(self, state: RunState) -> None:
        state.usage.model_calls += 1
        state.usage.cost_usd = round(state.usage.cost_usd + MODEL_COST_USD, 4)
        self._check_budgets(state)

    def _spend_tool(self, state: RunState) -> None:
        state.usage.tool_calls += 1
        state.usage.cost_usd = round(state.usage.cost_usd + TOOL_COST_USD, 4)
        self._check_budgets(state)

    def _spend_delegation(self, state: RunState) -> None:
        state.usage.delegations += 1
        if hasattr(state.usage, "delegation_depth"):
            state.usage.delegation_depth += 1
        state.usage.cost_usd = round(state.usage.cost_usd + DELEGATION_COST_USD, 4)
        self._check_budgets(state)

    # -- MCP helpers ------------------------------------------------------
    def _mcp_read(
        self, state: RunState, tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Idempotent read with bounded safe retries + circuit breaker. Returns None on degrade."""
        spec = validate_tool_call(tool_name, args)
        assert spec.category == "read", f"{tool_name} is not a read tool"
        self.breaker.guard("mcp")
        self._emit(
            state, EventType.TOOL_STARTED.value, {"tool_name": tool_name, "args": _redact(args)}
        )
        last_err: Exception | None = None
        for attempt in range(MAX_READ_RETRIES + 1):
            try:
                self._check_cancel(state)
                self._spend_tool(state)
                started = time.monotonic()
                out = self.deps.mcp.invoke(tool_name, args, timeout_s=mcp_timeout_for(tool_name))
                _reject_malformed(tool_name, out)
                self.breaker.record_success("mcp")
                self._emit(
                    state,
                    EventType.TOOL_COMPLETED.value,
                    {
                        "tool_name": tool_name,
                        "status": "succeeded",
                        "latency_s": round(time.monotonic() - started, 4),
                    },
                )
                return out
            except (RunCancelledError, BudgetExceededError):
                raise
            except (TimeoutError, ConnectionError) as exc:
                last_err = exc
                if attempt >= MAX_READ_RETRIES:
                    break
                continue  # safe retry: reads only
            except Exception as exc:  # malformed / validation -> do not retry blindly
                last_err = exc
                break
        self.breaker.record_failure("mcp")
        self._emit(
            state,
            EventType.TOOL_COMPLETED.value,
            {"tool_name": tool_name, "status": "failed", "error": _safe_error(last_err)},
        )
        return None

    # -- A2A helpers ------------------------------------------------------
    def _delegate(self, state: RunState) -> dict[str, Any] | None:
        """Delegate to observability specialist; None signals degraded fallback."""
        try:
            self.breaker.guard("a2a")
        except CircuitOpenError:
            state.a2a_fallback = True
            state.unresolved.append("A2A circuit open; degraded to orchestrator-only synthesis.")
            return None
        self._check_cancel(state)
        self._spend_delegation(state)
        service = str(state.context.get("service", "checkout-api"))
        task_id = f"{state.run_id}-obs-1"
        deadline = utcnow().timestamp() + A2A_TIMEOUT_SECONDS
        request = {
            "schema_version": "1.0",
            "task_id": task_id,
            "run_id": state.run_id,
            "tenant_id": state.tenant_id,
            "skill_id": "correlate-service-symptoms",
            "agent_name": "observability-agent",
            "objective": f"Correlate symptoms for {service}",
            "inputs": {
                "service": service,
                "window": {"start": "2026-09-11T11:00:00Z", "end": "2026-09-11T12:00:00Z"},
                "metrics_refs": [
                    e.get("ref", "")
                    for e in state.mcp_evidence
                    if "telemetry://m" in str(e.get("ref", ""))
                ]
                or [f"telemetry://m/{service}/err"],
                "logs_refs": [
                    e.get("ref", "")
                    for e in state.mcp_evidence
                    if "telemetry://l" in str(e.get("ref", ""))
                ],
                "deploy_state": _deploy_state_text(state),
            },
            "deadline": datetime.fromtimestamp(deadline, tz=UTC).isoformat(),
            "requester": "orchestrator",
            "trace_id": state.trace_id,
            "correlation_id": state.correlation_id,
        }
        self._emit(
            state,
            EventType.AGENT_DELEGATED.value,
            {
                "agent": "observability-agent",
                "skill_id": "correlate-service-symptoms",
                "task_id": task_id,
            },
        )
        try:
            result = self.deps.a2a.delegate(request, timeout_s=A2A_TIMEOUT_SECONDS)
        except (TimeoutError, ConnectionError) as exc:
            self.breaker.record_failure("a2a")
            state.a2a_fallback = True
            state.unresolved.append(
                f"Specialist delegation timed out ({_safe_error(exc)}); confidence reduced."
            )
            return None
        except RunCancelledError:
            raise
        except Exception as exc:
            self.breaker.record_failure("a2a")
            state.a2a_fallback = True
            state.unresolved.append(
                f"Specialist delegation failed ({_safe_error(exc)}); confidence reduced."
            )
            return None
        status = str(result.get("status", ""))
        if status != "completed":
            self.breaker.record_failure("a2a")
            state.a2a_fallback = True
            err = result.get("error", {})
            state.unresolved.append(
                f"Specialist returned {status}: {err.get('message', 'unknown')}; confidence reduced."
            )
            return None
        output = result.get("output")
        if not _valid_a2a_output(output):
            self.breaker.record_failure("a2a")
            state.a2a_fallback = True
            state.unresolved.append(
                "Specialist output failed schema validation; treated as untrusted and ignored."
            )
            return None
        self.breaker.record_success("a2a")
        return result

    # -- main driver ------------------------------------------------------
    def run(self, state: RunState) -> RunState:
        """Execute until terminal (completed/failed/cancelled) or approval pause."""
        if state.status == "created":
            state.status = "running"
            self._transition(
                state, "classify", EventType.RUN_STARTED.value, {"objective": state.objective}
            )
        try:
            while True:
                self._check_cancel(state)
                self._check_budgets(state)
                cur = state.current_state
                if cur == "classify":
                    if self._do_classify(state):
                        return state  # clarify terminal
                elif cur == "plan":
                    self._do_plan(state)
                elif cur == "resources":
                    self._do_resources(state)
                elif cur == "mcp_reads":
                    self._do_mcp_reads(state)
                elif cur == "a2a_delegate":
                    self._do_delegate(state)
                elif cur == "correlate":
                    self._do_correlate(state)
                elif cur == "findings":
                    if self._do_findings(state):
                        return state  # more_info terminal
                elif cur == "propose":
                    self._do_propose(state)
                elif cur == "policy":
                    if self._do_policy(state):
                        return state  # denied terminal
                elif cur == "approval_pause":
                    self._do_approval_pause(state)
                    return state  # paused; external resume required
                elif cur == "execute_approved":
                    self._do_execute(state)
                elif cur == "verify":
                    self._do_verify(state)
                elif cur == "complete":
                    self._do_complete(state)
                    return state
                else:
                    raise RuntimeError(f"unknown workflow state {cur!r}")
        except RunCancelledError:
            state.status = "cancelled"
            state.outcome = "cancelled"
            self._emit(state, EventType.RUN_CANCELLED.value, {"reason": "cancel requested"})
            self.deps.store.save(state)
            try:
                cancel = getattr(self.deps.a2a, "cancel", None)
                if callable(cancel):
                    cancel(f"{state.run_id}-obs-1")
            except Exception:
                pass
            return state
        except BudgetExceededError as exc:
            state.status = "failed"
            state.outcome = "failed"
            state.error = {
                "code": ErrorCode.BUDGET_EXCEEDED.value,
                "message": str(exc),
                "retryable": False,
            }
            self._emit(state, EventType.RUN_FAILED.value, {"error": state.error})
            self.deps.store.save(state)
            return state
        except Exception as exc:  # bounded, audited degradation
            state.status = "failed"
            state.outcome = "failed"
            state.error = {
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": _safe_error(exc),
                "retryable": False,
            }
            try:
                self._emit(state, EventType.RUN_FAILED.value, {"error": state.error})
                self.deps.store.save(state)
            except Exception:
                pass
            return state

    def resume_after_approval(
        self, state: RunState, *, decision: str, approver: str = "lead", reason: str | None = None
    ) -> RunState:
        """Resume a paused run after a human decision (approved|rejected|expired)."""
        if state.current_state != "approval_pause" or state.status != "waiting_for_approval":
            raise ValueError("run is not awaiting approval")
        decision = decision.lower()
        if decision not in ("approved", "rejected", "expired"):
            raise ValueError("decision must be approved|rejected|expired")
        if decision == "approved":
            for ap in state.approvals:
                ap["decision"] = "approved"
                ap["approver"] = approver
                ap["reason"] = reason
            self._emit(
                state,
                EventType.APPROVAL_RECEIVED.value,
                {"decision": "approved", "approver": approver},
            )
            self.deps.store.save(state)
            state.current_state = "execute_approved"
            return self.run(state)
        # rejected / expired -> complete-with-rejection, never execute
        for ap in state.approvals:
            ap["decision"] = "rejected" if decision == "rejected" else "expired"
            ap["approver"] = approver
            ap["reason"] = reason
        state.outcome = "rejected"
        state.status = "completed"
        self._emit(
            state, EventType.APPROVAL_RECEIVED.value, {"decision": decision, "approver": approver}
        )
        self._transition(
            state,
            "complete",
            EventType.RUN_COMPLETED.value,
            {
                "outcome": "rejected",
                "executed": False,
                "note": f"approval {decision}; no actions executed",
            },
        )
        return state

    # -- steps ------------------------------------------------------------
    def _do_classify(self, state: RunState) -> bool:
        """Returns True if clarify-terminal."""
        self._spend_model(state)
        classification = self.deps.model.classify(state.objective, state.context)
        state.classification = dict(classification)
        self._transition(
            state, "plan", EventType.MESSAGE_DELTA.value, {"classification": state.classification}
        )
        if bool(classification.get("needs_clarification")):
            state.outcome = "clarify"
            state.status = "completed"
            q = str(classification.get("reason", "insufficient context"))
            state.unresolved.append(f"Clarification needed: {q}")
            self._emit(
                state,
                EventType.MESSAGE_DELTA.value,
                {"clarification_question": f"Please clarify: {q}. Include service name."},
            )
            self._transition(
                state,
                "complete",
                EventType.RUN_COMPLETED.value,
                {"outcome": "clarify", "unresolved": state.unresolved},
            )
            return True
        return False

    def _do_plan(self, state: RunState) -> None:
        self._spend_model(state)
        state.plan = list(self.deps.model.plan(state.objective, state.classification))
        self._transition(state, "resources", EventType.MESSAGE_DELTA.value, {"plan": state.plan})

    def _do_resources(self, state: RunState) -> None:
        service = str(state.context.get("service", "checkout-api"))
        res = {
            "service": service,
            "uri": f"resource://services/{service}",
            "slo": "99.9%",
            "owner": "sre",
        }
        state.resources = res
        self._transition(
            state,
            "mcp_reads",
            EventType.TOOL_STARTED.value,
            {"tool_name": "resource.read", "uri": res["uri"]},
        )

    def _do_mcp_reads(self, state: RunState) -> None:
        service = str(state.context.get("service", "checkout-api"))
        for tool, args in (
            ("telemetry.query_metrics", {"service": service}),
            ("telemetry.query_logs", {"service": service}),
            ("service.get_health", {"service": service}),
            ("deployment.get_current_release", {"service": service}),
        ):
            out = self._mcp_read(state, tool, args)
            if out is not None:
                state.mcp_evidence.append({"tool": tool, **_jsonable(out)})
        if not state.mcp_evidence:
            state.unresolved.append(
                "All primary MCP reads failed; proceeding with degraded evidence."
            )
        self._transition(
            state,
            "a2a_delegate",
            EventType.TOOL_COMPLETED.value,
            {"evidence_count": len(state.mcp_evidence)},
        )

    def _do_delegate(self, state: RunState) -> None:
        result = self._delegate(state)
        state.a2a_result = _jsonable(result) if result is not None else None
        self._transition(
            state,
            "correlate",
            EventType.MESSAGE_DELTA.value,
            {"delegated": result is not None, "fallback": state.a2a_fallback},
        )

    def _do_correlate(self, state: RunState) -> None:
        findings_raw: list[dict[str, Any]] = []
        unresolved_extra: list[str] = []
        if state.a2a_result and isinstance(state.a2a_result.get("output"), dict):
            out = state.a2a_result["output"]
            findings_raw = list(out.get("findings", []))
            unresolved_extra = list(out.get("unresolved_questions", []))
        # Conflict check vs primary MCP evidence (never silently choose one).
        health = next((e for e in state.mcp_evidence if e.get("tool") == "service.get_health"), {})
        if health.get("status") == "healthy" and findings_raw and _avg_conf(findings_raw) > 0.6:
            state.conflict = (
                "Specialist reports anomaly (avg conf > 0.6) but primary "
                "service.get_health reports healthy; both retained, confidence capped."
            )
            state.unresolved.append(state.conflict)
            for f in findings_raw:
                try:
                    f["confidence"] = min(float(f.get("confidence", 0.0)), 0.6)
                except (TypeError, ValueError):
                    f["confidence"] = 0.5
        if state.a2a_fallback:
            for f in findings_raw:
                try:
                    f["confidence"] = round(float(f.get("confidence", 0.0)) - 0.15, 2)
                except (TypeError, ValueError):
                    f["confidence"] = 0.4
        state.unresolved.extend([str(q) for q in unresolved_extra if str(q)])
        state.correlation_note = {  # type: ignore[attr-defined]
            "specialist_findings": len(findings_raw),
            "fallback": state.a2a_fallback,
            "conflict": state.conflict,
        }
        self.deps.store.save(state)
        state.current_state = "findings"
        self._emit(state, EventType.MESSAGE_DELTA.value, {"correlation": state.correlation_note})  # type: ignore[attr-defined]
        self.deps.store.save(state)

    def _do_findings(self, state: RunState) -> bool:
        """Returns True if more_info-terminal (low confidence)."""
        raw: list[dict[str, Any]] = []
        if state.a2a_result and isinstance(state.a2a_result.get("output"), dict):
            raw = list(state.a2a_result["output"].get("findings", []))
        if not raw and state.mcp_evidence:
            # Orchestrator-only synthesis (degraded): derive one finding from MCP.
            raw = [
                {
                    "title": f"Elevated errors in {state.context.get('service', 'checkout-api')}",
                    "summary": "Primary telemetry shows elevated errors; specialist unavailable.",
                    "evidence_refs": [
                        e.get("ref", "telemetry://m/1") for e in state.mcp_evidence[:2]
                    ],
                    "confidence": 0.45,
                }
            ]
            state.unresolved.append("Orchestrator-only synthesis; specialist evidence missing.")
        findings: list[dict[str, Any]] = []
        for i, f in enumerate(raw):
            try:
                title = str(f.get("title", "")).strip()
                summary = str(f.get("summary", "")).strip()
                refs = [str(r) for r in f.get("evidence_refs", []) if str(r)]
                conf = float(f.get("confidence", 0.0))
                if not title or not summary or not refs:
                    state.unresolved.append(f"Finding {i} dropped: missing title/summary/evidence.")
                    continue
                conf = max(0.0, min(1.0, conf))
                sev = "high" if conf >= 0.75 else ("medium" if conf >= 0.5 else "low")
                findings.append(
                    {
                        "schema_version": "1.0",
                        "finding_id": f"{state.run_id}-f{i}",
                        "run_id": state.run_id,
                        "tenant_id": state.tenant_id,
                        "title": title[:256],
                        "summary": summary[:4000],
                        "evidence_refs": refs[:16],
                        "confidence": conf,
                        "severity": sev,
                    }
                )
            except (TypeError, ValueError):
                state.unresolved.append(f"Finding {i} dropped: schema validation failed.")
        state.findings = findings
        for f in findings:
            self._emit(state, EventType.FINDING_CREATED.value, {"finding": f})
        self.deps.store.save(state)
        max_conf = max((f["confidence"] for f in findings), default=0.0)
        if not findings or max_conf < LOW_CONFIDENCE_THRESHOLD:
            state.outcome = "more_info"
            state.status = "completed"
            state.unresolved.append(
                "Confidence below 0.5; more telemetry (metrics_refs/logs_refs/traces) is needed."
            )
            self._emit(
                state,
                EventType.MESSAGE_DELTA.value,
                {"more_info": "Please supply additional telemetry refs for re-analysis."},
            )
            self._transition(
                state,
                "complete",
                EventType.RUN_COMPLETED.value,
                {"outcome": "more_info", "unresolved": state.unresolved},
            )
            return True
        state.current_state = "propose"
        self.deps.store.save(state)
        return False

    def _do_propose(self, state: RunState) -> None:
        actions: list[dict[str, Any]] = []
        service = str(state.context.get("service", "checkout-api"))
        top = max(state.findings, key=lambda f: float(f.get("confidence", 0.0)))
        actions.append(
            build_proposed_action(
                run_id=state.run_id,
                tenant_id=state.tenant_id,
                tool_name="ticket.create",
                reason=f"Track investigation: {top['title']}",
                tool_input={
                    "title": f"Investigate {service}",
                    "severity": top.get("severity", "medium"),
                },
                risk="low",
                rollback=None,
                dry_run=False,
            )
        )
        if any("deployment" in str(f.get("title", "")).lower() for f in state.findings):
            actions.append(
                build_proposed_action(
                    run_id=state.run_id,
                    tenant_id=state.tenant_id,
                    tool_name="deployment.rollback",
                    reason="Recent release correlates with symptoms; prepare dry-run rollback",
                    tool_input={"service": service, "dry_run": True},
                    risk="medium",
                    rollback="re-deploy release 2026.09.10",
                    dry_run=True,
                )
            )
        state.proposed_actions = actions
        self._transition(
            state,
            "policy",
            EventType.MESSAGE_DELTA.value,
            {"proposed": [a["action_id"] for a in actions]},
        )

    def _do_policy(self, state: RunState) -> bool:
        """Returns True if denied-terminal."""
        env = str(state.context.get("env", "dev"))
        decisions: list[dict[str, Any]] = []
        for action in state.proposed_actions:
            tool = str(action["tool_name"])
            raw = self.deps.policy.evaluate(action, env=env)
            decision: dict[str, Any] = {
                "action_id": action.get("action_id"),
                "tool_name": tool,
            }
            for k in ("allowed", "requires_approval", "reason"):
                if k in raw:
                    decision[k] = raw[k]
            decisions.append(decision)
        state.policy_decisions = decisions
        self.deps.store.save(state)
        denied = [d for d in decisions if not d.get("allowed", False)]
        if denied:
            state.outcome = "denied"
            state.status = "completed"
            reasons = "; ".join(str(d.get("reason", "denied")) for d in denied)
            self._emit(
                state,
                EventType.MESSAGE_DELTA.value,
                {
                    "policy_denial": f"Policy denied {len(denied)} action(s): {reasons}. Nothing executed."
                },
            )
            self._transition(
                state,
                "complete",
                EventType.RUN_COMPLETED.value,
                {"outcome": "denied", "denied": denied},
            )
            return True
        state.current_state = "approval_pause"
        self.deps.store.save(state)
        return False

    def _do_approval_pause(self, state: RunState) -> None:
        if not state.proposed_actions:
            state.current_state = "verify"  # nothing consequential; skip gate
            self.deps.store.save(state)
            return
        state.status = "waiting_for_approval"
        now = utcnow().isoformat()
        for action in state.proposed_actions:
            state.approvals.append(
                {
                    "schema_version": "1.0",
                    "approval_id": f"{state.run_id}-{action['action_id']}-appr",
                    "run_id": state.run_id,
                    "tenant_id": state.tenant_id,
                    "action_id": action["action_id"],
                    "requested_by": "orchestrator",
                    "decision": "pending",
                    "requested_at": now,
                }
            )
        self._emit(
            state,
            EventType.APPROVAL_REQUIRED.value,
            {"approvals": state.approvals, "actions": state.proposed_actions},
        )
        self.deps.store.save(state)

    def _do_execute(self, state: RunState) -> None:
        approved_ids = {a["action_id"] for a in state.approvals if a.get("decision") == "approved"}
        if state.proposed_actions and not approved_ids:
            raise RuntimeError("execute_approved without any approved approval row")
        for action in state.proposed_actions:
            if action["action_id"] not in approved_ids:
                continue
            tool = str(action["tool_name"])
            spec = validate_tool_call(tool, dict(action.get("input", {})))
            if spec.category == "destructive":
                raise RuntimeError(f"refusing to execute destructive tool {tool}")
            self._check_cancel(state)
            self._spend_tool(state)  # billed; never auto-retried below
            self._emit(
                state,
                EventType.TOOL_STARTED.value,
                {
                    "tool_name": tool,
                    "action_id": action["action_id"],
                    "dry_run": bool(action.get("dry_run", False)),
                },
            )
            try:
                out = self.deps.mcp.invoke(
                    tool, dict(action.get("input", {})), timeout_s=mcp_timeout_for(tool)
                )
                _reject_malformed(tool, out)
                self._emit(
                    state,
                    EventType.TOOL_COMPLETED.value,
                    {"tool_name": tool, "action_id": action["action_id"], "status": "succeeded"},
                )
            except Exception as exc:  # no auto-retry for writes
                self._emit(
                    state,
                    EventType.TOOL_COMPLETED.value,
                    {
                        "tool_name": tool,
                        "action_id": action["action_id"],
                        "status": "failed",
                        "error": _safe_error(exc),
                    },
                )
                state.unresolved.append(f"Action {action['action_id']} failed: {_safe_error(exc)}")
        state.current_state = "verify"
        state.status = "running"
        self.deps.store.save(state)

    def _do_verify(self, state: RunState) -> None:
        service = str(state.context.get("service", "checkout-api"))
        out = self._mcp_read(state, "service.get_health", {"service": service})
        verified = bool(out and out.get("status") in ("healthy", "degraded"))
        self._transition(state, "complete", EventType.MESSAGE_DELTA.value, {"verified": verified})

    def _do_complete(self, state: RunState) -> None:
        state.outcome = state.outcome or "completed"
        state.status = "completed"
        max_conf = max((float(f.get("confidence", 0.0)) for f in state.findings), default=0.0)
        result = {
            "schema_version": "1.0",
            "run_id": state.run_id,
            "tenant_id": state.tenant_id,
            "situation": state.objective,
            "evidence": state.mcp_evidence,
            "findings": state.findings,
            "confidence": max_conf,
            "proposed_actions": state.proposed_actions,
            "unresolved_questions": state.unresolved,
            "outcome": state.outcome,
        }
        state.result = result  # type: ignore[attr-defined]
        self._emit(state, EventType.RUN_COMPLETED.value, {"result": result})
        self.deps.store.save(state)


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for k, v in args.items():
        kl = k.lower()
        if any(s in kl for s in ("secret", "token", "password", "api_key", "apikey", "bearer")):
            redacted[k] = "[redacted]"
        else:
            redacted[k] = v
    return redacted


def _safe_error(exc: BaseException | None) -> str:
    if exc is None:
        return "unknown error"
    msg = str(exc)[:500] or exc.__class__.__name__
    return re.sub(
        r"(?i)(bearer\s+\S+|api_key\s*=\s*\S+|client_secret\s*=\s*\S+)", "[redacted]", msg
    )


def _jsonable(obj: Any) -> Any:
    import json

    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return {"unserializable": True}


def _reject_malformed(tool_name: str, out: Any) -> None:
    if not isinstance(out, dict):
        raise ValueError(f"tool {tool_name} returned malformed (non-dict) result")
    if len(out) > 128:
        raise ValueError(f"tool {tool_name} returned oversize result")
    import json

    if len(json.dumps(out, default=str)) > 65536:
        raise ValueError(f"tool {tool_name} returned oversize result")


def _valid_a2a_output(output: Any) -> bool:
    if not isinstance(output, dict):
        return False
    findings = output.get("findings")
    if not isinstance(findings, list):
        return False
    for f in findings:
        if not isinstance(f, dict):
            return False
        if not f.get("title") or not f.get("summary"):
            return False
        refs = f.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            return False
        try:
            c = float(f.get("confidence", -1))
        except (TypeError, ValueError):
            return False
        if not (0.0 <= c <= 1.0):
            return False
    return True


def _avg_conf(findings: list[dict[str, Any]]) -> float:
    if not findings:
        return 0.0
    vals: list[float] = []
    for f in findings:
        try:
            vals.append(float(f.get("confidence", 0.0)))
        except (TypeError, ValueError):
            continue
    return sum(vals) / len(vals) if vals else 0.0


def _deploy_state_text(state: RunState) -> str | None:
    rel = next(
        (e for e in state.mcp_evidence if e.get("tool") == "deployment.get_current_release"), None
    )
    if rel and rel.get("release"):
        return f"release {rel['release']} deployed"
    return None


def request_cancel(store: InMemoryPersistence, run_id: str) -> bool:  # worker helper
    for saved in reversed(store.saves):
        if saved.run_id == run_id:
            saved.cancelled = True
            return True
    return False


__all__ = [
    "DELEGATION_COST_USD",
    "LOW_CONFIDENCE_THRESHOLD",
    "MCP_TIMEOUTS",
    "MCP_TIMEOUT_SECONDS",
    "MODEL_COST_USD",
    "READ_TOOLS",
    "STATES",
    "TOOL_COST_USD",
    "TOOL_NAME_RE",
    "TOOL_REGISTRY",
    "TOOL_TIMEOUTS",
    "BudgetExceededError",
    "CircuitBreaker",
    "CircuitOpenError",
    "FakeA2AClient",
    "FakeMCPClient",
    "FakeModelClient",
    "FakePolicyClient",
    "GraphDeps",
    "InMemoryPersistence",
    "OrchestratorGraph",
    "RunCancelledError",
    "SystemClock",
    "build_proposed_action",
    "describe",
    "mcp_timeout_for",
    "request_cancel",
    "validate_tool_call",
]
