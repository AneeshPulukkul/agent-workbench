"""Agent runtime abstraction (Spec §2.3/§5 + ADR-001/ADR-002).

Orchestrator depends on ``AgentRuntime`` Protocol only — never on a
provider/framework SDK directly. Implementations:

- ``FakeRuntime``: deterministic, offline, default in Compose/tests.
- ``ProviderRuntime``: LiteLLM-backed (lazy import; real providers only
  via config). Supports task routing + structured-output validation.

Cross-cutting (both runtimes):
- ``ModelRouter``: fast / reasoning / summary model selection.
- ``BudgetTracker``: model/tool/token/cost/duration/delegation-depth caps.
- Structured-output validation via Pydantic (never trust raw model text).
- Timeout + bounded retry for model (read) calls; writes are NEVER retried.
- Cooperative cancellation via ``asyncio.Event``.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, Field, ValidationError

from packages.contracts.run import AgentResult, Finding

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ModelError(RuntimeError):
    """Retryable-agnostic model failure (maps to ErrorCode.model_error)."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class ModelTimeoutError(ModelError):
    def __init__(self, message: str = "model call timed out") -> None:
        super().__init__(message, retryable=True)


class ModelValidationError(ModelError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class BudgetExceeded(RuntimeError):
    """Maps to ErrorCode.budget_exceeded (retryable=False)."""

    def __init__(self, message: str, *, budget: str = "") -> None:
        super().__init__(message)
        self.budget = budget
        self.retryable = False


# ---------------------------------------------------------------------------
# Tasks / routing
# ---------------------------------------------------------------------------


class ModelTask(StrEnum):
    FAST = "fast"  # classification, tool selection
    REASONING = "reasoning"  # diagnosis, planning
    SUMMARY = "summary"  # small summarization


@dataclass(frozen=True)
class ModelRouter:
    """Task-based model selection. No provider SDK imported here."""

    fast_model: str = "gpt-4o-mini"
    reasoning_model: str = "gpt-4o"
    summary_model: str = "gpt-4o-mini"

    def route(self, task: ModelTask | str) -> str:
        t = ModelTask(str(task))
        if t == ModelTask.FAST:
            return self.fast_model
        if t == ModelTask.SUMMARY:
            return self.summary_model
        return self.reasoning_model

    @classmethod
    def from_env(cls) -> ModelRouter:
        return cls(
            fast_model=os.getenv("FAST_MODEL", "gpt-4o-mini"),
            reasoning_model=os.getenv("REASONING_MODEL", "gpt-4o"),
            summary_model=os.getenv("SUMMARY_MODEL", "gpt-4o-mini"),
        )


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetLimits:
    max_model_calls: int = 10
    max_tool_calls: int = 20
    max_input_tokens: int = 60_000
    max_output_tokens: int = 20_000
    max_cost_usd: float = 2.0
    max_duration_s: float = 600.0
    max_delegation_depth: int = 3


@dataclass
class BudgetSnapshot:
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    elapsed_s: float = 0.0
    delegation_depth: int = 0


class BudgetTracker:
    """In-memory per-run budget ledger. Backed/seeded from Run.budget."""

    def __init__(self, limits: BudgetLimits | None = None) -> None:
        self.limits = limits or BudgetLimits()
        self._snap = BudgetSnapshot()
        self._start = time.monotonic()
        self._lock = asyncio.Lock()

    # -- read -----------------------------------------------------------
    def snapshot(self) -> BudgetSnapshot:
        s = BudgetSnapshot(
            model_calls=self._snap.model_calls,
            tool_calls=self._snap.tool_calls,
            input_tokens=self._snap.input_tokens,
            output_tokens=self._snap.output_tokens,
            cost_usd=self._snap.cost_usd,
            elapsed_s=time.monotonic() - self._start,
            delegation_depth=self._snap.delegation_depth,
        )
        return s

    def _check(self, s: BudgetSnapshot) -> None:
        lim = self.limits
        if s.model_calls > lim.max_model_calls:
            raise BudgetExceeded(
                f"model calls exceeded ({lim.max_model_calls})", budget="max_model_calls"
            )
        if s.tool_calls > lim.max_tool_calls:
            raise BudgetExceeded(
                f"tool calls exceeded ({lim.max_tool_calls})", budget="max_tool_calls"
            )
        if s.input_tokens > lim.max_input_tokens:
            raise BudgetExceeded("input token budget exceeded", budget="max_input_tokens")
        if s.output_tokens > lim.max_output_tokens:
            raise BudgetExceeded("output token budget exceeded", budget="max_output_tokens")
        if s.cost_usd > lim.max_cost_usd:
            raise BudgetExceeded(f"cost exceeded (${lim.max_cost_usd})", budget="max_cost_usd")
        if s.elapsed_s > lim.max_duration_s:
            raise BudgetExceeded("run duration exceeded", budget="max_duration_s")
        if s.delegation_depth > lim.max_delegation_depth:
            raise BudgetExceeded("delegation depth exceeded", budget="max_delegation_depth")

    def check(self) -> None:
        self._check(self.snapshot())

    # -- consume ----------------------------------------------------------
    # NOTE: sync fast-paths (no await) so graph code can call them inline.
    # An async variant with lock is available for concurrent fan-out.

    def record_model(
        self, *, input_tokens: int = 0, output_tokens: int = 0, cost_usd: float = 0.0
    ) -> BudgetSnapshot:
        self._snap.model_calls += 1
        self._snap.input_tokens += max(0, input_tokens)
        self._snap.output_tokens += max(0, output_tokens)
        self._snap.cost_usd += max(0.0, cost_usd)
        snap = self.snapshot()
        self._check(snap)
        return snap

    def record_tool(self, count: int = 1) -> BudgetSnapshot:
        self._snap.tool_calls += count
        snap = self.snapshot()
        self._check(snap)
        return snap

    def record_delegation(self, depth: int = 1) -> BudgetSnapshot:
        self._snap.delegation_depth += depth
        snap = self.snapshot()
        self._check(snap)
        return snap

    async def arecord_model(self, **kwargs: Any) -> BudgetSnapshot:
        async with self._lock:
            return self.record_model(**kwargs)

    @classmethod
    def from_budget_dict(cls, budget: dict[str, Any], **overrides: Any) -> BudgetTracker:
        lim = BudgetLimits(
            max_model_calls=int(budget.get("max_model_calls", 10)),
            max_tool_calls=int(budget.get("max_tool_calls", 20)),
            max_cost_usd=float(budget.get("max_cost_usd", 2.0)),
            max_input_tokens=int(overrides.get("max_input_tokens", 60_000)),
            max_output_tokens=int(overrides.get("max_output_tokens", 20_000)),
            max_duration_s=float(overrides.get("max_duration_s", 600.0)),
            max_delegation_depth=int(overrides.get("max_delegation_depth", 3)),
        )
        t = cls(lim)
        t._snap.model_calls = int(budget.get("consumed_model_calls", 0))
        t._snap.tool_calls = int(budget.get("consumed_tool_calls", 0))
        t._snap.cost_usd = float(budget.get("consumed_cost_usd", 0.0))
        return t


# ---------------------------------------------------------------------------
# Structured outputs
# ---------------------------------------------------------------------------

T = TypeVar("T", bound=BaseModel)


class Classification(BaseModel):
    model_config = {"extra": "forbid"}

    category: str = Field(min_length=1, max_length=128)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(default="", max_length=2000)


class Plan(BaseModel):
    model_config = {"extra": "forbid"}

    steps: list[str] = Field(default_factory=list, max_length=16)
    risks: list[str] = Field(default_factory=list, max_length=16)


def validate_structured[T: BaseModel](data: dict[str, Any], model_cls: type[T]) -> T:
    """Validate raw model JSON against a Pydantic model. Never coerce raw
    model text into an executable command — validation failure raises."""
    try:
        return model_cls.model_validate(data)
    except ValidationError as e:
        raise ModelValidationError(f"structured validation failed: {e}") from e


# ---------------------------------------------------------------------------
# Timeout / retry / cancellation helpers
# ---------------------------------------------------------------------------

# Tool categories that must NEVER be auto-retried (irreversible side effects).
NO_RETRY_TOOL_CATEGORIES = frozenset({"write", "destructive"})


def retry_allowed_for_tool(category: str) -> bool:
    return category not in NO_RETRY_TOOL_CATEGORIES


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (BudgetExceeded, ModelValidationError, asyncio.CancelledError)):
        return False
    if isinstance(exc, ModelError):
        return exc.retryable
    name = type(exc).__name__
    # litellm / httpx retryable shapes (matched by name to avoid hard dep).
    if name in {
        "RateLimitError",
        "APIConnectionError",
        "InternalServerError",
        "ServiceUnavailableError",
        "Timeout",
        "ConnectError",
        "ReadTimeout",
    }:
        return True
    return bool(isinstance(exc, (TimeoutError, ConnectionError)))


