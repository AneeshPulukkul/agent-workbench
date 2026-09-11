"""Replay tooling: re-drive evaluation/debugging from a persisted event log.

Event schema (minimal): ``{sequence, type, payload}``. Replay is pure:
sort by sequence, dedupe, fold into state, assert ordering invariants.
Matches the persist-then-publish + (run_id, sequence) dedupe contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReplayResult:
    run_id: str
    events_replayed: int
    duplicates_skipped: int
    state: dict[str, Any] = field(default_factory=dict)
    terminal: str | None = None


TERMINAL_TYPES = {"run.completed", "run.failed", "run.cancelled"}


def replay_events(run_id: str, events: list[dict[str, Any]]) -> ReplayResult:
    ordered = sorted(events, key=lambda e: int(e.get("sequence", 0)))
    seen: set[int] = set()
    state: dict[str, Any] = {"findings": [], "approvals": [], "tools": []}
    terminal: str | None = None
    dupes = 0
    for e in ordered:
        seq = int(e.get("sequence", 0))
        if seq in seen:
            dupes += 1
            continue
        seen.add(seq)
        etype = str(e.get("type", ""))
        payload = e.get("payload", {})
        if etype == "finding.created" and isinstance(payload, dict):
            state["findings"].append(payload)
        elif etype in ("approval.required", "approval.received") and isinstance(payload, dict):
            state["approvals"].append(payload)
        elif etype == "tool.called" and isinstance(payload, dict):
            state["tools"].append(payload.get("tool"))
        if etype in TERMINAL_TYPES:
            terminal = etype
    # Ordering invariant: sequences must be dense from min..max (no gaps).
    if seen and set(seen) != set(range(min(seen), max(seen) + 1)):
        raise ValueError("event log has sequence gaps; cannot replay exactly")
    return ReplayResult(
        run_id=run_id,
        events_replayed=len(seen),
        duplicates_skipped=dupes,
        state=state,
        terminal=terminal,
    )


__all__ = ["TERMINAL_TYPES", "ReplayResult", "replay_events"]
