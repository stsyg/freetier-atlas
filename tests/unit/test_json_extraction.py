"""Unit tests for the shared declarative JSON extractor (``_json``).

These pin the module's core contract -- :func:`extract_records` and
:func:`parse_json` **never raise**, resolving every hostile or malformed input to
a returned :class:`JsonExtractError` (which each adapter maps to a rejected
``{error, detail}`` candidate).

The deep-nesting case is a regression guard. It must be **deterministic and
portable**: an over-cap document is rejected by the explicit
:data:`MAX_JSON_NESTING_DEPTH` scan *before* ``json.loads`` on every platform,
rather than relying on the decoder raising :class:`RecursionError` (whose
threshold is interpreter/platform-dependent -- depth 3000 trips it on some
builds but not others, which previously made CI red while the dev box was
green). The tests therefore pick a depth relative to the cap, not a depth chosen
to trip RecursionError, and one test raises the interpreter recursion limit to
prove the cap (not RecursionError) is what classifies the bomb as rejected.
"""

from __future__ import annotations

import sys

import pytest
from app.ingest.adapters._json import (
    MAX_JSON_NESTING_DEPTH,
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


def _overcap_nesting(depth: int = MAX_JSON_NESTING_DEPTH + 50) -> str:
    """A *valid* but pathologically deep JSON body whose nesting exceeds the cap.

    Depth is chosen relative to the enforced cap, NOT to trip the interpreter's
    RecursionError (whose threshold is not portable), so this resolves to a
    rejected candidate deterministically on every platform.
    """

    return '{"provider":"x","offers":' + "[" * depth + "]" * depth + "}"


def test_parse_json_overcap_nesting_returns_error_never_raises() -> None:
    value, error = parse_json(_overcap_nesting())
    assert value is None
    assert error is not None
    assert error.error == "malformed_json"
    assert error.detail == "input nesting too deep"


def test_parse_json_overcap_rejected_even_when_recursionerror_cannot_fire() -> None:
    """The depth-cap -- not RecursionError -- classifies an over-cap bomb.

    Simulate a CI/Linux interpreter whose ``json`` recursion limit is effectively
    unreachable by raising the recursion limit so ``json.loads`` would NOT raise
    RecursionError for this depth. The input must STILL be rejected, proving the
    outcome is portable and independent of the interpreter recursion limit.
    """

    original = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(1_000_000)
        value, error = parse_json(_overcap_nesting())
    finally:
        sys.setrecursionlimit(original)
    assert value is None
    assert error is not None
    assert error.error == "malformed_json"
    assert error.detail == "input nesting too deep"


def test_parse_json_undercap_nesting_still_parses() -> None:
    """A legitimately nested document under the cap parses normally (no error)."""

    depth = MAX_JSON_NESTING_DEPTH - 10
    text = "[" * depth + "]" * depth  # valid, deeply-but-legally nested array
    value, error = parse_json(text)
    assert error is None
    assert value is not None


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


def test_extract_records_overcap_nesting_is_handled_not_raised() -> None:
    extraction = extract_records(_overcap_nesting(), _PROFILE)
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
