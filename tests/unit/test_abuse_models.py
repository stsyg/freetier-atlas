"""The five S2 abuse tables must be registered on the ORM metadata.

This guards the migration drift check: ``migrations/versions/0008`` must create
exactly the tables the models declare, so they have to be present in
``Base.metadata`` (via ``app.models``) for the live-DB ``compare_metadata`` test
to see them.
"""

from __future__ import annotations

from app import models

_EXPECTED = {
    "rate_limit_bucket",
    "abuse_flag",
    "circuit_breaker",
    "request_dedupe",
    "pow_challenge",
}


def test_abuse_tables_registered_on_metadata() -> None:
    registered = set(models.Base.metadata.tables)
    assert _EXPECTED <= registered


def test_abuse_models_exported() -> None:
    for name in (
        "RateLimitBucket",
        "AbuseFlag",
        "CircuitBreaker",
        "RequestDedupe",
        "PowChallenge",
    ):
        assert hasattr(models, name), name
