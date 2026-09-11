"""Contract: runs gateway — durable create/get/events, idempotency, approvals, cancel."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.store import reset_repository

OBJECTIVE = "Investigate elevated checkout API error rate"
CONTEXT = {"service": "checkout-api"}


@pytest.fixture()
def client():
    reset_repository("sqlite:///:memory:")
    with TestClient(app) as c:
        yield c


def _create(client: TestClient, key: str = "inv-001") -> dict:
    r = client.post(
        "/v1/runs",
        json={
            "objective": OBJECTIVE,
            "context": CONTEXT,
            "max_tool_calls": 20,
            "max_model_calls": 10,
            "max_cost_usd": 2.0,
            "schema_version": "1.0",
        },
        headers={"Idempotency-Key": key},
    )
    assert r.status_code == 202, r.text
    return r.json()


def test_create_get_events_replay(client: TestClient) -> None:
    created = _create(client)
    run_id = created["run_id"]
    assert created["stream_url"] == f"/v1/runs/{run_id}/events"
    assert created["schema_version"] == "1.0"

    g = client.get(f"/v1/runs/{run_id}")
    assert g.status_code == 200, g.text
    body = g.json()
    assert body["run_id"] == run_id
    assert body["status"]
    assert "approvals" in body and "budgets" in body

    ev = client.get(f"/v1/runs/{run_id}/events?after_sequence=0")
    assert ev.status_code == 200, ev.text
    payload = ev.json()
    assert payload["run_id"] == run_id
    events = payload["events"]
    assert len(events) >= 1
    seqs = [e["sequence"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    # Replay tail via after_sequence.
    tail = client.get(f"/v1/runs/{run_id}/events?after_sequence={seqs[0]}")
    assert tail.status_code == 200
    tail_events = tail.json()["events"]
    assert all(e["sequence"] > seqs[0] for e in tail_events)

    # Last-Event-ID fallback honors header when after_sequence=0.
    replay = client.get(
        f"/v1/runs/{run_id}/events?after_sequence=0",
        headers={"Last-Event-ID": str(seqs[0])},
    )
    assert replay.status_code == 200
    assert [e["sequence"] for e in replay.json()["events"]] == [
        e["sequence"] for e in tail_events
    ]


def test_idempotent_create_same_key(client: TestClient) -> None:
    first = _create(client, key="idem-same")
    second = _create(client, key="idem-same")
    assert first["run_id"] == second["run_id"]
    # No duplicate run: events identical on replay.
    e1 = client.get(f"/v1/runs/{first['run_id']}/events?after_sequence=0").json()
    assert len(e1["events"]) >= 1


def test_approve_double_decide_409(client: TestClient) -> None:
    created = _create(client, key="inv-appr")
    run_id = created["run_id"]
    listed = client.get(f"/v1/runs/{run_id}/approvals")
    assert listed.status_code == 200, listed.text
    approvals = listed.json()["approvals"]
    assert approvals, "expected approval gate rows after graph pause"
    approval_id = approvals[0]["approval_id"]

    d1 = client.post(
        f"/v1/runs/{run_id}/approvals/{approval_id}/decide",
        json={
            "decision": "approved",
            "approver": "oncall@example.com",
            "schema_version": "1.0",
        },
        headers={"Idempotency-Key": "dec-001"},
    )
    assert d1.status_code == 200, d1.text
    assert d1.json()["decision"] == "approved"

    # Same key replays stored outcome (no 409).
    d1_replay = client.post(
        f"/v1/runs/{run_id}/approvals/{approval_id}/decide",
        json={
            "decision": "approved",
            "approver": "oncall@example.com",
            "schema_version": "1.0",
        },
        headers={"Idempotency-Key": "dec-001"},
    )
    assert d1_replay.status_code == 200

    # Different key on decided row -> 409.
    d2 = client.post(
        f"/v1/runs/{run_id}/approvals/{approval_id}/decide",
        json={
            "decision": "rejected",
            "approver": "other@example.com",
            "schema_version": "1.0",
        },
        headers={"Idempotency-Key": "dec-002"},
    )
    assert d2.status_code == 409, d2.text


def test_decide_requires_approver_and_key(client: TestClient) -> None:
    created = _create(client, key="inv-req")
    run_id = created["run_id"]
    approvals = client.get(f"/v1/runs/{run_id}/approvals").json()["approvals"]
    assert approvals
    approval_id = approvals[0]["approval_id"]

    missing_key = client.post(
        f"/v1/runs/{run_id}/approvals/{approval_id}/decide",
        json={
            "decision": "approved",
            "approver": "oncall@example.com",
            "schema_version": "1.0",
        },
    )
    assert missing_key.status_code == 400

    missing_approver = client.post(
        f"/v1/runs/{run_id}/approvals/{approval_id}/decide",
        json={"decision": "approved", "schema_version": "1.0"},
        headers={"Idempotency-Key": "dec-x"},
    )
    assert missing_approver.status_code in (400, 422)


def test_cancel_propagates_and_idempotent(client: TestClient) -> None:
    from apps.api.store import A2A_CANCELLED

    created = _create(client, key="inv-cancel")
    run_id = created["run_id"]

    c1 = client.post(f"/v1/runs/{run_id}/cancel")
    assert c1.status_code == 202, c1.text
    assert c1.json()["status"] == "cancelled"

    g = client.get(f"/v1/runs/{run_id}")
    assert g.json()["status"] == "cancelled"

    ev = client.get(f"/v1/runs/{run_id}/events?after_sequence=0").json()["events"]
    assert any(e["type"] == "run.cancelled" for e in ev)
    assert f"{run_id}-obs-1" in A2A_CANCELLED

    c2 = client.post(f"/v1/runs/{run_id}/cancel")
    assert c2.status_code == 202
    assert c2.json()["status"] == "cancelled"
