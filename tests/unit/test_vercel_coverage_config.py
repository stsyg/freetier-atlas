"""Contract tests for the Vercel coverage-only provider declaration."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from app.config import ConfigError, load_and_validate
from app.config.models import ProviderConfig
from app.read_api.taxonomy import canonical_slugs

REPO_ROOT = Path(__file__).resolve().parents[2]
VERCEL_CONFIG = REPO_ROOT / "config" / "examples" / "providers" / "vercel.example.yaml"
GITHUB_CONFIG = REPO_ROOT / "config" / "examples" / "providers" / "github.example.yaml"

EXPECTED = {
    "compute-vms": ("offered_no_z0", "https://vercel.com/docs/sandbox/pricing"),
    "containers-app-hosting": ("offered_no_z0", "https://vercel.com/docs/plans/hobby"),
    "serverless-functions": (
        "offered_no_z0",
        "https://vercel.com/docs/functions/usage-and-pricing",
    ),
    "relational-databases": ("not_offered", "https://vercel.com/docs/marketplace-storage"),
    "nosql-key-value": ("not_offered", "https://vercel.com/docs/marketplace-storage"),
    "object-file-storage": (
        "offered_no_z0",
        "https://vercel.com/docs/vercel-blob/usage-and-pricing",
    ),
    "networking-cdn-dns": ("offered_no_z0", "https://vercel.com/docs/plans/hobby"),
    "queues-messaging-jobs": ("offered_no_z0", "https://vercel.com/docs/queues/pricing"),
    "auth-identity": (
        "unknown",
        "https://vercel.com/docs/deployment-protection/methods-to-protect-deployments/vercel-authentication",
    ),
    "cicd-source-control": ("offered_no_z0", "https://vercel.com/docs/deployments"),
    "monitoring-logs-tracing": ("offered_no_z0", "https://vercel.com/docs/observability"),
    "ai-inference-embeddings": (
        "offered_no_z0",
        "https://vercel.com/docs/ai-gateway/pricing",
    ),
    "email-notifications-comms": ("not_offered", "https://vercel.com/docs/notifications"),
    "secrets-config-devtools": (
        "offered_no_z0",
        "https://vercel.com/docs/global-config/global-config-limits",
    ),
}


def _load_vercel() -> ProviderConfig:
    model = load_and_validate(VERCEL_CONFIG)
    assert isinstance(model, ProviderConfig)
    return model


def test_vercel_loads_with_explicit_empty_sources() -> None:
    model = _load_vercel()
    raw = yaml.safe_load(VERCEL_CONFIG.read_text(encoding="utf-8"))

    assert "sources" in raw
    assert raw["sources"] == []
    assert model.sources == []
    assert model.service_categories == {}


def test_provider_sources_field_remains_required(tmp_path: Path) -> None:
    raw = yaml.safe_load(VERCEL_CONFIG.read_text(encoding="utf-8"))
    del raw["sources"]
    path = tmp_path / "vercel-without-sources.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems)
    assert "sources" in problems
    assert "type=missing" in problems


def test_existing_nonempty_provider_config_still_loads_identically() -> None:
    model = load_and_validate(GITHUB_CONFIG)
    assert isinstance(model, ProviderConfig)
    assert [source.id for source in model.sources] == [
        "github-actions-billing",
        "github-packages-billing",
        "github-codespaces-billing",
        "github-pages-limits",
        "github-enterprise-cloud-trial",
    ]


def test_empty_sources_rejects_a_coverage_source_reference(tmp_path: Path) -> None:
    raw = yaml.safe_load(VERCEL_CONFIG.read_text(encoding="utf-8"))
    declaration = raw["coverage"]["compute-vms"]
    declaration.pop("evidence_url")
    declaration["source"] = "synthetic-vercel-source"
    path = tmp_path / "vercel-bad-source.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems)
    assert "compute-vms" in problems
    assert "synthetic-vercel-source" in problems
    assert "not declared in this file" in problems


def test_vercel_declares_exact_canonical_state_and_url_map() -> None:
    model = _load_vercel()

    assert tuple(model.coverage) == canonical_slugs()
    assert len(model.coverage) == 14
    assert {
        slug: (entry.state, entry.evidence_url) for slug, entry in model.coverage.items()
    } == EXPECTED
    assert sum(entry.state == "offered_no_z0" for entry in model.coverage.values()) == 10
    assert sum(entry.state == "not_offered" for entry in model.coverage.values()) == 3
    assert sum(entry.state == "unknown" for entry in model.coverage.values()) == 1
    assert all(entry.state != "verified_free" for entry in model.coverage.values())
    assert all(entry.source is None for entry in model.coverage.values())
    assert all(
        (entry.evidence_url or "").startswith("https://vercel.com/")
        for entry in model.coverage.values()
    )


def test_vercel_rationales_pin_product_truth_and_taxonomy_boundaries() -> None:
    model = _load_vercel()
    rationales = {slug: entry.rationale or "" for slug, entry in model.coverage.items()}
    evidence_urls = "\n".join(entry.evidence_url or "" for entry in model.coverage.values()).lower()
    raw = yaml.safe_load(VERCEL_CONFIG.read_text(encoding="utf-8"))

    assert "no payment card" in rationales["compute-vms"]
    assert "functions" in rationales["containers-app-hosting"].lower()
    assert "dependency" in rationales["serverless-functions"].lower()
    assert "marketplace" in rationales["relational-databases"].lower()
    assert "global config" in rationales["nosql-key-value"].lower()
    assert "network/cdn" in rationales["object-file-storage"].lower()
    assert (
        "not a claim that vercel provides independent dns registration"
        in rationales["networking-cdn-dns"].lower()
    )
    assert "cron jobs" in rationales["queues-messaging-jobs"].lower()
    assert "remains unresolved" in rationales["auth-identity"].lower()
    assert (
        "not a claim that vercel is a source-control provider"
        in rationales["cicd-source-control"].lower()
    )
    assert "grace period" in rationales["monitoring-logs-tracing"].lower()
    assert (
        "temporary model promotions are not used" in rationales["ai-inference-embeddings"].lower()
    )
    assert "not a first-party transactional" in rationales["email-notifications-comms"].lower()
    assert "rather than nosql" in rationales["secrets-config-devtools"].lower()
    assert "/ling-" not in evidence_urls
    assert "/changelog/" not in evidence_urls
    assert "extraction_profile" not in str(raw)
    assert "service_categories" not in raw