def check_cancelled(cancellation: asyncio.Event | None) -> None:
    if cancellation is not None and cancellation.is_set():
        raise asyncio.CancelledError("run cancelled")


async def run_with_timeout(coro, timeout_s: float):
    try:
        return await asyncio.wait_for(asyncio.ensure_future(coro), timeout_s)
    except TimeoutError as e:
        raise ModelTimeoutError() from e


async def call_with_retry(
    fn,
    *,
    max_retries: int = 2,
    timeout_s: float = 30.0,
    is_write: bool = False,
    cancellation: asyncio.Event | None = None,
    base_backoff_s: float = 0.2,
    cap_backoff_s: float = 4.0,
):
    """Bounded retry for MODEL (read) calls. Writes are never retried:
    ``is_write=True`` forces a single attempt."""
    if is_write:
        max_retries = 0
    last: BaseException | None = None
    for attempt in range(max_retries + 1):
        check_cancelled(cancellation)
        try:
            return await run_with_timeout(fn(), timeout_s)
        except Exception as e:
            last = e
            if isinstance(e, asyncio.CancelledError):
                raise
            if is_write or not _is_retryable(e) or attempt == max_retries:
                raise
            await asyncio.sleep(min(base_backoff_s * (2**attempt), cap_backoff_s))
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# Model I/O shapes
# ---------------------------------------------------------------------------


