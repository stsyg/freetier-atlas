"""Fail-closed contract tests for generic HTML matrix and prose extraction."""

from __future__ import annotations

from dataclasses import replace

import pytest
from app.ingest import (
    FetchPolicy,
    FixtureFetcher,
    HtmlDocAdapter,
    HtmlExtractionProfile,
    HtmlMatrixRow,
    HtmlTextAssertion,
)
from app.ingest.scan import _content_hash

URL = "https://example.com/pricing"


def _profile(
    *,
    signature: tuple[str, ...] = ("Metric", "Pro"),
    tier: str = "Hobby",
    rows: dict[str, HtmlMatrixRow] | None = None,
    ignored: tuple[str, ...] = ("Ignored metric",),
    assertions: tuple[HtmlTextAssertion, ...] = (),
) -> HtmlExtractionProfile:
    return HtmlExtractionProfile(
        name="matrix_test",
        header_signature=signature,
        mode="matrix",
        matrix_metric_header="Metric",
        matrix_tier_header=tier,
        matrix_rows=rows
        or {
            "CPU": HtmlMatrixRow("cpu"),
            "Memory": HtmlMatrixRow("memory"),
            "Creations": HtmlMatrixRow("creations"),
        },
        ignored_matrix_rows=ignored,
        trusted_assertions=bool(assertions),
        assertions=assertions,
        required_fields=("service", "offer_type") if assertions else (),
    )


