"""AG-UI adapter: canonical events (§4.2) -> AG-UI-facing events at API boundary.

Mapping (docs/architecture/agui-mapping.md):

| Canonical EventType        | AG-UI event              |
|----------------------------|--------------------------|
| run.started                | RUN_STARTED              |
| message.delta              | TEXT_MESSAGE_CONTENT     |
| tool.started/completed     | TOOL_CALL_START/FINISH   |
| agent.delegated (+completion)| ACTIVITY                |
| finding.created            | STATE_SNAPSHOT (card)    |
| approval.required          | FRONTEND_INTERACTION     |
| approval.received          | STATE_UPDATE             |
| run.completed/failed/cancelled | RUN_FINISHED/RUN_ERROR/RUN_CANCELLED |

Rules:
- Only ``safe_for_ui=True`` (and ``sensitive=False``) events are rendered;
  anything else returns ``None`` (filtered, never leaked to UI).
- Unknown future canonical types map to a generic collapsible
  ``UNKNOWN`` event (forward-compatible, never raises).
"""

from __future__ import annotations

from typing import Any

from packages.contracts.events import AgentEvent, EventType

AG_UI_TYPE_BY_CANONICAL: dict[str, str] = {
    EventType.RUN_STARTED.value: "RUN_STARTED",
    EventType.MESSAGE_DELTA.value: "TEXT_MESSAGE_CONTENT",
    EventType.TOOL_STARTED.value: "TOOL_CALL_START",
    EventType.TOOL_COMPLETED.value: "TOOL_CALL_FINISH",
    EventType.AGENT_DELEGATED.value: "ACTIVITY",
    EventType.FINDING_CREATED.value: "STATE_SNAPSHOT",
    EventType.APPROVAL_REQUIRED.value: "FRONTEND_INTERACTION",
    EventType.APPROVAL_RECEIVED.value: "STATE_UPDATE",
    EventType.RUN_COMPLETED.value: "RUN_FINISHED",
    EventType.RUN_FAILED.value: "RUN_ERROR",
    EventType.RUN_CANCELLED.value: "RUN_CANCELLED",
}

TERMINAL_AG_UI = frozenset({"RUN_FINISHED", "RUN_ERROR", "RUN_CANCELLED"})


def _as_dict(event: AgentEvent | dict[str, Any]) -> dict[str, Any]:
    if isinstance(event, AgentEvent):
        return event.model_dump(mode="json")
    return dict(event)


def is_renderable(event: AgentEvent | dict[str, Any]) -> bool:
    """True only when safe_for_ui and not sensitive."""
    d = _as_dict(event)
    if d.get("sensitive") is True:
        return False
    return d.get("safe_for_ui") is True


def to_ag_ui(event: AgentEvent | dict[str, Any]) -> dict[str, Any] | None:
    """Map one canonical event to an AG-UI event dict, or None to filter.

    Never raises on unknown types — returns a generic UNKNOWN envelope.
    """
    d = _as_dict(event)
    if not is_renderable(d):
        return None
    canonical = str(d.get("type", ""))
    ag_type = AG_UI_TYPE_BY_CANONICAL.get(canonical, "UNKNOWN")
    base: dict[str, Any] = {
        "type": ag_type,
        "sequence": d.get("sequence", 0),
        "event_id": d.get("event_id"),
        "run_id": d.get("run_id"),
        "timestamp": d.get("timestamp"),
        "trace_id": d.get("trace_id"),
    }
    data = d.get("data") if isinstance(d.get("data"), dict) else {}
    if ag_type == "RUN_STARTED":
        base["data"] = {"run_id": d.get("run_id"), **data}
    elif ag_type == "TEXT_MESSAGE_CONTENT":
        # Stream chunks: prefer delta/text/classification/plan style fields.
        text = (
            data.get("delta")
            or data.get("text")
            or data.get("clarification_question")
            or data.get("more_info")
            or data.get("policy_denial")
            or ""
        )
        base["data"] = {"delta": text, "raw": data}
    elif ag_type in ("TOOL_CALL_START", "TOOL_CALL_FINISH"):
        base["data"] = {
            "tool_name": data.get("tool_name"),
            "status": data.get("status"),
            "action_id": data.get("action_id"),
            "latency_s": data.get("latency_s"),
            "error": data.get("error"),
            "args": data.get("args", {}),
        }
    elif ag_type == "ACTIVITY":
        base["data"] = {
            "agent": data.get("agent"),
            "skill_id": data.get("skill_id"),
            "task_id": data.get("task_id"),
            "status": data.get("status", "delegated"),
        }
    elif ag_type == "STATE_SNAPSHOT":
        finding = data.get("finding", data)
        base["data"] = {
            "kind": "finding",
            "title": finding.get("title"),
            "summary": finding.get("summary"),
            "confidence": finding.get("confidence"),
            "severity": finding.get("severity"),
            "evidence_refs": finding.get("evidence_refs", []),
            "finding_id": finding.get("finding_id"),
        }
    elif ag_type == "FRONTEND_INTERACTION":
        # Approval card: action, reason, risk, rollback, approve/reject.
        actions = data.get("actions", [])
        approvals = data.get("approvals", [])
        base["data"] = {"kind": "approval", "actions": actions, "approvals": approvals}
    elif ag_type == "STATE_UPDATE":
        base["data"] = {
            "decision": data.get("decision"),
            "approver": data.get("approver"),
            "approval_id": data.get("approval_id"),
        }
    elif ag_type in TERMINAL_AG_UI:
        base["data"] = data
    else:  # UNKNOWN — generic collapsible, forward-compatible
        base["data"] = {"canonical_type": canonical, "payload": data}
    return base


def batch_to_ag_ui(events: list[AgentEvent | dict[str, Any]]) -> list[dict[str, Any]]:
    """Map a batch, dropping non-renderable events, preserving order."""
    out: list[dict[str, Any]] = []
    for e in events:
        mapped = to_ag_ui(e)
        if mapped is not None:
            out.append(mapped)
    out.sort(key=lambda m: int(m.get("sequence", 0)))
    return out


def is_terminal_ag_ui(event: dict[str, Any]) -> bool:
    return str(event.get("type", "")) in TERMINAL_AG_UI


__all__ = [
    "AG_UI_TYPE_BY_CANONICAL",
    "TERMINAL_AG_UI",
    "is_renderable",
    "to_ag_ui",
    "batch_to_ag_ui",
    "is_terminal_ag_ui",
]
