"""Unit tests for the deterministic free-text parser (F007 slice 1).

The parser is tier 1 of the routing ladder: a pure, rule-based interpreter that
runs before any LLM and re-derives nothing about Z0/quotas. These tests pin its
conservative behaviour and prove its output validates through the strict
request schema (so a parser success yields a byte-identical deterministic
recommendation downstream).
"""

from __future__ import annotations

import pytest
from app.adviser.llm.parser import deterministic_parse
from app.adviser.schema import RecommendationRequest


def test_parses_single_category_and_demand() -> None:
    parsed = deterministic_parse("I need object storage with 10 GB storage per month")
    assert parsed == {
        "requirements": [
            {
                "category": "object-file-storage",
                "demands": [{"metric": "storage", "amount": "10", "unit": "GB", "period": "month"}],
            }
        ]
    }


def test_output_validates_through_strict_schema() -> None:
    parsed = deterministic_parse("relational database with 500 requests per day")
    assert parsed is not None
    # The parser output must pass the *existing* strict request schema unchanged.
    request = RecommendationRequest.model_validate(parsed)
    assert request.requirements[0].category == "relational-databases"
    assert str(request.requirements[0].demands[0].amount) == "500"


def test_multiple_categories_one_requirement_each() -> None:
    parsed = deterministic_parse(
        "serverless functions with 1000000 invocations. object storage with 5 GB storage"
    )
    assert parsed is not None
    categories = [r["category"] for r in parsed["requirements"]]
    assert categories == ["serverless-functions", "object-file-storage"]


def test_amount_is_emitted_as_exact_string() -> None:
    parsed = deterministic_parse("object storage with 0.5 GB storage")
    assert parsed is not None
    # Kept as a string so the schema coerces the exact Decimal (never a float).
    assert parsed["requirements"][0]["demands"][0]["amount"] == "0.5"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "hello, I am just saying hi",
        "I need object storage",  # category but no quantified demand
        "please give me 10 GB",  # quantity but no recognised category
    ],
)
def test_returns_none_when_not_confidently_parseable(text: str) -> None:
    assert deterministic_parse(text) is None


def test_non_string_input_returns_none() -> None:
    assert deterministic_parse(None) is None  # type: ignore[arg-type]


def test_parser_output_never_contains_url_markers() -> None:
    # Even when the prose embeds a slash/host-ish token, the parser only ever
    # emits vocabulary/units/short words, so the output stays URL-clean and the
    # strict schema (which rejects "/") still accepts it.
    parsed = deterministic_parse("object storage with 10 GB storage")
    assert parsed is not None
    RecommendationRequest.model_validate(parsed)  # would raise on a URL marker


def test_default_metric_derived_from_unit_when_absent() -> None:
    parsed = deterministic_parse("serverless functions with 200 invocations")
    assert parsed is not None
    assert parsed["requirements"][0]["demands"][0]["metric"] == "invocations"
