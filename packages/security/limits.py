"""Input size limits, timeouts, rate limits (Spec §10 + api-contracts.md).

Limits (server-enforced, single source of truth):
- objective <= 8KB, context <= 64KB / <= 64 keys, action input <= 50 keys / 32KB.
- Tool timeouts mirror the MCP catalog: read 15-30s, analysis/dry-run 60s,
  write 60s. Client must set HTTP timeout >= tool timeout + 5s.
- RateLimiter is an in-process token bucket (per-tenant + global). Production
  should front this with ingress rate-limiting; this is the last-mile guard.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

OBJECTIVE_MAX_BYTES = 8 * 1024
CONTEXT_MAX_BYTES = 64 * 1024
CONTEXT_MAX_KEYS = 64
ACTION_INPUT_MAX_KEYS = 50
ACTION_INPUT_MAX_BYTES = 32 * 1024

TOOL_TIMEOUTS: dict[str, int] = {
    "telemetry.query_metrics": 30,
    "telemetry.query_logs": 30,
    "service.get_health": 15,
    "knowledge.search": 15,
    "knowledge.get_runbook": 15,
    "remediation.simulate": 60,
    "deployment.rollback": 60,
    "database.failover": 60,
}
DEFAULT_TOOL_TIMEOUT = 30


class LimitExceededError(Exception):
    def __init__(self, message: str, *, code: str = "validation_error") -> None:
        super().__init__(message)
        self.code = code


class RateLimitError(Exception):
    def __init__(self, message: str = "rate limit exceeded") -> None:
        super().__init__(message)
        self.code = "rate_limited"


def _size(obj: Any) -> int:
    return len(json.dumps(obj, default=str))


def check_objective(objective: str) -> str:
    if len(objective.encode("utf-8")) > OBJECTIVE_MAX_BYTES:
        raise LimitExceededError("objective must be <= 8KB")
    if not objective.strip():
        raise LimitExceededError("objective must be non-empty")
    return objective


def check_context(context: dict[str, Any]) -> dict[str, Any]:
    if len(context) > CONTEXT_MAX_KEYS:
        raise LimitExceededError("context must have <= 64 keys")
    if _size(context) > CONTEXT_MAX_BYTES:
        raise LimitExceededError("context must be <= 64KB serialized")
    return context


def check_action_input(action_input: dict[str, Any]) -> dict[str, Any]:
    if len(action_input) > ACTION_INPUT_MAX_KEYS:
        raise LimitExceededError("action input must have <= 50 keys")
    if _size(action_input) > ACTION_INPUT_MAX_BYTES:
        raise LimitExceededError("action input must be <= 32KB serialized")
    return action_input


@dataclass
class InputLimits:
    objective_max_bytes: int = OBJECTIVE_MAX_BYTES
    context_max_bytes: int = CONTEXT_MAX_BYTES
    context_max_keys: int = CONTEXT_MAX_KEYS
    action_max_keys: int = ACTION_INPUT_MAX_KEYS
    action_max_bytes: int = ACTION_INPUT_MAX_BYTES


@dataclass
class TimeoutConfig:
    tool_timeouts: dict[str, int] = field(default_factory=lambda: dict(TOOL_TIMEOUTS))
    default_seconds: int = DEFAULT_TOOL_TIMEOUT
    client_slack_seconds: int = 5

    def for_tool(self, tool_name: str) -> int:
        return self.tool_timeouts.get(tool_name, self.default_seconds)

    def client_timeout(self, tool_name: str) -> int:
        return self.for_tool(tool_name) + self.client_slack_seconds


@dataclass
class RateLimiter:
    """Token-bucket per key. ``capacity`` burst, ``refill_per_sec`` rate."""

    capacity: int = 60
    refill_per_sec: float = 1.0
    _buckets: dict[str, tuple[float, float]] = field(default_factory=dict, repr=False)

    def allow(self, key: str, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        tokens, updated = self._buckets.get(key, (float(self.capacity), now))
        tokens = min(float(self.capacity), tokens + (now - updated) * self.refill_per_sec)
        if tokens < 1.0:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1.0, now)
        return True

    def check(self, key: str, *, now: float | None = None) -> None:
        if not self.allow(key, now=now):
            raise RateLimitError(f"rate limit exceeded for {key}")


__all__ = [
    "ACTION_INPUT_MAX_BYTES",
    "ACTION_INPUT_MAX_KEYS",
    "CONTEXT_MAX_BYTES",
    "CONTEXT_MAX_KEYS",
    "DEFAULT_TOOL_TIMEOUT",
    "OBJECTIVE_MAX_BYTES",
    "TOOL_TIMEOUTS",
    "InputLimits",
    "LimitExceededError",
    "RateLimitError",
    "RateLimiter",
    "TimeoutConfig",
    "check_action_input",
    "check_context",
    "check_objective",
]
