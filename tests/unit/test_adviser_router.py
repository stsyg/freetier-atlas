"""Unit tests for the stateless POST /adviser/recommend route (F006 slice 3)."""

from __future__ import annotations

import pytest
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
                },
            ]
        }
    ).pool()


@pytest.fixture
def client(monkeypatch):
    # No DB: stub the session dependency and the catalogue read with a synthetic pool.
    monkeypatch.setattr("app.adviser.router.gather_candidates", lambda _session: _pool())
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


def test_recommend_returns_200_and_deterministic_body(client) -> None:
    r1 = client.post("/adviser/recommend", json=_SATISFIABLE)
    r2 = client.post("/adviser/recommend", json=_SATISFIABLE)
    assert r1.status_code == 200
    body = r1.json()
    assert body["fully_zero_cost"] is True
    assert body["architecture"][0]["offer"]["offer_id"] == 100
    assert body["priorities"] == ["exactly_zero_cost", "portability", "low_lock_in"]
    # Identical input -> byte-identical output.
    assert r1.json() == r2.json()


def test_get_is_not_allowed(client) -> None:
    assert client.get("/adviser/recommend").status_code == 405


def test_url_like_field_is_rejected(client) -> None:
    bad = {
        "workload_name": "http://evil.example.com/x",
        "requirements": _SATISFIABLE["requirements"],
    }
    assert client.post("/adviser/recommend", json=bad).status_code == 422


def test_unknown_field_is_rejected(client) -> None:
    bad = {"requirements": _SATISFIABLE["requirements"], "candidate": True}
    assert client.post("/adviser/recommend", json=bad).status_code == 422


def test_empty_requirements_rejected(client) -> None:
    assert client.post("/adviser/recommend", json={"requirements": []}).status_code == 422


def test_non_canonical_category_rejected(client) -> None:
    bad = {
        "requirements": [
            {
                "category": "object-storage",  # not one of the 14 canonical slugs
                "demands": [{"metric": "storage", "amount": "5", "unit": "GB"}],
            }
        ]
    }
    assert client.post("/adviser/recommend", json=bad).status_code == 422
