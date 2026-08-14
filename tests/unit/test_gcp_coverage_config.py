"""Contract tests for the Google Cloud provider coverage declaration.

The declaration's job is to be honest in BOTH directions: it may not claim a
category is free without evidence, and it may not claim a category is absent
without evidence either. Google Cloud offers almost every category, so an
unsupported ``not_offered`` would be as wrong here as an unsupported
``verified_free``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from app.config import ConfigError, load_and_validate
from app.config.models import ProviderConfig
from app.read_api.taxonomy import canonical_slugs

REPO_ROOT = Path(__file__).resolve().parents[2]
GCP_CONFIG = REPO_ROOT / "config" / "examples" / "providers" / "gcp.example.yaml"

FREE_TIER_SOURCE = "gcp-free-tier-products"

EXPECTED: dict[str, tuple[str, str | None]] = {
    "compute-vms": ("offered_no_z0", FREE_TIER_SOURCE),
    "containers-app-hosting": ("offered_no_z0", FREE_TIER_SOURCE),
    "serverless-functions": ("offered_no_z0", FREE_TIER_SOURCE),
    "relational-databases": ("offered_no_z0", "gcp-bigquery-free-tier"),
    "nosql-key-value": ("offered_no_z0", "gcp-firestore-free-tier"),
    "object-file-storage": ("offered_no_z0", FREE_TIER_SOURCE),
    "networking-cdn-dns": ("unknown", None),
    "queues-messaging-jobs": ("offered_no_z0", FREE_TIER_SOURCE),
    "auth-identity": ("unknown", None),
    "cicd-source-control": ("offered_no_z0", FREE_TIER_SOURCE),
    "monitoring-logs-tracing": ("offered_no_z0", FREE_TIER_SOURCE),
    "ai-inference-embeddings": ("offered_no_z0", FREE_TIER_SOURCE),
    "email-notifications-comms": ("unknown", None),
    "secrets-config-devtools": ("offered_no_z0", FREE_TIER_SOURCE),
}


def _load() -> ProviderConfig:
    model = load_and_validate(GCP_CONFIG)
    assert isinstance(model, ProviderConfig)
    return model


def test_gcp_loads_with_four_official_sources_over_three_documents() -> None:
    model = _load()
    raw = yaml.safe_load(GCP_CONFIG.read_text(encoding="utf-8"))

    assert [source["id"] for source in raw["sources"]] == [
        "gcp-free-tier-products",
        "gcp-free-trial",
        "gcp-firestore-free-tier",
        "gcp-bigquery-free-tier",
    ]
    assert [source.id for source in model.sources] == [source.id for source in model.sources]
    assert all(source.trust_level == "official" for source in model.sources)
    assert all(
        (source.url or "").startswith("https://cloud.google.com/") for source in model.sources
    )
    # Two sources deliberately read the SAME document through different section
    # anchors, because that page publishes two different offers.
    documents = {(source.url or "").split("#", 1)[0] for source in model.sources}
    assert len(documents) == 3
    assert len(model.sources) == 4


def test_the_two_offers_on_one_page_use_the_pages_own_section_anchors() -> None:
    model = _load()
    by_id = {source.id: source.url for source in model.sources}
    assert by_id["gcp-free-tier-products"].endswith("/free/docs/free-cloud-features#free-tier")
    assert by_id["gcp-free-trial"].endswith("/free/docs/free-cloud-features#free-trial")


def test_gcp_declares_exact_canonical_state_and_provenance_map() -> None:
    model = _load()

    assert tuple(model.coverage) == canonical_slugs()
    assert len(model.coverage) == 14
    assert {slug: (entry.state, entry.source) for slug, entry in model.coverage.items()} == EXPECTED
    assert sum(entry.state == "offered_no_z0" for entry in model.coverage.values()) == 11
    assert sum(entry.state == "unknown" for entry in model.coverage.values()) == 3
    assert sum(entry.state == "not_offered" for entry in model.coverage.values()) == 0
    assert all(entry.state != "verified_free" for entry in model.coverage.values())
    # Provenance is a declared source in this file, never a bare URL, so every
    # claim traces to a capture with its own capture.json.
    assert all(entry.evidence_url is None for entry in model.coverage.values())
    assert all(entry.rationale for entry in model.coverage.values())


def test_no_category_asserts_absence_without_evidence() -> None:
    """`not_offered` is a positive claim, and this slice can support none of them."""

    model = _load()
    for slug, entry in model.coverage.items():
        assert entry.state != "not_offered", (
            f"{slug}: declaring a Google Cloud category absent needs an official page "
            "proving it, which this slice did not verify."
        )


@pytest.mark.parametrize(
    "slug", ["networking-cdn-dns", "auth-identity", "email-notifications-comms"]
)
def test_each_unknown_is_deliberate_after_research(slug: str) -> None:
    model = _load()
    rationale = (model.coverage[slug].rationale or "").lower()
    assert "measured" in rationale
    assert "free tier usage-limits table" in rationale
    assert "unknown" in rationale


def test_rationales_pin_the_unfavourable_product_truth() -> None:
    model = _load()
    rationales = {slug: (entry.rationale or "").lower() for slug, entry in model.coverage.items()}
    raw_text = GCP_CONFIG.read_text(encoding="utf-8").lower()
    # Comments wrap, so compare against the file with comment markers and line
    # breaks collapsed. This normalises FORMAT only; the words still have to be
    # there in order.
    flat = " ".join(line.lstrip().lstrip("#").strip() for line in raw_text.splitlines())
    flat = " ".join(flat.split())

    # The one sentence that decides every Google Cloud verdict must be quoted in
    # the config, not merely implied by the state values.
    assert "any usage that exceeds the free tier usage limits is billed at standard rates" in flat
    assert "billed at standard rates" in rationales["compute-vms"]
    assert "e2-micro" in rationales["compute-vms"]
    assert "cluster charge only" in rationales["containers-app-hosting"]
    assert "cloud run functions" in rationales["serverless-functions"]
    assert "analytical warehouse" in rationales["relational-databases"]
    assert "enabling billing" in rationales["nosql-key-value"]
    assert "us-east1, us-west1 and us-central1" in rationales["object-file-storage"]
    assert "recaptcha" in rationales["auth-identity"]
    assert "considered and rejected" in rationales["auth-identity"]
    assert "cloud kms autokey" in rationales["secrets-config-devtools"]
    # No Z0 claim may hide behind vocabulary: the only place the token may
    # appear is the comment that explains why nothing here uses it.
    assert "state: verified_free" not in raw_text


def test_provider_rejects_a_duplicate_source_id(tmp_path: Path) -> None:
    raw = yaml.safe_load(GCP_CONFIG.read_text(encoding="utf-8"))
    raw["sources"].append(dict(raw["sources"][0]))
    path = tmp_path / "gcp-duplicate-sources.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems)
    assert "provider 'gcp'" in problems
    assert "duplicate source ids: gcp-free-tier-products" in problems


def test_coverage_rejects_an_undeclared_source_reference(tmp_path: Path) -> None:
    raw = yaml.safe_load(GCP_CONFIG.read_text(encoding="utf-8"))
    raw["coverage"]["compute-vms"]["source"] = "synthetic-gcp-source"
    path = tmp_path / "gcp-bad-source.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems)
    assert "compute-vms" in problems
    assert "synthetic-gcp-source" in problems
    assert "not declared in this file" in problems


def test_an_offered_category_without_provenance_is_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load(GCP_CONFIG.read_text(encoding="utf-8"))
    raw["coverage"]["compute-vms"].pop("source")
    path = tmp_path / "gcp-no-provenance.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems)
    assert "compute-vms" in problems
    assert "without provenance" in problems
