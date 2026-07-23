"""Unit tests for the shared declarative JSON extractor (``_json``).

These pin the module's core contract -- :func:`extract_records` and
:func:`parse_json` **never raise**, resolving every hostile or malformed input to
a returned :class:`JsonExtractError` (which each adapter maps to a rejected
``{error, detail}`` candidate). The recursion-bomb case is a regression guard:
``json.loads`` raises :class:`RecursionError` (a :class:`RuntimeError`, *not* a
:class:`ValueError`) on deeply-nested input, which must be caught here rather
than escaping to abort a scan.
"""

from __future__ import annotations

import pytest
from app.ingest.adapters._json import (
    JsonExtractionProfile,
    JsonField,
    extract_records,
    parse_json,
)

_PROFILE = JsonExtractionProfile(
    name="offer_api",
    records_path=("offers",),
    provider_key="provider",
    fields=(
        JsonField("service", "service", "text"),
        JsonField("offer_type", "billing", "text"),
    ),
    required_fields=("service", "offer_type"),
)


def _recursion_bomb(depth: int = 3000) -> str:
    """A JSON body whose nesting depth exceeds the decoder's recursion limit."""

    return '{"provider":"x","offers":' + "[" * depth + "]" * depth + "}"


def test_parse_json_recursion_bomb_returns_error_never_raises() -> None:
    value, error = parse_json(_recursion_bomb())
    assert value is None
    assert error is not None
    assert error.error == "malformed_json"
    assert error.detail  # a non-empty, allocation-safe detail


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"provider": "x", "offers": [',  # truncated
        '{"provider": "x"}',  # records path absent
        "[1, 2, 3]",  # non-mapping root with a records_path profile
    ],
)
def test_extract_records_malformed_returns_error_never_raises(text: str) -> None:
    extraction = extract_records(text, _PROFILE)
    assert extraction.records == ()
    assert extraction.error is not None
    assert extraction.error.error


def test_extract_records_recursion_bomb_is_handled_not_raised() -> None:
    extraction = extract_records(_recursion_bomb(), _PROFILE)
    assert extraction.records == ()
    assert extraction.error is not None
    assert extraction.error.error == "malformed_json"


def test_extract_records_missing_field_is_unknown_not_guessed() -> None:
    extraction = extract_records('{"provider": "x", "offers": [{"service": "S"}]}', _PROFILE)
    assert extraction.error is None
    (record,) = extraction.records
    # A present field is coerced; an absent field is UNKNOWN (None), never guessed.
    assert record.facts["service"] == "S"
    assert record.facts["offer_type"] is None