@dataclass
class ModelRequest:
    messages: list[dict[str, Any]]
    task: ModelTask = ModelTask.REASONING
    response_model: type[BaseModel] | None = None
    max_tokens: int = 1024
    timeout_s: float = 30.0
    run_id: str | None = None
    tenant_id: str | None = None
    trace_id: str | None = None


@dataclass
class ModelResponse:
    text: str
    structured: BaseModel | None = None
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


# ---------------------------------------------------------------------------
# AgentRuntime Protocol
# ---------------------------------------------------------------------------


class AgentRuntime(Protocol):
    """Framework-replaceable runtime (ADR-001). LangGraph is the default
    managed implementation; Fake/Provider satisfy this Protocol."""

    async def classify(
        self,
        objective: str,
        *,
        context: dict[str, Any] | None = None,
        cancellation: asyncio.Event | None = None,
    ) -> Classification: ...

    async def plan(
        self,
        objective: str,
        *,
        context: dict[str, Any] | None = None,
        cancellation: asyncio.Event | None = None,
    ) -> Plan: ...

    async def synthesize(
        self,
        objective: str,
        *,
        findings: list[Finding] | None = None,
        context: dict[str, Any] | None = None,
        run_id: str | None = None,
        tenant_id: str | None = None,
        cancellation: asyncio.Event | None = None,
    ) -> AgentResult: ...

    async def summarize(self, text: str, *, cancellation: asyncio.Event | None = None) -> str: ...


# ---------------------------------------------------------------------------
# FakeRuntime (deterministic)
# ---------------------------------------------------------------------------


def _deterministic_seed(*parts: str) -> str:
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return h


