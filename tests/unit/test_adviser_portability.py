"""Unit tests for the deterministic portability assessment (F006 slice 3)."""

from __future__ import annotations

from decimal import Decimal

from app.adviser.portability import assess_portability, normalize_trait


def test_self_hosted_base_is_higher_than_managed() -> None:
    sh = assess_portability("self_hosted", [])
    mg = assess_portability("managed", [])
    assert sh.score > mg.score
    assert sh.deployment_model == "self_hosted"


def test_unknown_deployment_model_is_conservative() -> None:
    a = assess_portability(None, [])
    assert a.deployment_model == "unknown"
    assert a.score == Decimal("0.30")


def test_positive_traits_raise_score() -> None:
    a = assess_portability("managed", ["open_source", "s3_compatible"])
    assert a.score == Decimal("0.60")
    assert a.label == "medium"
    assert a.positive_traits == ("open_source", "s3_compatible")


def test_negative_traits_lower_score() -> None:
    a = assess_portability("managed", ["proprietary_api", "vendor_lock_in"])
    assert a.score == Decimal("0.20")
    assert a.label == "low"
    assert a.lock_in_label == "high"


def test_unknown_traits_are_neutral_not_guessed() -> None:
    a = assess_portability("managed", ["some_unrecognised_trait"])
    assert a.score == Decimal("0.40")  # unchanged
    assert a.unknown_traits == ("some_unrecognised_trait",)


def test_score_is_clamped() -> None:
    high = assess_portability(
        "self_hosted",
        ["open_source"] * 0
        + [
            "open_source",
            "self_hostable",
            "container_based",
            "s3_compatible",
            "postgres_compatible",
        ],
    )
    assert high.score <= Decimal("1")
    low = assess_portability(
        "managed",
        ["proprietary_api", "vendor_lock_in", "no_data_export", "closed_source", "egress_fees"],
    )
    assert low.score >= Decimal("0")


def test_exit_plan_always_present() -> None:
    a = assess_portability("managed", [])
    assert a.exit_plan  # never empty
    assert any("backup" in line.lower() for line in a.exit_plan)


def test_lock_in_is_inverse_of_portability() -> None:
    assert (
        assess_portability("self_hosted", ["open_source", "self_hostable"]).lock_in_label == "low"
    )
    assert assess_portability("managed", []).lock_in_label == "moderate"


def test_deterministic_and_order_independent_traits() -> None:
    a = assess_portability("managed", ["open_source", "s3_compatible"])
    b = assess_portability("managed", ["s3_compatible", "open_source"])
    assert a == b


def test_normalize_trait() -> None:
    assert normalize_trait("Open-Source") == "open_source"
    assert normalize_trait("  S3 Compatible ") == "s3_compatible"
