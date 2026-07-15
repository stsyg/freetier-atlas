"""Unit tests for the API health endpoints (no live database required)."""

from __future__ import annotations

import app.main as main_module
from fastapi.testclient import TestClient

client = TestClient(main_module.app)


def test_health_liveness_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert "environment" in body


def test_root_descriptor() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"]


def test_readiness_ok_when_database_reachable(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "check_database", lambda: None)
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"


def test_readiness_503_when_database_unreachable(monkeypatch) -> None:
    def boom() -> None:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(main_module, "check_database", boom)
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "unreachable"
    # The failure message must not leak connection strings or credentials.
    assert "atlas" not in response.text.lower()
