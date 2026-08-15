"""Contract tests for the Microsoft Azure provider coverage declaration.

The declaration's job is to be honest in BOTH directions: it may not claim a
category is free without evidence, and it may not claim a category is absent
without evidence either.

Azure makes both halves easy to get wrong. It obviously *does* sell compute,
storage and databases, so an author who "knows" that is tempted to declare those
categories offered. It also genuinely publishes free tiers that a cautious
author is tempted to suppress -- Microsoft calls the Cosmos DB tier LIFETIME,
and the Students page says in so many words that no credit card is required.
This slice declares only what an official served page actually evidenced,
records for every other category exactly what was probed and what it did or did
not say, and refuses to drop the two favourable findings.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from app.config import ConfigError, load_and_validate
from app.config.models import ProviderConfig
from app.read_api.coverage import CoverageSignals
from app.read_api.taxonomy import canonical_slugs

from tests.support.coverage import assert_declarations_match_signals

REPO_ROOT = Path(__file__).resolve().parents[2]
AZURE_CONFIG = REPO_ROOT / "config" / "examples" / "providers" / "azure.example.yaml"

EXPECTED: dict[str, tuple[str, str | None]] = {
    "compute-vms": ("unknown", None),
    "containers-app-hosting": ("offered_no_z0", "azure-app-service-quotas"),
    "serverless-functions": ("unknown", None),
    "relational-databases": ("unknown", None),
    "nosql-key-value": ("offered_no_z0", "azure-cosmos-db-free-tier"),
    "object-file-storage": ("unknown", None),
    "networking-cdn-dns": ("unknown", None),
    "queues-messaging-jobs": ("unknown", None),
    "auth-identity": ("unknown", None),
    "cicd-source-control": ("offered_no_z0", "azure-devops-services"),
    "monitoring-logs-tracing": ("unknown", None),
    "ai-inference-embeddings": ("unknown", None),
    "email-notifications-comms": ("unknown", None),
    "secrets-config-devtools": ("unknown", None),
}

SOURCE_IDS = [
    "azure-free-account",
    "azure-free-services",
    "azure-cosmos-db-free-tier",
    "azure-app-service-quotas",
    "azure-static-web-apps-plans",
    "azure-devops-services",
    "azure-students",
]

#: Categories whose `unknown` is backed by a live probe that FOUND something,
#: found nothing usable, or was refused. Each must name the page it probed.
PROBED_UNKNOWNS = (
    "compute-vms",
    "serverless-functions",
    "object-file-storage",
    "auth-identity",
    "monitoring-logs-tracing",
    "secrets-config-devtools",
)

#: Categories nothing was probed for. Saying so plainly is a fine outcome.
UNPROBED_UNKNOWNS = (
    "relational-databases",
    "networking-cdn-dns",
    "queues-messaging-jobs",
    "ai-inference-embeddings",
    "email-notifications-comms",
)


def _load() -> ProviderConfig:
    model = load_and_validate(AZURE_CONFIG)
    assert isinstance(model, ProviderConfig)
    return model


def test_azure_loads_with_seven_official_sources_over_seven_documents() -> None:
    model = _load()
    raw = yaml.safe_load(AZURE_CONFIG.read_text(encoding="utf-8"))

    assert [source["id"] for source in raw["sources"]] == SOURCE_IDS
    assert all(source.trust_level == "official" for source in model.sources)
    hosts = {(source.url or "").split("://", 1)[-1].split("/", 1)[0] for source in model.sources}
    assert hosts == {"azure.microsoft.com", "learn.microsoft.com"}
    # Unlike Google Cloud, no two Azure sources share a document: each offer is
    # read from its own page, so no section anchor is needed to keep them apart.
    documents = {(source.url or "").split("#", 1)[0] for source in model.sources}
    assert len(documents) == 7
    assert len(model.sources) == 7


def test_the_slice_spans_both_marketing_and_documentation_hosts() -> None:
    """The split is a finding, not an accident.

    Azure's marketing host publishes the offer TERMS (the free account, the
    12-month window, DevOps pricing, Students) while the documentation host
    publishes the per-service ALLOWANCES and their exhaustion behaviour. A slice
    that used only one host would have had to compose facts across documents to
    say anything material.
    """

    model = _load()
    by_host: dict[str, set[str]] = {}
    for source in model.sources:
        host = (source.url or "").split("://", 1)[-1].split("/", 1)[0]
        by_host.setdefault(host, set()).add(source.id)
    assert by_host["azure.microsoft.com"] == {
        "azure-free-account",
        "azure-free-services",
        "azure-devops-services",
        "azure-students",
    }
    assert by_host["learn.microsoft.com"] == {
        "azure-cosmos-db-free-tier",
        "azure-app-service-quotas",
        "azure-static-web-apps-plans",
    }


def test_azure_declares_exact_canonical_state_and_provenance_map() -> None:
    model = _load()

    assert tuple(model.coverage) == canonical_slugs()
    assert len(model.coverage) == 14
    assert {slug: (entry.state, entry.source) for slug, entry in model.coverage.items()} == EXPECTED
    assert sum(entry.state == "offered_no_z0" for entry in model.coverage.values()) == 3
    assert sum(entry.state == "unknown" for entry in model.coverage.values()) == 11
    assert sum(entry.state == "not_offered" for entry in model.coverage.values()) == 0
    assert all(entry.state != "verified_free" for entry in model.coverage.values())
    # Provenance is a declared source in this file, never a bare URL, so every
    # claim traces to a capture with its own capture.json.
    assert all(entry.evidence_url is None for entry in model.coverage.values())
    assert all(entry.rationale for entry in model.coverage.values())


def test_the_eleven_unknowns_are_exactly_the_probed_plus_the_unprobed() -> None:
    """No third kind of `unknown` may exist: every one is accounted for."""

    model = _load()
    unknown = {slug for slug, entry in model.coverage.items() if entry.state == "unknown"}
    assert unknown == set(PROBED_UNKNOWNS) | set(UNPROBED_UNKNOWNS)
    assert not set(PROBED_UNKNOWNS) & set(UNPROBED_UNKNOWNS)
    assert len(unknown) == 11


def test_no_category_asserts_absence_without_evidence() -> None:
    """`not_offered` is a positive claim, and this slice can support none of them.

    Azure plainly sells every category in the taxonomy, so `not_offered` would be
    wrong here. But "obviously offered" is not evidence either, which is why the
    unverified categories are `unknown` rather than `offered_no_z0`.
    """

    model = _load()
    for slug, entry in model.coverage.items():
        assert entry.state != "not_offered", (
            f"{slug}: declaring an Azure category absent needs an official page proving it, "
            "which this slice did not verify."
        )


@pytest.mark.parametrize("slug", PROBED_UNKNOWNS)
def test_each_probed_unknown_names_what_was_actually_probed(slug: str) -> None:
    """An `unknown` that names no probe is indistinguishable from laziness."""

    model = _load()
    rationale = (model.coverage[slug].rationale or "").lower()
    assert "measured (live)" in rationale
    assert "azure.microsoft.com/" in rationale or "learn.microsoft.com/" in rationale
    assert "unknown" in rationale


@pytest.mark.parametrize("slug", UNPROBED_UNKNOWNS)
def test_each_unprobed_unknown_says_so_plainly(slug: str) -> None:
    """Not probing something is a fine outcome; pretending otherwise is not."""

    model = _load()
    rationale = (model.coverage[slug].rationale or "").lower()
    assert "not probed" in rationale
    assert "unknown" in rationale


def test_rationales_pin_the_unfavourable_product_truth() -> None:
    model = _load()
    rationales = {slug: (entry.rationale or "").lower() for slug, entry in model.coverage.items()}
    raw_text = AZURE_CONFIG.read_text(encoding="utf-8").lower()
    # Comments wrap, so compare against the file with comment markers and line
    # breaks collapsed. This normalises FORMAT only; the words still have to be
    # there in order.
    flat = " ".join(line.lstrip().lstrip("#").strip() for line in raw_text.splitlines())
    flat = " ".join(flat.split())

    # The two sentences that decide the Azure verdicts must be quoted in the
    # config, not merely implied by the state values.
    assert "all you need is a phone number, a credit card or a debit card (non-prepaid)" in flat
    assert (
        "the throughput and storage consumed beyond these limits are billed at regular price"
        in flat
    )

    # The perpetual-but-still-billed finding is the most important one here.
    assert "indefinitely" in rationales["nosql-key-value"]
    assert "billed at regular price" in rationales["nosql-key-value"]
    assert "z1" in rationales["nosql-key-value"]

    # The safe-exhaustion finding, and the narrowness of its failure.
    assert "stopped until the quota resets" in rationales["containers-app-hosting"]
    assert "payment card is required" in rationales["containers-app-hosting"]

    assert "1,800 minutes" in rationales["cicd-source-control"]
    assert "reset on the first day of the month" in rationales["cicd-source-control"]
    assert "client-rendered" in flat

    # No Z0 claim may hide behind vocabulary: the only place the token may appear
    # is the comment that explains why nothing here uses it.
    assert "state: verified_free" not in raw_text


def test_the_perpetual_finding_is_not_overstated() -> None:
    """One Azure offer is perpetual. The config must not let that imply free."""

    model = _load()
    perpetual = model.coverage["nosql-key-value"]
    assert perpetual.state == "offered_no_z0"
    assert perpetual.source == "azure-cosmos-db-free-tier"
    rationale = (perpetual.rationale or "").lower()
    assert "perpetual" in rationale
    assert "no z0 offer" in rationale
    assert "perpetual does not mean free" in rationale


def test_the_favourable_findings_are_not_suppressed() -> None:
    """Under-reporting a real free offer is a defect too, so both are recorded.

    The Cosmos DB lifetime tier and the App Service safe stop are the two
    findings a cautious author is most likely to bury, so the config must state
    both plainly rather than hide behind a bare `unknown`.
    """

    model = _load()
    flat = " ".join(AZURE_CONFIG.read_text(encoding="utf-8").split()).lower()
    assert "lifetime" in flat
    assert "no credit card is required" not in flat  # never asserted as a gate
    assert "the only block in this entire sweep stating" in flat
    # The two categories that DO carry an evidenced offer say what it is.
    assert (
        "safe, non-billing stop"
        in (model.coverage["containers-app-hosting"].rationale or "").lower()
    )
    assert (
        "genuinely perpetual azure allowance"
        in (model.coverage["nosql-key-value"].rationale or "").lower()
    )


def test_two_categories_name_a_concrete_candidate_for_the_next_slice() -> None:
    """A bounded scope is honest only when it names what it left on the table."""

    model = _load()
    auth = (model.coverage["auth-identity"].rationale or "").lower()
    secrets = (model.coverage["secrets-config-devtools"].rationale or "").lower()
    for rationale in (auth, secrets):
        assert "did not add it as a source" in rationale
        assert "candidate for the next azure slice" in rationale
        assert "rather than an unchecked absence" in rationale
    # Each names a real allowance it measured, so the deferral is evidenced.
    assert "50,000 monthly active users" in auth
    assert "1,000 requests per day" in secrets


def test_declarations_do_not_contradict_an_empty_published_catalogue() -> None:
    """The reusable Wave-3 helper, called from this provider's own module."""

    assert_declarations_match_signals(_load(), {})


