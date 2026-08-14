"""Contract tests for the AWS provider coverage declaration.

The declaration's job is to be honest in BOTH directions: it may not claim a
category is free without evidence, and it may not claim a category is absent
without evidence either.

AWS makes the second half unusually easy to get wrong. It obviously *does* offer
compute, storage and databases, so an author who "knows" that is tempted to
declare those categories offered. This slice declares only what an official
served page actually evidenced, and records for every other category exactly what
was probed and what it did or did not say.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from app.config import ConfigError, load_and_validate
from app.config.models import ProviderConfig
from app.read_api.taxonomy import canonical_slugs

REPO_ROOT = Path(__file__).resolve().parents[2]
AWS_CONFIG = REPO_ROOT / "config" / "examples" / "providers" / "aws.example.yaml"

EXPECTED: dict[str, tuple[str, str | None]] = {
    "compute-vms": ("unknown", None),
    "containers-app-hosting": ("unknown", None),
    "serverless-functions": ("unknown", None),
    "relational-databases": ("unknown", None),
    "nosql-key-value": ("offered_no_z0", "aws-dynamodb-free-tier"),
    "object-file-storage": ("unknown", None),
    "networking-cdn-dns": ("offered_no_z0", "aws-api-gateway-free-tier"),
    "queues-messaging-jobs": ("offered_no_z0", "aws-step-functions-free-tier"),
    "auth-identity": ("unknown", None),
    "cicd-source-control": ("unknown", None),
    "monitoring-logs-tracing": ("unknown", None),
    "ai-inference-embeddings": ("unknown", None),
    "email-notifications-comms": ("unknown", None),
    "secrets-config-devtools": ("unknown", None),
}

SOURCE_IDS = [
    "aws-free-tier-plan",
    "aws-free-plan",
    "aws-12-month-free-tier",
    "aws-dynamodb-free-tier",
    "aws-api-gateway-free-tier",
    "aws-step-functions-free-tier",
]


def _load() -> ProviderConfig:
    model = load_and_validate(AWS_CONFIG)
    assert isinstance(model, ProviderConfig)
    return model


def test_aws_loads_with_six_official_sources_over_six_documents() -> None:
    model = _load()
    raw = yaml.safe_load(AWS_CONFIG.read_text(encoding="utf-8"))

    assert [source["id"] for source in raw["sources"]] == SOURCE_IDS
    assert all(source.trust_level == "official" for source in model.sources)
    assert all((source.url or "").startswith("https://aws.amazon.com/") for source in model.sources)
    # Unlike Google Cloud, no two AWS sources share a document: each offer is
    # read from its own page, so no section anchor is needed to keep them apart.
    documents = {(source.url or "").split("#", 1)[0] for source in model.sources}
    assert len(documents) == 6
    assert len(model.sources) == 6


def test_no_source_uses_a_client_rendered_documentation_host() -> None:
    """MEASURED: docs.aws.amazon.com serves ~1 KB shells with no content.

    Two guide pages there were probed live and parsed to zero tables and zero
    text blocks. Citing one would produce a source that can never extract, so the
    allowlist for this provider is deliberately the marketing/pricing host only.
    """

    model = _load()
    assert all("docs.aws.amazon.com" not in (source.url or "") for source in model.sources)
    assert model.provider.official_domains == ["aws.amazon.com"]


def test_aws_declares_exact_canonical_state_and_provenance_map() -> None:
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


def test_no_category_asserts_absence_without_evidence() -> None:
    """`not_offered` is a positive claim, and this slice can support none of them.

    AWS plainly sells every category in the taxonomy, so `not_offered` would be
    wrong here. But "obviously offered" is not evidence either, which is why the
    unverified categories are `unknown` rather than `offered_no_z0`.
    """

    model = _load()
    for slug, entry in model.coverage.items():
        assert entry.state != "not_offered", (
            f"{slug}: declaring an AWS category absent needs an official page proving it, "
            "which this slice did not verify."
        )


@pytest.mark.parametrize(
    "slug",
    [
        "compute-vms",
        "containers-app-hosting",
        "serverless-functions",
        "object-file-storage",
        "auth-identity",
        "monitoring-logs-tracing",
    ],
)
def test_each_probed_unknown_names_what_was_actually_probed(slug: str) -> None:
    """An `unknown` that names no probe is indistinguishable from laziness."""

    model = _load()
    rationale = (model.coverage[slug].rationale or "").lower()
    assert "measured (live)" in rationale
    assert "aws.amazon.com/" in rationale
    assert "unknown" in rationale


@pytest.mark.parametrize(
    "slug",
    [
        "relational-databases",
        "cicd-source-control",
        "ai-inference-embeddings",
        "secrets-config-devtools",
    ],
)
def test_each_unprobed_unknown_says_so_plainly(slug: str) -> None:
    """Not probing something is a fine outcome; pretending otherwise is not."""

    model = _load()
    rationale = (model.coverage[slug].rationale or "").lower()
    assert "not probed" in rationale
    assert "unknown" in rationale


def test_rationales_pin_the_unfavourable_product_truth() -> None:
    model = _load()
    rationales = {slug: (entry.rationale or "").lower() for slug, entry in model.coverage.items()}
    raw_text = AWS_CONFIG.read_text(encoding="utf-8").lower()
    # Comments wrap, so compare against the file with comment markers and line
    # breaks collapsed. This normalises FORMAT only; the words still have to be
    # there in order.
    flat = " ".join(line.lstrip().lstrip("#").strip() for line in raw_text.splitlines())
    flat = " ".join(flat.split())

    # The two sentences that decide every AWS verdict must be quoted in the
    # config, not merely implied by the state values.
    assert (
        "you are required to provide a valid payment method to sign up for an aws account, "
        "whether you choose a free plan or a paid plan" in flat
    )
    assert "begin incurring charges at the standard pay-as-you-go service rates" in flat

    # The perpetual-but-still-billed finding is the most important one here.
    assert "indefinitely" in rationales["queues-messaging-jobs"]
    assert "charged per state transition above the free tier" in rationales["queues-messaging-jobs"]
    assert "z1" in rationales["queues-messaging-jobs"]

    assert "25 gb of data storage" in rationales["nosql-key-value"]
    assert "unknown rather than inferred" in rationales["nosql-key-value"]
    assert "up to 12 months" in rationales["networking-cdn-dns"]
    assert "worked pricing example" in rationales["serverless-functions"]
    assert "client-rendered" in flat

    # No Z0 claim may hide behind vocabulary: the only place the token may appear
    # is the comment that explains why nothing here uses it.
    assert "state: verified_free" not in raw_text


def test_the_always_free_finding_is_not_overstated() -> None:
    """One AWS offer is perpetual. The config must not let that imply free."""

    model = _load()
    perpetual = model.coverage["queues-messaging-jobs"]
    assert perpetual.state == "offered_no_z0"
    assert perpetual.source == "aws-step-functions-free-tier"
    rationale = (perpetual.rationale or "").lower()
    assert "perpetual" in rationale
    assert "no z0 offer" in rationale


def test_provider_rejects_a_duplicate_source_id(tmp_path: Path) -> None:
    raw = yaml.safe_load(AWS_CONFIG.read_text(encoding="utf-8"))
    raw["sources"].append(dict(raw["sources"][0]))
    path = tmp_path / "aws-duplicate-sources.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems)
    assert "provider 'aws'" in problems
    assert "duplicate source ids: aws-free-tier-plan" in problems


def test_coverage_rejects_an_undeclared_source_reference(tmp_path: Path) -> None:
    raw = yaml.safe_load(AWS_CONFIG.read_text(encoding="utf-8"))
    raw["coverage"]["nosql-key-value"]["source"] = "synthetic-aws-source"
    path = tmp_path / "aws-bad-source.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems)
    assert "nosql-key-value" in problems
    assert "synthetic-aws-source" in problems
    assert "not declared in this file" in problems


def test_an_offered_category_without_provenance_is_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load(AWS_CONFIG.read_text(encoding="utf-8"))
    raw["coverage"]["nosql-key-value"].pop("source")
    path = tmp_path / "aws-no-provenance.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems)
    assert "nosql-key-value" in problems
    assert "without provenance" in problems
