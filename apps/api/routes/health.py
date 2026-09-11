"""Liveness + readiness probes (spec §11.2, api-contracts.md)."""

from __future__ import annotations

import os

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "agent-api"}


@router.get("/ready")
def ready() -> dict[str, object]:
    # Local/mock mode: no real enterprise or DB dependency yet.
    # Later milestones add postgres + downstream checks; keep mock-ok behind APP_ENV=local.
    mode = os.getenv("APP_ENV", "local")
    checks: dict[str, str] = {"postgres": "mock-ok", "mcp": "mock-ok", "a2a": "mock-ok"}
    return {"status": "ready", "mode": mode, "checks": checks}
