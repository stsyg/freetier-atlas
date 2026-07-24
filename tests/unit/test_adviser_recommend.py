"""Unit tests for the deterministic adviser orchestrator (F006 slice 3)."""

from __future__ import annotations

from app.adviser.recommend import recommend
from app.adviser.schema import RecommendationRequest

from tests.support.synthetic import build_catalogue


def _z0_offer(oid, metric, amount, unit, *, reset="month"):
    return {
        "id": oid,
        "offer_type": "recurring_quota",
        "zero_cost_class": "Z0_TRUE_FREE",
        "commercial_use_allowed": True,
        "personal_use_allowed": True,
        "requires_card": False,
        "has_paid_dependencies": False,
        "version": {
            "material_facts": {
                "confidence": 0.95,
                "gate": {"automatic_threshold": 0.8, "uncertain_threshold": 0.5},
            },
            "quotas": [
                {
                    "metric": metric,
                    "amount": str(amount),
                    "unit": unit,
                    "reset_period": reset,
                    "exhaustion_behaviour": "hard_stop",
                }
            ],
        },
    }


def _provider(pid, slug, sid, category, offers, deployment="managed", traits=None):
    return {
        "id": pid,
        "slug": slug,
        "name": slug.title(),
        "services": [
            {
                "id": sid,
                "canonical_name": f"{slug} svc",
                "category_slug": category,
                "deployment_model": deployment,
                "portability_traits": traits or [],
                "offers": offers,
            }
        ],
    }


def _req(category, metric, amount, unit, *, period="month", label=None):
    return RecommendationRequest.model_validate(
        {
            "workload_name": "wl",
            "requirements": [
                {
                    "category": category,
                    "label": label,
                    "demands": [
                        {"metric": metric, "amount": str(amount), "unit": unit, "period": period}
                    ],
                }
            ],
        }
    )


def test_satisfiable_single_requirement() -> None:
    pool = build_catalogue(
        {
            "providers": [
                _provider(
                    1, "acme", 10, "object-file-storage", [_z0_offer(100, "storage", 10, "GB")]
                ),
            ]
        }
    ).pool()
    result = recommend(_req("object-file-storage", "storage", 5, "GB"), pool)
    assert result.fully_zero_cost is True
    assert len(result.components) == 1
    assert result.components[0].candidate.offer_id == 100
    assert result.impossible == ()


def test_tiebreak_picks_more_headroom_deterministically() -> None:
    cat = build_catalogue(
        {
            "providers": [
                _provider(
                    1, "acme", 10, "object-file-storage", [_z0_offer(100, "storage", 10, "GB")]
                ),
                _provider(
                    2, "beta", 20, "object-file-storage", [_z0_offer(200, "storage", 20, "GB")]
                ),
            ]
        }
    )
    req = _req("object-file-storage", "storage", 5, "GB")
    first = recommend(req, cat.pool())
    second = recommend(req, cat.pool())
    # More headroom (20GB) wins, and the choice is reproducible.
    assert first.components[0].candidate.offer_id == 200
    assert first.components[0].candidate.offer_id == second.components[0].candidate.offer_id


def test_impossible_order_reduction_recalc_selfhost() -> None:
    pool = build_catalogue(
        {
            "providers": [
                _provider(
                    1, "acme", 10, "object-file-storage", [_z0_offer(100, "storage", 10, "GB")]
                ),
                _provider(
                    2,
                    "oss",
                    20,
                    "object-file-storage",
                    [
                        {
                            "id": 200,
                            "offer_type": "self_hosted_open_source",
                            "zero_cost_class": "Z3_SELF_HOSTED_BUILDING_BLOCK",
                            "requires_card": False,
                            "has_paid_dependencies": False,
                            "version": {
                                "material_facts": {},
                                "quotas": [
                                    {
                                        "metric": "storage",
                                        "amount": "100",
                                        "unit": "GB",
                                        "reset_period": "month",
                                        "exhaustion_behaviour": "hard_stop",
                                    }
                                ],
                            },
                        }
                    ],
                    deployment="self_hosted",
                ),
                _provider(3, "hostco", 30, "compute-vms", [_z0_offer(300, "compute", 1, "vcpu")]),
            ]
        }
    ).pool()
    # Demand 50GB cannot fit the 10GB Z0 quota -> impossible order kicks in.
    result = recommend(_req("object-file-storage", "storage", 50, "GB"), pool)
    assert result.fully_zero_cost is False
    assert len(result.impossible) == 1
    imp = result.impossible[0]
    # (a) blocking reason present, (b) reduction computed, (c) recalculation fits,
    # (d) self-hosting on a Z0 host.
    assert imp.blocking_reason
    assert imp.reductions and imp.reductions[0].feasible is True
    assert imp.reductions[0].reduced_amount is not None
    assert imp.recalculated is not None and imp.recalculated.reduced is True
    assert imp.self_hosting and imp.self_hosting[0].building_block.offer_id == 200
    assert imp.self_hosting[0].host is not None and imp.self_hosting[0].host.offer_id == 300


def test_z1_z2_only_in_separate_section() -> None:
    pool = build_catalogue(
        {
            "providers": [
                _provider(
                    1,
                    "bigco",
                    10,
                    "object-file-storage",
                    [
                        {
                            "id": 300,
                            "offer_type": "recurring_quota",
                            "zero_cost_class": "Z1_BILLING_EXPOSURE",
                            "requires_card": False,
                            "has_paid_dependencies": False,
                            "version": {
                                "material_facts": {},
                                "quotas": [
                                    {
                                        "metric": "storage",
                                        "amount": "100",
                                        "unit": "GB",
                                        "reset_period": "month",
                                        "exhaustion_behaviour": "automatic_billing",
                                    }
                                ],
                            },
                        }
                    ],
                ),
            ]
        }
    ).pool()
    result = recommend(_req("object-file-storage", "storage", 5, "GB"), pool)
    # No Z0 offer -> not fully zero cost; Z1 must appear ONLY in the not-free set.
    assert result.fully_zero_cost is False
    assert result.components == ()
    assert [o.candidate.offer_id for o in result.not_free] == [300]


def test_priorities_are_product_fixed() -> None:
    pool = build_catalogue(
        {
            "providers": [
                _provider(
                    1, "acme", 10, "object-file-storage", [_z0_offer(100, "storage", 10, "GB")]
                ),
            ]
        }
    ).pool()
    result = recommend(_req("object-file-storage", "storage", 5, "GB"), pool)
    assert result.priorities == ("exactly_zero_cost", "portability", "low_lock_in")