def _table(
    *,
    headers: tuple[str, ...] = ("Metric", "Hobby", "Pro"),
    rows: tuple[tuple[str, ...], ...] = (
        ("CPU", "First 5 hours/month", "$1/hour"),
        ("Memory", "Up to 420 GB-hours/month", "$2/GB-hour"),
        ("Creations", "5,000/month", "$3/1M"),
        ("Ignored metric", "20 GB/month", "$4/GB"),
    ),
    table_class: str = "table-module__buildHash__docsTable",
    header_markup: str | None = None,
) -> str:
    header = header_markup or "".join(f"<th>{value}</th>" for value in headers)
    body = "".join("<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>" for row in rows)
    return (
        f'<table class="{table_class}"><thead><tr>{header}</tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def _html(*parts: str) -> str:
    return (
        "<!doctype html><html><head><title>Matrix Offer</title></head><body>"
        + "".join(parts)
        + "</body></html>"
    )


def _extract(html: str, profile: HtmlExtractionProfile):
    fetcher = FixtureFetcher(
        {URL: (html.encode(), "text/html")},
        FetchPolicy(official_domains=("example.com",)),
    )
    adapter = HtmlDocAdapter(fetcher, (URL,), profile, provider="example")
    document = adapter.canonicalize(adapter.fetch(URL))
    return adapter, document, list(adapter.extract(document))


def _assert_rejected(candidates, error: str):
    assert len(candidates) == 1
    assert candidates[0].verification_state == "rejected"
    assert candidates[0].facts["error"] == error


def test_header_signature_selects_one_of_multiple_sibling_tables() -> None:
    other = _table(headers=("Region", "Availability"), rows=(("iad1", "Available"),))
    _, _, candidates = _extract(_html(other, _table()), _profile())

    assert len(candidates) == 1
    assert candidates[0].facts == {
        "cpu": "First 5 hours/month",
        "memory": "Up to 420 GB-hours/month",
        "creations": "5,000/month",
    }


def test_header_signature_zero_matches_is_actionable() -> None:
    _, _, candidates = _extract(
        _html(_table(headers=("Name", "Free"), rows=(("CPU", "5"),))),
        _profile(),
    )

    _assert_rejected(candidates, "table_not_found")
    assert "required_headers" in candidates[0].facts["detail"]
    assert "headers=" in candidates[0].facts["detail"]


def test_header_signature_multiple_matches_fails_closed() -> None:
    _, _, candidates = _extract(_html(_table(), _table()), _profile())
    _assert_rejected(candidates, "ambiguous_table")
    assert "matches=[0, 1]" in candidates[0].facts["detail"]


def test_header_signature_is_order_insensitive_and_normalizes_case_whitespace() -> None:
    rows = (
        ("$1/hour", " First 5 hours/month ", "CPU"),
        ("$2/GB-hour", "Up to 420 GB-hours/month", "Memory"),
        ("$3/1M", "5,000/month", "Creations"),
        ("$4/GB", "20 GB/month", "Ignored metric"),
    )
    profile = _profile(signature=(" pro ", " METRIC "), tier=" hobby ")
    _, _, candidates = _extract(
        _html(_table(headers=(" PRO ", "hObBy", " metric "), rows=rows)),
        profile,
    )
    assert candidates[0].facts["cpu"] == "First 5 hours/month"


@pytest.mark.parametrize(
    "header_markup",
    (
        "<th>Metric</th><th colspan='2'>Pro</th>",
        "<th>Metric</th><th rowspan='2'>Hobby</th><th>Pro</th>",
    ),
)
def test_header_signature_rejects_spanned_headers(header_markup: str) -> None:
    _, _, candidates = _extract(
        _html(_table(header_markup=header_markup)),
        _profile(),
    )
    _assert_rejected(candidates, "table_not_found")
    assert "rowspan/colspan" in candidates[0].facts["detail"]


def test_header_signature_rejects_multiple_thead_header_rows() -> None:
    table = (
        "<table><thead><tr><th>Plans</th></tr>"
        "<tr><th>Metric</th><th>Hobby</th><th>Pro</th></tr></thead>"
        "<tbody><tr><td>CPU</td><td>5</td><td>10</td></tr></tbody></table>"
    )
    _, _, candidates = _extract(_html(table), _profile())
    _assert_rejected(candidates, "table_not_found")
    assert "expected one header row" in candidates[0].facts["detail"]


def test_hashed_class_changes_do_not_change_extraction() -> None:
    _, first_document, first = _extract(
        _html(_table(table_class="table-module__first__docsTable")), _profile()
    )
    _, second_document, second = _extract(
        _html(_table(table_class="table-module__second__docsTable")), _profile()
    )

    assert first[0].facts == second[0].facts
    assert first_document.content_hash != second_document.content_hash


def test_matrix_missing_or_duplicate_tier_column_is_rejected() -> None:
    _, _, missing = _extract(
        _html(
            _table(
                headers=("Metric", "Free", "Pro"),
                rows=(
                    ("CPU", "5", "10"),
                    ("Memory", "420", "20"),
                    ("Creations", "5000", "30"),
                    ("Ignored metric", "20", "40"),
                ),
            )
        ),
        _profile(),
    )
    _assert_rejected(missing, "invalid_tier_column")

    duplicate_rows = (
        ("CPU", "5", "6", "10"),
        ("Memory", "420", "421", "20"),
        ("Creations", "5000", "5001", "30"),
        ("Ignored metric", "20", "21", "40"),
    )
    _, _, duplicate = _extract(
        _html(_table(headers=("Metric", "Hobby", "Hobby", "Pro"), rows=duplicate_rows)),
        _profile(),
    )
    _assert_rejected(duplicate, "invalid_tier_column")


@pytest.mark.parametrize(
    ("rows", "error"),
    (
        (
            (
                ("CPU", "5", "10"),
                ("Memory", "420", "20"),
                ("Ignored metric", "20", "40"),
            ),
            "missing_matrix_rows",
        ),
        (
            (
                ("CPU", "5", "10"),
                ("CPU", "6", "11"),
                ("Memory", "420", "20"),
                ("Creations", "5000", "30"),
                ("Ignored metric", "20", "40"),
            ),
            "duplicate_matrix_rows",
        ),
        (
            (
                ("CPU", "5", "10"),
                ("Memory", "420"),
                ("Creations", "5000", "30"),
                ("Ignored metric", "20", "40"),
            ),
            "irregular_row_width",
        ),
        (
            (
                ("CPU", "5", "10"),
                ("Memory", "420", "20"),
                ("Creations", "5000", "30"),
                ("Surprise metric", "1", "2"),
                ("Ignored metric", "20", "40"),
            ),
            "unknown_matrix_rows",
        ),
    ),
)
def test_matrix_rejects_bad_rows(rows: tuple[tuple[str, ...], ...], error: str) -> None:
    _, _, candidates = _extract(_html(_table(rows=rows)), _profile())
    _assert_rejected(candidates, error)


def test_matrix_rejects_conflicting_values_for_one_fact() -> None:
    profile = _profile(
        rows={
            "CPU": HtmlMatrixRow("capacity"),
            "Memory": HtmlMatrixRow("capacity"),
            "Creations": HtmlMatrixRow("creations"),
        }
    )
    _, _, candidates = _extract(_html(_table()), profile)
    _assert_rejected(candidates, "conflicting_matrix_values")


def test_matrix_preserves_qualifiers_and_emits_one_candidate() -> None:
    _, _, candidates = _extract(_html(_table()), _profile())
    assert len(candidates) == 1
    assert candidates[0].facts["cpu"] == "First 5 hours/month"
    assert candidates[0].facts["memory"] == "Up to 420 GB-hours/month"
    assert len(candidates[0].evidence) == 3
    assert all("-> fact[" in location.selector for location in candidates[0].evidence)


def _assertion(
    text: str = "Free & safe",
    *,
    field: str = "offer_type",
    value: object = "always_free",
    scope: str = "document",
    required: bool = True,
) -> HtmlTextAssertion:
    return HtmlTextAssertion(text=text, field=field, value=value, scope=scope, required=required)


def test_assertion_exact_match_normalizes_entities_and_whitespace() -> None:
    profile = _profile(assertions=(_assertion(),))
    _, _, candidates = _extract(_html(_table(), "<p>  Free &amp;\n safe </p>"), profile)
    assert candidates[0].facts["offer_type"] == "always_free"
    assert candidates[0].evidence[-1].excerpt == "Free & safe"


def test_required_assertion_missing_or_drifted_rejects_candidate() -> None:
    profile = _profile(assertions=(_assertion(),))
    _, _, candidates = _extract(_html(_table(), "<p>Free but changed</p>"), profile)
    _assert_rejected(candidates, "assertion_not_found")


def test_optional_assertion_missing_leaves_field_absent() -> None:
    profile = _profile(assertions=(_assertion(required=False),))
    _, _, candidates = _extract(_html(_table()), profile)
    assert "offer_type" not in candidates[0].facts


def test_duplicate_assertion_match_is_ambiguous() -> None:
    profile = _profile(assertions=(_assertion(),))
    _, _, candidates = _extract(
        _html(_table(), "<p>Free &amp; safe</p><p>Free &amp; safe</p>"),
        profile,
    )
    _assert_rejected(candidates, "ambiguous_assertion")


def test_conflicting_assertions_fail_closed() -> None:
    profile = _profile(
        assertions=(
            _assertion("Free plan", value="always_free"),
            _assertion("Trial plan", value="trial"),
        )
    )
    _, _, candidates = _extract(
        _html(_table(), "<p>Free plan</p><p>Trial plan</p>"),
        profile,
    )
    _assert_rejected(candidates, "conflicting_assertion")


@pytest.mark.parametrize(
    ("scope", "markup"),
    (
        ("title", ""),
        ("heading", "<h1>Matrix Offer</h1>"),
        ("document", "<p>Matrix Offer</p>"),
    ),
)
def test_assertion_scopes_are_exact(scope: str, markup: str) -> None:
    profile = _profile(assertions=(_assertion("Matrix Offer", scope=scope),))
    _, _, candidates = _extract(_html(_table(), markup), profile)
    assert candidates[0].facts["offer_type"] == "always_free"


def test_assertion_scope_mismatch_does_not_cross_match() -> None:
    profile = _profile(assertions=(_assertion("Matrix Offer", scope="heading"),))
    _, _, candidates = _extract(_html(_table(), "<p>Matrix Offer</p>"), profile)
    _assert_rejected(candidates, "assertion_not_found")


@pytest.mark.parametrize(
    "near_match",
    (
        "Prefix Free & safe",
        "Free & safe suffix",
        "Free and safe",
        "Not Free & safe",
    ),
)
def test_assertion_near_match_or_substring_never_satisfies_exact_text(near_match: str) -> None:
    profile = _profile(assertions=(_assertion(),))
    _, _, candidates = _extract(_html(_table(), f"<p>{near_match}</p>"), profile)
    _assert_rejected(candidates, "assertion_not_found")


def test_profile_rejects_untrusted_assertions() -> None:
    with pytest.raises(ValueError, match="trusted_assertions"):
        HtmlExtractionProfile(name="unsafe", assertions=(_assertion(),))


def test_second_document_cannot_complete_first_document() -> None:
    profile = _profile(assertions=(_assertion(),))
    _, _, matrix_only = _extract(_html(_table()), profile)
    _, _, prose_only = _extract(_html("<p>Free &amp; safe</p>"), profile)

    _assert_rejected(matrix_only, "assertion_not_found")
    _assert_rejected(prose_only, "table_not_found")


def test_same_document_output_and_fact_hash_are_deterministic() -> None:
    profile = _profile(assertions=(_assertion(),))
    html = _html(_table(), "<p>Free &amp; safe</p>")
    _, _, first = _extract(html, profile)
    _, _, second = _extract(html, replace(profile))

    assert first == second
    assert _content_hash(first[0].facts) == _content_hash(second[0].facts)


def test_old_synthetic_table_id_is_absent_and_not_required() -> None:
    html = _html(_table())
    assert 'id="free-tier"' not in html
    _, _, candidates = _extract(html, _profile())
    assert candidates[0].verification_state == "candidate"

    legacy = HtmlExtractionProfile(name="old-synthetic", table_id="free-tier")
    _, _, rejected = _extract(html, legacy)
    _assert_rejected(rejected, "table_not_found")
