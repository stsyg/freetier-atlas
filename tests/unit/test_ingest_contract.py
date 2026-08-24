"""Source-adapter contract tests (offline).

Verifies the ABC enforces all seven methods, that the reference JSON adapter
runs end-to-end through the offline FixtureFetcher producing candidate-only
facts with evidence and health, and that the verification vocabulary is closed
and matches docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import pytest
from app.ingest import (
    AdapterHealth,
    CandidateFacts,
    EvidenceLocation,
    FetchPolicy,
    FixtureFetcher,
    JsonOfferAdapter,
    OfflineFetcher,
    SourceAdapter,
    SourceDocument,
    is_verification_state,
)
from app.ingest.base import CONTRACT_METHODS
from app.ingest.vocab import VERIFICATION_STATES

_DOCUMENTED_STATES = {
    "detected",
    "extracting",
    "candidate",
    "verified",
    "verified_with_caveats",
    "conflict",
    "stale",
    "withdrawn",
    "rejected",
}


def _full_method_impls() -> dict:
    return {
        "discover": lambda self: [],
        "fetch": lambda self, url: None,
        "canonicalize": lambda self, result: None,
        "extract": lambda self, document: [],
        "validate": lambda self, candidate: [],
        "evidence": lambda self, candidate: [],
        "health": lambda self: None,
    }


def test_contract_method_names_are_the_seven() -> None:
    assert set(CONTRACT_METHODS) == {
        "discover",
        "fetch",
        "canonicalize",
        "extract",
        "validate",
        "evidence",
        "health",
    }


def test_complete_adapter_can_be_instantiated() -> None:
    cls = type("CompleteAdapter", (SourceAdapter,), _full_method_impls())
    instance = cls(OfflineFetcher())
    assert isinstance(instance, SourceAdapter)


@pytest.mark.parametrize("missing", CONTRACT_METHODS)
def test_adapter_missing_any_method_cannot_instantiate(missing) -> None:
    impls = _full_method_impls()
    del impls[missing]
    cls = type(f"Missing_{missing}", (SourceAdapter,), impls)
    with pytest.raises(TypeError):
        cls(OfflineFetcher())


def test_verification_vocab_is_closed_and_matches_docs() -> None:
    assert set(VERIFICATION_STATES) == _DOCUMENTED_STATES
    # No duplicates and deterministic ordering.
    assert len(VERIFICATION_STATES) == len(_DOCUMENTED_STATES)
    assert is_verification_state("candidate")
    assert not is_verification_state("published")


def test_the_reserved_fact_field_registry_cannot_drift_from_the_publisher() -> None:
    """The two halves of the registry must name exactly the same fields.

    ``app.ingest.vocab.RESERVED_FACT_FIELDS`` decides which fact fields an
    assertion may pin by name; ``app.publish.revalidate.NON_QUOTA_FIELDS``
    decides which fact keys the publisher does NOT turn into quota metrics.
    They are the same set viewed from two sides, and they live in two modules
    because ``app.publish`` imports ``app.ingest`` (so the reverse import would
    be a cycle).

    A silent divergence is the exact failure the assertion-field registry
    exists to prevent, one layer up: a name that ingest thinks is reserved but
    publish does not would be validated as a material condition and then
    republished as a quota row.
    """

    from app.ingest.vocab import (
        ASSERTION_FIELD_RULES,
        NON_ASSERTABLE_FACT_FIELDS,
        RESERVED_FACT_FIELDS,
    )
    from app.publish.revalidate import NON_QUOTA_FIELDS

    assert RESERVED_FACT_FIELDS == NON_QUOTA_FIELDS, (
        "the ingest assertion registry and the publisher's non-quota registry have drifted:\n"
        f"  only in ingest:  {sorted(RESERVED_FACT_FIELDS - NON_QUOTA_FIELDS)}\n"
        f"  only in publish: {sorted(NON_QUOTA_FIELDS - RESERVED_FACT_FIELDS)}"
    )
    # The two halves partition the registry: assertable, or control plane.
    assert set(ASSERTION_FIELD_RULES) | NON_ASSERTABLE_FACT_FIELDS == RESERVED_FACT_FIELDS
    assert set(ASSERTION_FIELD_RULES) & NON_ASSERTABLE_FACT_FIELDS == set()


def test_no_module_in_the_ingest_package_declares_all_twice() -> None:
    """A module's declared public API must not be silently overwritten.

    THE DEFECT THIS EXISTS FOR, found by two independent Level-2 evaluators:
    ``app.ingest.vocab`` declared ``__all__`` TWICE. Python executes top to
    bottom, so the second assignment silently voided the first and twelve names
    the module meant to export never reached its declared API. Behaviourally
    inert -- nothing star-imports it and ``app.ingest.__init__`` imports
    explicitly -- but silently wrong, and NO existing gate caught it: ``ruff``
    exits 0 because pyflakes ``F811`` does not cover module-level variable
    rebinding, and no test asserted this module's ``__all__``.

    That is precisely the failure mode this slice exists to end -- an implicit
    contract a contributor can violate with no deterministic failure -- so it is
    checked here rather than left to review. It is deliberately written for the
    WHOLE package, not for the one module that failed: a guard shaped around the
    single known instance would not catch the next one.

    The repository already treats ``__all__`` as a contract elsewhere
    (``tests/unit/test_adapter_oracle.py`` iterates ``oracle_module.__all__``),
    so a truncated ``__all__`` is not merely cosmetic.
    """

    import ast
    from pathlib import Path

    package_root = Path(__file__).resolve().parents[2] / "apps" / "api" / "app" / "ingest"
    modules = sorted(package_root.rglob("*.py"))
    assert len(modules) >= 15, (
        f"only {len(modules)} modules found under {package_root}; the walk stopped "
        "seeing the ingest package"
    )

    offenders: list[str] = []
    checked = 0
    for path in modules:
        if "__pycache__" in path.parts:
            continue
        checked += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assignments = [
            node.lineno
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(getattr(target, "id", None) == "__all__" for target in node.targets)
        ]
        if len(assignments) > 1:
            offenders.append(
                f"{path.name} assigns __all__ {len(assignments)}x at lines {assignments}"
            )

    assert checked >= 15, f"only {checked} modules actually parsed; the walk is vacuous"
    assert not offenders, (
        "a module's declared public API is silently overwritten by a later assignment "
        "(the earlier one is dead, and ruff does not catch this):\n  " + "\n  ".join(offenders)
    )


def test_the_ingest_vocabulary_exports_what_it_declares() -> None:
    """Every name in ``vocab.__all__`` resolves, and a star-import agrees.

    Checked at RUNTIME rather than by reading the source: the defect above was
    invisible to reading precisely because both assignments looked correct in
    isolation. A star-import is what a consumer actually gets, so that is what
    is measured.
    """

    from app.ingest import vocab

    assert len(vocab.__all__) == len(set(vocab.__all__)), "__all__ contains duplicates"

    missing = [name for name in vocab.__all__ if not hasattr(vocab, name)]
    assert not missing, f"__all__ names that do not exist on the module: {missing}"

    namespace: dict[str, object] = {}
    exec("from app.ingest.vocab import *", namespace)  # noqa: S102 - measuring the real effect
    exported = {name for name in namespace if not name.startswith("__")}
    assert exported == set(vocab.__all__), (
        "a star-import does not match the declared __all__:\n"
        f"  declared not exported: {sorted(set(vocab.__all__) - exported)}\n"
        f"  exported not declared: {sorted(exported - set(vocab.__all__))}"
    )

    # The two registry halves are part of the public contract, so their absence
    # would be a silent truncation of exactly the kind this guards against.
    for name in ("ASSERTION_FIELD_RULES", "NON_ASSERTABLE_FACT_FIELDS", "assertion_field_problem"):
        assert name in vocab.__all__, f"{name} is public API but is not declared in __all__"


def test_candidate_facts_cannot_be_born_verified() -> None:
    with pytest.raises(ValueError):
        CandidateFacts(
            provider="p", source_url="https://p.example/x", facts={}, verification_state="verified"
        )
    with pytest.raises(ValueError):
        CandidateFacts(
            provider="p", source_url="https://p.example/x", facts={}, verification_state="bogus"
        )
    # candidate is allowed.
    ok = CandidateFacts(provider="p", source_url="https://p.example/x", facts={})
    assert ok.verification_state == "candidate"


# --------------------------------------------------------------------------
# Reference adapter end-to-end (offline)
# --------------------------------------------------------------------------

_SOURCE_URL = "https://provider.example/free.json"
_DOC = (
    b'{"provider":"provider.example","offers":['
    b'{"service":"Widgets","offer_type":"always_free",'
    b'"requires_card":false,"has_paid_dependencies":false,'
    b'"quotas":[{"metric":"requests","exhaustion_behaviour":"hard_stop"}]}'
    b"]}"
)


def _reference_adapter() -> JsonOfferAdapter:
    policy = FetchPolicy(official_domains=("provider.example",))
    fetcher = FixtureFetcher({_SOURCE_URL: (_DOC, "application/json")}, policy)
    return JsonOfferAdapter(fetcher, source_urls=(_SOURCE_URL,))


def test_reference_adapter_end_to_end_offline() -> None:
    adapter = _reference_adapter()

    urls = adapter.discover()
    assert urls == (_SOURCE_URL,)

    result = adapter.fetch(urls[0])
    document = adapter.canonicalize(result)
    assert isinstance(document, SourceDocument)
    assert document.mime == "application/json"
    assert document.content_hash == result.content_hash

    candidates = adapter.extract(document)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert isinstance(candidate, CandidateFacts)
    # Candidate only -- never a verified fact.
    assert candidate.verification_state == "candidate"
    assert candidate.facts["service"] == "Widgets"
    assert candidate.facts["offer_type"] == "always_free"
    assert candidate.facts["quotas"] == ("hard_stop",)

    assert adapter.validate(candidate) == []

    evidence = adapter.evidence(candidate)
    assert evidence and isinstance(evidence[0], EvidenceLocation)
    assert evidence[0].url == _SOURCE_URL
    assert evidence[0].selector == "$.offers[0]"
    assert evidence[0].content_hash == document.content_hash


def test_reference_adapter_validate_flags_missing_fields() -> None:
    adapter = _reference_adapter()
    incomplete = CandidateFacts(
        provider="p", source_url=_SOURCE_URL, facts={"service": None, "offer_type": None}
    )
    problems = adapter.validate(incomplete)
    assert any("service" in p for p in problems)
    assert any("offer_type" in p for p in problems)


def test_reference_adapter_health_ok_offline() -> None:
    adapter = _reference_adapter()
    health = adapter.health()
    assert isinstance(health, AdapterHealth)
    assert health.healthy is True
    assert health.source_url == _SOURCE_URL


def test_reference_adapter_health_unhealthy_when_source_unreachable() -> None:
    # FixtureFetcher with no matching fixture -> fetch raises -> health False.
    policy = FetchPolicy(official_domains=("provider.example",))
    adapter = JsonOfferAdapter(FixtureFetcher({}, policy), source_urls=(_SOURCE_URL,))
    health = adapter.health()
    assert health.healthy is False
    assert "not_found" in health.detail


def test_reference_adapter_uses_only_the_fetcher_seam(monkeypatch) -> None:
    # Prove the adapter reaches the network only through the injected fetcher:
    # a fetcher whose fetch is intercepted is the only I/O path used.
    import socket

    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("adapter opened a socket directly")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    adapter = _reference_adapter()
    # Full pipeline runs with sockets forbidden.
    document = adapter.canonicalize(adapter.fetch(adapter.discover()[0]))
    assert adapter.extract(document)
