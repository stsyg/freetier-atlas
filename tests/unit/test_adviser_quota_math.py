"""Unit tests for exact-Decimal quota fit math (F006 slice 3)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.adviser.quota_math import (
    evaluate_demand,
    evaluate_offer,
    periods_compatible,
    reduced_demand_amount,
)


@dataclass
class D:
    metric: str
    amount: Decimal
    unit: str
    period: str | None = "month"


@dataclass
class Q:
    metric: str
    amount: object
    unit: str | None
    reset_period: str | None = "month"


def d(metric, amount, unit, period="month") -> D:
    return D(metric, Decimal(str(amount)), unit, period)


def test_covered_with_headroom() -> None:
    fit = evaluate_demand(d("storage", 5, "GB"), [Q("storage", "10", "GB")])
    assert fit.covered is True
    assert fit.headroom == Decimal("5000000000")
    assert fit.canonical_unit == "byte"
    assert fit.is_boundary is False


def test_exact_boundary_fits_with_zero_headroom() -> None:
    fit = evaluate_demand(d("storage", 10, "GB"), [Q("storage", "10", "GB")])
    assert fit.covered is True
    assert fit.headroom == Decimal(0)
    assert fit.is_boundary is True


def test_one_over_boundary_does_not_fit() -> None:
    fit = evaluate_demand(
        d("requests", 1000001, "requests"), [Q("requests", "1000000", "requests")]
    )
    assert fit.covered is False
    assert fit.headroom == Decimal(-1)


def test_decimal_exactness_no_float_artefact() -> None:
    # 0.1 + 0.2 != 0.3 in binary float; with Decimal the boundary is exact.
    fit = evaluate_demand(d("data", "0.3", "GB"), [Q("data", "0.3", "GB")])
    assert fit.covered is True
    assert fit.headroom == Decimal("0")
    assert fit.is_boundary is True


def test_unknown_demand_unit_fails_closed() -> None:
    fit = evaluate_demand(d("storage", 5, "blobs"), [Q("storage", "100", "blobs")])
    assert fit.covered is False
    assert "normaliz" in fit.reason.lower()


def test_unknown_quota_unit_fails_closed() -> None:
    fit = evaluate_demand(d("storage", 5, "GB"), [Q("storage", "100", "widgets")])
    assert fit.covered is False


def test_metric_mismatch_fails_closed() -> None:
    fit = evaluate_demand(d("storage", 5, "GB"), [Q("bandwidth", "100", "GB")])
    assert fit.covered is False
    assert "no quota measures" in fit.reason.lower()


def test_dimension_mismatch_fails_closed() -> None:
    # Same metric, but count vs data-size are not comparable dimensions.
    fit = evaluate_demand(d("things", 5, "requests"), [Q("things", "100", "GB")])
    assert fit.covered is False


def test_incompatible_period_fails_closed() -> None:
    fit = evaluate_demand(
        d("requests", 5, "requests", period="day"),
        [Q("requests", "100", "requests", reset_period="month")],
    )
    assert fit.covered is False


def test_period_none_is_compatible() -> None:
    assert periods_compatible(None, "month") is True
    assert periods_compatible("month", "monthly") is True
    assert periods_compatible("day", "month") is False


def test_metric_separators_normalise() -> None:
    fit = evaluate_demand(d("egress bandwidth", 5, "GB"), [Q("egress-bandwidth", "10", "GB")])
    assert fit.covered is True


def test_evaluate_offer_requires_all_demands() -> None:
    quotas = [Q("storage", "10", "GB"), Q("requests", "5", "requests")]
    demands = [d("storage", 5, "GB"), d("requests", 10, "requests")]
    fit = evaluate_offer(demands, quotas)
    assert fit.fits is False
    assert len(fit.blocking) == 1
    assert fit.blocking[0].metric == "requests"


def test_evaluate_offer_min_headroom_none_when_not_fitting() -> None:
    fit = evaluate_offer([d("storage", 50, "GB")], [Q("storage", "10", "GB")])
    assert fit.fits is False
    assert fit.min_headroom_ratio is None


def test_reduced_demand_amount_exact() -> None:
    amount = reduced_demand_amount(
        d("requests", 3000000, "requests"), [Q("requests", "1000000", "requests")]
    )
    assert amount == Decimal("1000000")


def test_reduced_demand_amount_fails_closed_on_unknown_unit() -> None:
    amount = reduced_demand_amount(d("storage", 5, "blobs"), [Q("storage", "100", "blobs")])
    assert amount is None
