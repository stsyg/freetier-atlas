"""Integration test against the live Docker Compose stack.

Skipped unless ``ATLAS_STACK_BASE_URL`` is set (the stack smoke scripts drive the
authoritative live checks). When set, this exercises the running API's health
endpoints against ground truth.
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.environ.get("ATLAS_STACK_BASE_URL")

pytestmark = pytest.mark.integration

skip_without_stack = pytest.mark.skipif(
    not BASE_URL,
    reason="ATLAS_STACK_BASE_URL not set; run scripts/stack-up then set it to enable.",
)


@skip_without_stack
def test_live_health_ok() -> None:
    response = httpx.get(f"{BASE_URL}/health", timeout=5.0)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@skip_without_stack
def test_live_readiness_ok() -> None:
    response = httpx.get(f"{BASE_URL}/health/ready", timeout=5.0)
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
