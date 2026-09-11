"""Knowledge tools — mock fixtures (real adapters: Prompt 6)."""

from __future__ import annotations


def search(query: str) -> dict[str, object]:
    return {"query": query, "mode": "mock", "results": []}


def get_runbook(runbook_id: str) -> dict[str, object]:
    return {"runbook_id": runbook_id, "mode": "mock", "steps": []}