class FakeRuntime:
    """Deterministic offline runtime for tests/local (LLM_MODE=fake).

    Outputs are pure functions of the inputs — no network, no randomness.
    Every call consumes ``BudgetTracker`` and validates structured output.
    """

    def __init__(
        self,
        tracker: BudgetTracker | None = None,
        router: ModelRouter | None = None,
        latency_s: float = 0.0,
    ) -> None:
        self.tracker = tracker or BudgetTracker()
        self.router = router or ModelRouter()
        self.latency_s = latency_s

    async def _tick(
        self, task: ModelTask, text: str, cancellation: asyncio.Event | None = None
    ) -> None:
        check_cancelled(cancellation)
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        check_cancelled(cancellation)
        # Deterministic fake metering: 1 token / ~4 chars, $1e-6 per token.
        toks = max(1, len(text) // 4)
        self.tracker.record_model(
            input_tokens=toks, output_tokens=toks // 2 + 1, cost_usd=toks * 1e-6
        )
        self.tracker.check()

    async def classify(
        self,
        objective: str,
        *,
        context: dict[str, Any] | None = None,
        cancellation: asyncio.Event | None = None,
    ) -> Classification:
        await self._tick(ModelTask.FAST, objective, cancellation)
        low = objective.lower()
        if any(k in low for k in ("error", "latency", "outage", "incident", "alert")):
            cat, conf = "incident", 0.9
        elif any(k in low for k in ("deploy", "release", "rollback", "change")):
            cat, conf = "change-risk", 0.85
        elif any(k in low for k in ("cost", "spend", "billing")):
            cat, conf = "cost", 0.7
        else:
            cat, conf = "general", 0.6
        data = {
            "category": cat,
            "confidence": conf,
            "reasoning": f"fake classification (seed={_deterministic_seed(objective)[:8]})",
        }
        return validate_structured(data, Classification)

    async def plan(
        self,
        objective: str,
        *,
        context: dict[str, Any] | None = None,
        cancellation: asyncio.Event | None = None,
    ) -> Plan:
        await self._tick(ModelTask.REASONING, objective, cancellation)
        seed = _deterministic_seed(objective)
        steps = [
            "retrieve context (services, runbooks)",
            "read-only diagnostics (metrics/logs/traces)",
            "delegate to specialist (observability) if needed",
            "correlate evidence and draft findings",
        ]
        if int(seed[:2], 16) % 2 == 0:
            steps.append("prepare change-review with rollback")
        data = {"steps": steps, "risks": ["incomplete telemetry", "conflicting evidence"]}
        return validate_structured(data, Plan)

    async def synthesize(
        self,
        objective: str,
        *,
        findings: list[Finding] | None = None,
        context: dict[str, Any] | None = None,
        run_id: str | None = None,
        tenant_id: str | None = None,
        cancellation: asyncio.Event | None = None,
    ) -> AgentResult:
        await self._tick(ModelTask.REASONING, objective, cancellation)
        findings = findings or []
        data = {
            "answer": f"Fake synthesis for: {objective[:200]}",
            "findings": [f.model_dump() for f in findings],
            "proposed_actions": [],
            "unresolved_questions": [],
            "run_id": run_id,
            "tenant_id": tenant_id,
        }
        # Drop Nones so AgentResult validation stays strict-clean.
        data = {k: v for k, v in data.items() if v is not None}
        return validate_structured(data, AgentResult)

    async def summarize(self, text: str, *, cancellation: asyncio.Event | None = None) -> str:
        await self._tick(ModelTask.SUMMARY, text, cancellation)
        return text[:500]


# ---------------------------------------------------------------------------
# ProviderRuntime (LiteLLM)
# ---------------------------------------------------------------------------


@dataclass
class ProviderRuntime:
    """LiteLLM-backed runtime. ``litellm`` is imported lazily so unit tests
    and offline environments can import this module without the dep."""

    router: ModelRouter = field(default_factory=ModelRouter.from_env)
    tracker: BudgetTracker = field(default_factory=BudgetTracker)
    max_retries: int = 2
    timeout_s: float = 30.0
    cost_per_1k_input: float = 0.001
    cost_per_1k_output: float = 0.002

    # -- low-level model call -------------------------------------------------
    async def complete(
        self, request: ModelRequest, cancellation: asyncio.Event | None = None
    ) -> ModelResponse:
        check_cancelled(cancellation)
        self.tracker.check()
        model = self.router.route(request.task)
        started = time.monotonic()

        async def _call() -> Any:
            try:
                import litellm  # lazy: real providers only via config
            except ImportError as e:
                raise ModelError(
                    "litellm is not installed; set LLM_MODE=fake or install litellm",
                    retryable=False,
                ) from e
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": request.messages,
                "max_tokens": request.max_tokens,
            }
            if request.response_model is not None:
                # Ask for JSON; validate strictly client-side afterwards.
                kwargs["response_format"] = {"type": "json_object"}
            return await litellm.acompletion(**kwargs)

        raw = await call_with_retry(
            _call,
            max_retries=self.max_retries,
            timeout_s=request.timeout_s or self.timeout_s,
            is_write=False,
            cancellation=cancellation,
        )
        check_cancelled(cancellation)
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            choice = raw["choices"][0]["message"]["content"] or ""
            usage = raw.get("usage") or {}
        except (KeyError, IndexError, TypeError) as e:
            raise ModelValidationError(f"unexpected provider payload: {e}") from e
        in_toks = int(usage.get("prompt_tokens", max(1, len(str(choice)) // 4)))
        out_toks = int(usage.get("completion_tokens", max(1, len(str(choice)) // 4)))
        cost = (in_toks / 1000) * self.cost_per_1k_input + (
            out_toks / 1000
        ) * self.cost_per_1k_output
        self.tracker.record_model(input_tokens=in_toks, output_tokens=out_toks, cost_usd=cost)
        structured: BaseModel | None = None
        if request.response_model is not None:
            import json as _json

            try:
                payload = _json.loads(choice) if isinstance(choice, str) else dict(choice)
            except Exception as e:
                raise ModelValidationError(f"provider did not return JSON: {e}") from e
            structured = validate_structured(payload, request.response_model)
        return ModelResponse(
            text=choice if isinstance(choice, str) else str(choice),
            structured=structured,
            model=model,
            input_tokens=in_toks,
            output_tokens=out_toks,
            cost_usd=cost,
            latency_ms=latency_ms,
        )

    # -- Protocol surface -------------------------------------------------------
    async def classify(
        self,
        objective: str,
        *,
        context: dict[str, Any] | None = None,
        cancellation: asyncio.Event | None = None,
    ) -> Classification:
        resp = await self.complete(
            ModelRequest(
                messages=[
                    {
                        "role": "system",
                        "content": "Classify the operations objective. Reply with JSON "
                        "{category, confidence, reasoning} only.",
                    },
                    {"role": "user", "content": objective[:4000]},
                ],
                task=ModelTask.FAST,
                response_model=Classification,
            ),
            cancellation,
        )
        assert isinstance(resp.structured, Classification)
        return resp.structured

    async def plan(
        self,
        objective: str,
        *,
        context: dict[str, Any] | None = None,
        cancellation: asyncio.Event | None = None,
    ) -> Plan:
        resp = await self.complete(
            ModelRequest(
                messages=[
                    {
                        "role": "system",
                        "content": "Plan a bounded investigation. Reply with JSON "
                        "{steps[], risks[]} only.",
                    },
                    {"role": "user", "content": objective[:4000]},
                ],
                task=ModelTask.REASONING,
                response_model=Plan,
            ),
            cancellation,
        )
        assert isinstance(resp.structured, Plan)
        return resp.structured

    async def synthesize(
        self,
        objective: str,
        *,
        findings: list[Finding] | None = None,
        context: dict[str, Any] | None = None,
        run_id: str | None = None,
        tenant_id: str | None = None,
        cancellation: asyncio.Event | None = None,
    ) -> AgentResult:
        ev = "\n".join(f"- {f.title}: {f.summary}" for f in (findings or []))[:4000]
        resp = await self.complete(
            ModelRequest(
                messages=[
                    {
                        "role": "system",
                        "content": "Synthesize a structured incident review. Reply with JSON "
                        "{answer, findings[], proposed_actions[], "
                        "unresolved_questions[]} only.",
                    },
                    {"role": "user", "content": f"Objective: {objective[:2000]}\nEvidence:\n{ev}"},
                ],
                task=ModelTask.REASONING,
                response_model=AgentResult,
            ),
            cancellation,
        )
        assert isinstance(resp.structured, AgentResult)
        return resp.structured

    async def summarize(self, text: str, *, cancellation: asyncio.Event | None = None) -> str:
        resp = await self.complete(
            ModelRequest(
                messages=[
                    {"role": "system", "content": "Summarize concisely."},
                    {"role": "user", "content": text[:8000]},
                ],
                task=ModelTask.SUMMARY,
            ),
            cancellation,
        )
        return resp.text[:2000]


def runtime_from_env(tracker: BudgetTracker | None = None) -> AgentRuntime:
    """LLM_MODE=fake (default) -> FakeRuntime; otherwise ProviderRuntime."""
    if os.getenv("LLM_MODE", "fake").lower() == "fake":
        return FakeRuntime(tracker=tracker or BudgetTracker())
    return ProviderRuntime(tracker=tracker or BudgetTracker())


__all__ = [
    "NO_RETRY_TOOL_CATEGORIES",
    "AgentRuntime",
    "BudgetExceeded",
    "BudgetLimits",
    "BudgetSnapshot",
    "BudgetTracker",
    "Classification",
    "FakeRuntime",
    "ModelError",
    "ModelRequest",
    "ModelResponse",
    "ModelRouter",
    "ModelTask",
    "ModelTimeoutError",
    "ModelValidationError",
    "Plan",
    "ProviderRuntime",
    "call_with_retry",
    "check_cancelled",
    "retry_allowed_for_tool",
    "run_with_timeout",
    "runtime_from_env",
    "validate_structured",
]
