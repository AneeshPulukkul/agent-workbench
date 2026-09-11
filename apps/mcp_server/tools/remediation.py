"""Remediation tools — mock only. deployment.rollback supports dry_run and never acts locally."""

from __future__ import annotations


def simulate(action: str) -> dict[str, object]:
    return {"action": action, "mode": "mock", "risk": "unknown"}


def rollback(deployment: str, dry_run: bool = True) -> dict[str, object]:
    return {"deployment": deployment, "dry_run": dry_run, "mode": "mock", "executed": False}
