"""Read-only telemetry tools — mock fixtures (real adapters: Prompt 6)."""

from __future__ import annotations


def query_metrics(service: str, metric: str) -> dict[str, object]:
    return {"service": service, "metric": metric, "mode": "mock", "points": []}


def query_logs(service: str, query: str) -> dict[str, object]:
    return {"service": service, "query": query, "mode": "mock", "logs": []}
