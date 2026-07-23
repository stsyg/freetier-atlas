"""Unit tests for the ingestion trust / quarantine-separation gate.

These cover the *application* half of the two-layer separation invariant
(the database half lives in migration 0006 + tests/integration/
test_ingest_separation.py). They are pure/offline: no DB, no network.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from app.ingest.trust import (
    OFFICIAL_TRUST_LEVEL,
    SeparationError,
    assert_evidence_permitted,
    is_official_source,
)


@dataclass
class _FakeSource:
    trust_level: str


def test_official_trust_level_constant() -> None:
    assert OFFICIAL_TRUST_LEVEL == "official"


@pytest.mark.parametrize(
    "trust_level",
    ["community", "unverified", "unknown", "", "Official", "OFFICIAL"],
)
def test_is_official_source_false_for_non_official(trust_level: str) -> None:
    assert is_official_source(_FakeSource(trust_level=trust_level)) is False


def test_is_official_source_true_only_for_exact_official() -> None:
    assert is_official_source(_FakeSource(trust_level="official")) is True


def test_is_official_source_missing_attribute_is_not_official() -> None:
    assert is_official_source(object()) is False  # type: ignore[arg-type]


def test_assert_evidence_permitted_allows_official_candidate() -> None:
    # Should not raise.
    assert_evidence_permitted(candidate_official=True)
    assert_evidence_permitted(candidate_official=True, trust_level="official")


def test_assert_evidence_permitted_rejects_non_official_candidate() -> None:
    with pytest.raises(SeparationError):
        assert_evidence_permitted(candidate_official=False)


def test_assert_evidence_permitted_error_mentions_trust_level() -> None:
    with pytest.raises(SeparationError) as excinfo:
        assert_evidence_permitted(candidate_official=False, trust_level="community")
    assert "community" in str(excinfo.value)


def test_separation_error_is_runtime_error() -> None:
    assert issubclass(SeparationError, RuntimeError)
