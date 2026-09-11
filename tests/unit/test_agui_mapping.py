"""Unit: AG-UI adapter — mapping, safe_for_ui gating, unknown tolerance."""

from __future__ import annotations

from datetime import UTC, datetime

from packages.contracts.events import AgentEvent
from packages.protocols.ag_ui import batch_to_ag_ui, is_renderable, to_ag_ui

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def _ev(seq: int, type: str, data: dict | None = None, **kw):  # type: ignore[no-untyped-def]
    base = dict(
        event_id=f"e{seq}",
        run_id="run_1",
        tenant_id="t1",
        sequence=seq,
        type=type,
        timestamp=NOW,
        data=data or {},
        safe_for_ui=True,
    )
    base.update(kw)
    return AgentEvent(**base)  # type: ignore[arg-type]


def test_only_safe_for_ui_renders():
    assert to_ag_ui(_ev(1, "message.delta", {"delta": "hi"})) is not None
    assert to_ag_ui(_ev(2, "message.delta", {"delta": "hi"}, safe_for_ui=False)) is None
    assert (
        to_ag_ui(_ev(3, "message.delta", {"delta": "hi"}, sensitive=True, safe_for_ui=False))
        is None
    )
    assert is_renderable(_ev(1, "message.delta")) is True


def test_mapping_table():
    assert to_ag_ui(_ev(1, "run.started"))["type"] == "RUN_STARTED"  # type: ignore[index]
    assert to_ag_ui(_ev(2, "message.delta", {"delta": "x"}))["type"] == "TEXT_MESSAGE_CONTENT"  # type: ignore[index]
    assert to_ag_ui(_ev(3, "tool.started", {"tool_name": "a"}))["type"] == "TOOL_CALL_START"  # type: ignore[index]
    assert to_ag_ui(_ev(4, "tool.completed", {"tool_name": "a"}))["type"] == "TOOL_CALL_FINISH"  # type: ignore[index]
    assert to_ag_ui(_ev(5, "agent.delegated", {"agent": "obs"}))["type"] == "ACTIVITY"  # type: ignore[index]
    out = to_ag_ui(_ev(6, "finding.created", {"finding": {"title": "t", "summary": "s"}}))
    assert out is not None and out["type"] == "STATE_SNAPSHOT"
    out = to_ag_ui(_ev(7, "approval.required", {"actions": [], "approvals": []}))
    assert out is not None and out["type"] == "FRONTEND_INTERACTION"
    assert to_ag_ui(_ev(8, "approval.received", {"decision": "approved"}))["type"] == "STATE_UPDATE"  # type: ignore[index]
    assert to_ag_ui(_ev(9, "run.completed", {}))["type"] == "RUN_FINISHED"  # type: ignore[index]


def test_unknown_type_tolerated():
    raw = dict(_ev(10, "run.started").model_dump(mode="json"))
    raw["type"] = "future.wonder"
    out = to_ag_ui(raw)  # type: ignore[arg-type]
    assert out is not None and out["type"] == "UNKNOWN"
    assert out["data"]["canonical_type"] == "future.wonder"


def test_batch_drops_unsafe_preserves_order():
    events = [
        _ev(3, "tool.started", {"tool_name": "a"}),
        _ev(1, "message.delta", {"delta": "x"}),
        _ev(2, "message.delta", {"delta": "y"}, safe_for_ui=False),
    ]
    out = batch_to_ag_ui(events)  # type: ignore[arg-type]
    assert [e["sequence"] for e in out] == [1, 3]
