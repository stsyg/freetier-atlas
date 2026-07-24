"""Unit tests for the strict adviser request schema (F006 slice 3)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.adviser.schema import (
    FIXED_PRIORITIES,
    MAX_DEMANDS_PER_REQUIREMENT,
    MAX_REQUIREMENTS,
    Demand,
    RecommendationRequest,
)
from pydantic import ValidationError


def _req(**overrides) -> dict:
    base = {
        "requirements": [
            {
                "category": "object-file-storage",
                "demands": [{"metric": "storage", "amount": "5", "unit": "GB", "period": "month"}],
            }
        ]
    }
    base.update(overrides)
    return base


def test_fixed_priorities_constant() -> None:
    assert FIXED_PRIORITIES == ("exactly_zero_cost", "portability", "low_lock_in")


def test_valid_request_parses() -> None:
    request = RecommendationRequest.model_validate(_req(workload_name="demo"))
    assert request.workload_name == "demo"
    assert request.requirements[0].category == "object-file-storage"


def test_amount_is_exact_decimal_from_string() -> None:
    demand = Demand.model_validate({"metric": "cpu", "amount": "0.1", "unit": "count"})
    assert demand.amount == Decimal("0.1")
    assert isinstance(demand.amount, Decimal)


def test_amount_float_is_coerced_without_binary_artefact() -> None:
    demand = Demand.model_validate({"metric": "cpu", "amount": 0.1, "unit": "count"})
    assert demand.amount == Decimal("0.1")


def test_amount_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Demand.model_validate({"metric": "cpu", "amount": "0", "unit": "count"})


@pytest.mark.parametrize(
    "bad",
    ["https://x.test", "http://x", "a/b", "www.example.com", "user@host", "..\\etc", "a://b"],
)
def test_url_like_values_are_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        RecommendationRequest.model_validate(
            {
                "requirements": [
                    {
                        "category": "object-file-storage",
                        "demands": [{"metric": bad, "amount": "1", "unit": "GB"}],
                    }
                ]
            }
        )


def test_url_like_workload_name_rejected() -> None:
    with pytest.raises(ValidationError):
        RecommendationRequest.model_validate(_req(workload_name="http://evil.test"))


def test_unknown_category_rejected() -> None:
    with pytest.raises(ValidationError):
        RecommendationRequest.model_validate(
            _req(
                requirements=[
                    {
                        "category": "not-a-category",
                        "demands": [{"metric": "x", "amount": "1", "unit": "GB"}],
                    }
                ]
            )
        )


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        RecommendationRequest.model_validate(_req(surprise="nope"))


def test_requirements_bounded() -> None:
    too_many = [
        {
            "category": "object-file-storage",
            "demands": [{"metric": "storage", "amount": "1", "unit": "GB"}],
        }
        for _ in range(MAX_REQUIREMENTS + 1)
    ]
    with pytest.raises(ValidationError):
        RecommendationRequest.model_validate({"requirements": too_many})


def test_demands_bounded_and_non_empty() -> None:
    with pytest.raises(ValidationError):
        RecommendationRequest.model_validate(
            _req(requirements=[{"category": "object-file-storage", "demands": []}])
        )
    too_many = [
        {"metric": "m", "amount": "1", "unit": "GB"} for _ in range(MAX_DEMANDS_PER_REQUIREMENT + 1)
    ]
    with pytest.raises(ValidationError):
        RecommendationRequest.model_validate(
            _req(requirements=[{"category": "object-file-storage", "demands": too_many}])
        )
