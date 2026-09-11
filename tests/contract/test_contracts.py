"""Contract: Agent Card shape + run-create envelope (no services required)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.a2a_agents.observability.agent import AGENT_CARD
from apps.api.main import app

client = TestClient(app)


def test_agent_card_contract() -> None:
    assert AGENT_CARD["name"] == "observability-agent"
    skills = AGENT_CARD["skills"]
    assert isinstance(skills, list) and skills[0]["id"] == "correlate-service-symptoms"
    assert "authentication" in AGENT_CARD


def test_create_run_envelope() -> None:
    r = client.post("/v1/runs", json={"objective": "Investigate checkout errors"})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "created"
    assert body["stream_url"].startswith("/v1/runs/")
