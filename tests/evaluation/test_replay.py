"""Replay tests: ordering, dedupe, resume-after-restart (§12 replay)."""

from __future__ import annotations

import pytest

from tests.evaluation.replay import replay_events


def _events() -> list[dict[str, object]]:
    return [
        {"sequence": 1, "type": "run.created", "payload": {}},
        {"sequence": 2, "type": "tool.called", "payload": {"tool": "service.get_health"}},
        {"sequence": 3, "type": "finding.created", "payload": {"title": "degraded"}},
        {"sequence": 4, "type": "approval.required", "payload": {"action": "deployment.rollback"}},
        {"sequence": 5, "type": "approval.received", "payload": {"decision": "approved"}},
        {"sequence": 6, "type": "run.completed", "payload": {}},
    ]


def test_replay_orders_and_folds_state() -> None:
    shuffled = list(reversed(_events()))
    res = replay_events("run_x", shuffled)  # type: ignore[arg-type]
    assert res.events_replayed == 6 and res.duplicates_skipped == 0
    assert res.terminal == "run.completed"
    assert res.state["tools"] == ["service.get_health"]
    assert len(res.state["findings"]) == 1


def test_replay_dedupes_duplicate_delivery() -> None:
    events = [*_events(), {"sequence": 2, "type": "tool.called", "payload": {"tool": "x"}}]
    res = replay_events("run_x", events)  # type: ignore[arg-type]
    assert res.duplicates_skipped == 1 and res.events_replayed == 6


def test_replay_rejects_gaps() -> None:
    events = [e for e in _events() if e["sequence"] != 3]
    with pytest.raises(ValueError, match="gaps"):
        replay_events("run_x", events)  # type: ignore[arg-type]


def test_worker_restart_resumes_from_persisted_log() -> None:
    # Simulate crash after seq 3: first half replays, then remainder appends.
    first, rest = _events()[:3], _events()[3:]
    part = replay_events("run_x", first)  # type: ignore[arg-type]
    assert part.terminal is None
    full = replay_events("run_x", first + rest)  # type: ignore[arg-type]
    assert full.terminal == "run.completed"
    assert full.events_replayed == 6