def test_the_contradiction_helper_would_fire_on_a_suppressed_free_offer() -> None:
    """NON-VACUITY GUARD for the call above.

    A helper invoked with empty signals passes trivially. This proves it can
    fail: if Azure published a free offer in a category this file declares
    `unknown`, the declaration becomes a material contradiction.
    """

    config = _load()
    with pytest.raises(AssertionError) as excinfo:
        assert_declarations_match_signals(
            config,
            {"compute-vms": CoverageSignals(published_offer_count=1, free_offer_count=1)},
        )
    assert "compute-vms" in str(excinfo.value)


def test_provider_rejects_a_duplicate_source_id(tmp_path: Path) -> None:
    raw = yaml.safe_load(AZURE_CONFIG.read_text(encoding="utf-8"))
    raw["sources"].append(dict(raw["sources"][0]))
    path = tmp_path / "azure-duplicate-sources.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems)
    assert "provider 'azure'" in problems
    assert "duplicate source ids: azure-free-account" in problems


def test_coverage_rejects_an_undeclared_source_reference(tmp_path: Path) -> None:
    raw = yaml.safe_load(AZURE_CONFIG.read_text(encoding="utf-8"))
    raw["coverage"]["nosql-key-value"]["source"] = "synthetic-azure-source"
    path = tmp_path / "azure-bad-source.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems)
    assert "nosql-key-value" in problems
    assert "synthetic-azure-source" in problems
    assert "not declared in this file" in problems


def test_an_offered_category_without_provenance_is_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load(AZURE_CONFIG.read_text(encoding="utf-8"))
    raw["coverage"]["nosql-key-value"].pop("source")
    path = tmp_path / "azure-no-provenance.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems)
    assert "nosql-key-value" in problems
    assert "without provenance" in problems


def test_dropping_one_evidenced_category_breaks_the_evidence_floor(tmp_path: Path) -> None:
    """Exactly three categories are evidence-backed, which is the floor itself.

    That is worth pinning rather than leaving as a coincidence: a later author
    who withdraws one of the three must be told the config has become unloadable
    rather than discovering it in CI.
    """

    raw = yaml.safe_load(AZURE_CONFIG.read_text(encoding="utf-8"))
    raw["coverage"]["cicd-source-control"] = {
        "state": "unknown",
        "rationale": "withdrawn by this test to prove the floor is load-bearing",
    }
    path = tmp_path / "azure-below-floor.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems).lower()
    assert "azure" in problems
