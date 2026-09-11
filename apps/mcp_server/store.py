"""In-memory audit log + idempotency store (PG-backed in later prompts)."""

from __future__ import annotations

from packages.contracts.tools import ToolInvocation

AUDIT_LOG: list[ToolInvocation] = []

# key -> {"input_hash": str, "result": dict}
IDEMPOTENCY_STORE: dict[str, dict[str, object]] = {}

# tool_name -> artificial delay in seconds (tests use this for timeout paths).
INJECTED_DELAYS: dict[str, float] = {}


def reset_state() -> None:
    AUDIT_LOG.clear()
    IDEMPOTENCY_STORE.clear()
    INJECTED_DELAYS.clear()


__all__ = ["AUDIT_LOG", "IDEMPOTENCY_STORE", "INJECTED_DELAYS", "reset_state"]
