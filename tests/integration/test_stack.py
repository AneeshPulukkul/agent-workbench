"""Integration: requires `make up` (postgres + services). Skipped otherwise."""

from __future__ import annotations

import os

import httpx
import pytest

API = os.getenv("TEST_API_URL", "http://localhost:8080")

pytestmark = pytest.mark.integration


def _up() -> bool:
    try:
        r = httpx.get(f"{API}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def test_api_ready_live() -> None:
    if not _up():
        pytest.skip("stack not up (run `make up` first)")
    r = httpx.get(f"{API}/ready", timeout=5)
    assert r.status_code == 200
