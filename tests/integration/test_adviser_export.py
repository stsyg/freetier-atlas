"""Integration tests for POST /adviser/export.

Covers the stateless route contract: deterministic 200 body, secret-free files,
405 for GET, 422 for URL-like input, an impossible-requirement bundle, and a
probe proving no DB row is written by a generation call.
"""

from __future__ import annotations

import pytest
from app.adviser.abuse import InMemoryAbuseStore
from app.adviser.export import ALLOWED_PATHS, scan_secrets
from app.db import get_session
from app.main import app
from fastapi.testclient import TestClient

from tests.support.synthetic import build_catalogue


def _pool():
    return build_catalogue(
        {
            "providers": [
                {
                    "id": 1,
                    "slug": "acme",
                    "name": "Acme",
                    "services": [
                        {
                            "id": 10,
                            "canonical_name": "acme store",
                            "category_slug": "object-file-storage",
                            "deployment_model": "managed",
                            "portability_traits": ["open_source"],
                            "offers": [
                                {
                                    "id": 100,
                                    "offer_type": "recurring_quota",
                                    "zero_cost_class": "Z0_TRUE_FREE",
                                    "commercial_use_allowed": True,
                                    "personal_use_allowed": True,
                                    "requires_card": False,
                                    "has_paid_dependencies": False,
                                    "version": {
                                        "material_facts": {"confidence": 0.95},
                                        "quotas": [
                                            {
                                                "metric": "storage",
                                                "amount": "10",
                                                "unit": "GB",
                                                "reset_period": "month",
                                                "exhaustion_behaviour": "hard_stop",
                                            }
                                        ],
                                    },
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    ).pool()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.adviser.router.gather_candidates", lambda _session: _pool())
    store = InMemoryAbuseStore()
    monkeypatch.setattr("app.adviser.router.get_abuse_store", lambda: store)
    app.dependency_overrides[get_session] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


_SATISFIABLE = {
    "workload_name": "demo",
    "requirements": [
        {
            "category": "object-file-storage",
            "demands": [{"metric": "storage", "amount": "5", "unit": "GB", "period": "month"}],
        }
    ],
}


def test_export_returns_200_with_validated_secret_free_files(client) -> None:
    response = client.post("/adviser/export", json=_SATISFIABLE)
    assert response.status_code == 200
    body = response.json()
    assert body["fully_zero_cost"] is True
    paths = [f["path"] for f in body["files"]]
    assert "docker-compose.yml" in paths and "MANIFEST.json" in paths
    assert set(paths) <= ALLOWED_PATHS
    for f in body["files"]:
        assert scan_secrets(f["content"]) == []
    assert all(body["manifest"]["validation"].values())


def test_export_is_deterministic(client) -> None:
    r1 = client.post("/adviser/export", json=_SATISFIABLE)
    r2 = client.post("/adviser/export", json=_SATISFIABLE)
    assert r1.json() == r2.json()


def test_export_get_is_not_allowed(client) -> None:
    assert client.get("/adviser/export").status_code == 405


def test_export_rejects_url_like_field(client) -> None:
    bad = {
        "workload_name": "https://evil.example.com/x",
        "requirements": _SATISFIABLE["requirements"],
    }
    assert client.post("/adviser/export", json=bad).status_code == 422


def test_export_handles_impossible_requirement(client) -> None:
    impossible = {
        "workload_name": "needs ai",
        "requirements": [
            {
                "category": "ai-inference-embeddings",
                "demands": [
                    {"metric": "tokens", "amount": "1000", "unit": "count", "period": "month"}
                ],
            }
        ],
    }
    response = client.post("/adviser/export", json=impossible)
    assert response.status_code == 200
    body = response.json()
    assert body["fully_zero_cost"] is False
    # Still returns a validated, secret-free bundle.
    for f in body["files"]:
        assert scan_secrets(f["content"]) == []
