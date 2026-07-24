"""Unit tests for adviser candidate selection + Z0-safety cross-check (F006 s3)."""

from __future__ import annotations

from tests.support.synthetic import build_pool_from


def _cat(providers) -> dict:
    return {"providers": providers}


def _offer(oid, zclass, *, offer_type="recurring_quota", exhaustion="hard_stop", card=False):
    return {
        "id": oid,
        "offer_type": offer_type,
        "zero_cost_class": zclass,
        "requires_card": card,
        "has_paid_dependencies": False,
        "version": {
            "material_facts": {
                "confidence": 0.95,
                "gate": {"automatic_threshold": 0.8, "uncertain_threshold": 0.5},
            },
            "quotas": [
                {
                    "metric": "storage",
                    "amount": "10",
                    "unit": "GB",
                    "reset_period": "month",
                    "exhaustion_behaviour": exhaustion,
                }
            ],
        },
    }


def _provider(pid, slug, sid, category, offers, deployment="managed"):
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
                "portability_traits": [],
                "offers": offers,
            }
        ],
    }


def test_z0_offer_enters_z0_pool() -> None:
    pool = build_pool_from(
        _cat(
            [
                _provider(1, "acme", 10, "object-file-storage", [_offer(100, "Z0_TRUE_FREE")]),
            ]
        )
    )
    assert [c.offer_id for c in pool.z0] == [100]
    assert pool.z3 == () and pool.not_free == () and pool.excluded == ()


def test_z3_held_for_self_hosting() -> None:
    pool = build_pool_from(
        _cat(
            [
                _provider(
                    1,
                    "oss",
                    10,
                    "serverless-functions",
                    [
                        _offer(
                            200,
                            "Z3_SELF_HOSTED_BUILDING_BLOCK",
                            offer_type="self_hosted_open_source",
                        )
                    ],
                    deployment="self_hosted",
                ),
            ]
        )
    )
    assert [c.offer_id for c in pool.z3] == [200]
    assert pool.z0 == ()


def test_z1_z2_go_to_not_free() -> None:
    pool = build_pool_from(
        _cat(
            [
                _provider(
                    1,
                    "bigco",
                    10,
                    "object-file-storage",
                    [_offer(300, "Z1_BILLING_EXPOSURE", exhaustion="automatic_billing")],
                ),
                _provider(
                    2,
                    "trialco",
                    20,
                    "object-file-storage",
                    [_offer(400, "Z2_TEMPORARY_OR_CONDITIONAL", offer_type="trial")],
                ),
            ]
        )
    )
    assert sorted(c.offer_id for c in pool.not_free) == [300, 400]
    assert pool.z0 == ()


def test_contradiction_is_excluded_not_recommended() -> None:
    # Persisted Z0 but the engine sees automatic_billing -> Z1: disagreement.
    pool = build_pool_from(
        _cat(
            [
                _provider(
                    1,
                    "sneaky",
                    10,
                    "object-file-storage",
                    [_offer(500, "Z0_TRUE_FREE", exhaustion="automatic_billing")],
                ),
            ]
        )
    )
    assert pool.z0 == ()
    assert [c.offer_id for c in pool.excluded] == [500]
    assert pool.excluded[0].engine_class == "Z1_BILLING_EXPOSURE"
    assert pool.excluded[0].persisted_class == "Z0_TRUE_FREE"


def test_unknown_class_is_excluded() -> None:
    pool = build_pool_from(
        _cat(
            [
                _provider(
                    1,
                    "murky",
                    10,
                    "object-file-storage",
                    [_offer(600, "UNKNOWN", exhaustion="unknown")],
                ),
            ]
        )
    )
    assert pool.z0 == () and pool.z3 == () and pool.not_free == ()
    assert [c.offer_id for c in pool.excluded] == [600]


def test_unpublished_offer_is_ignored() -> None:
    # An offer with no version is not published and must never be selected.
    provider = _provider(
        1,
        "draft",
        10,
        "object-file-storage",
        [
            {
                "id": 700,
                "offer_type": "recurring_quota",
                "zero_cost_class": "Z0_TRUE_FREE",
                "requires_card": False,
                "has_paid_dependencies": False,
            }
        ],
    )
    pool = build_pool_from(_cat([provider]))
    assert pool.z0 == () and pool.excluded == ()
