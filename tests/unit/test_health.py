"""Unit: gateway health/readiness + policy defaults (no services required)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import app
from apps.orchestrator.policies import evaluate

client = TestClient(app)


def test_health() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready_mock() -> None:
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["postgres"] == "mock-ok"


def test_policy_defaults() -> None:
    assert evaluate("telemetry.query_metrics", "none").allowed is True
    assert evaluate("ticket.create", "external_write").requires_approval is True
    assert evaluate("database.failover", "destructive").allowed is False
