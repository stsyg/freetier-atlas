"""Unit tests for the declarative configuration system (F003 slice 1).

Cover the good example files, unknown-field rejection, malformed YAML, missing
required fields, inline-secret rejection, environment-variable-name references,
JSON Schema export, and the CLI. All tests run offline.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from app.config import ConfigError, load_and_validate
from app.config.cli import main as cli_main
from app.config.loader import detect_family
from app.config.models import FAMILY_MODELS, MIN_EVIDENCE_BACKED_COVERAGE
from app.models.vocab import EVIDENCE_BACKED_COVERAGE_STATES
from app.read_api.taxonomy import canonical_slugs

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPO_ROOT / "config" / "examples"


def _example_files() -> list[Path]:
    return sorted(EXAMPLES.rglob("*.yaml"))


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Positive: the shipped example configs are valid.
# --------------------------------------------------------------------------- #
def test_example_files_exist() -> None:
    files = _example_files()
    assert files, "expected example YAML config files under config/examples"


@pytest.mark.parametrize("path", _example_files(), ids=lambda p: p.name)
def test_example_configs_validate(path: Path) -> None:
    model = load_and_validate(path)
    assert model is not None


def test_family_detection_covers_every_example() -> None:
    for path in _example_files():
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert detect_family(data) in FAMILY_MODELS


# --------------------------------------------------------------------------- #
# Negative: unknown field is rejected with an actionable, path-scoped error.
# --------------------------------------------------------------------------- #
def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "application.yaml",
        """
application:
  name: FreeTier Atlas
  public_url: http://localhost:3000
  api_url: http://localhost:8000
  environment: development
  typo_field: oops
catalogue:
  default_zero_cost_classes: [Z0_TRUE_FREE]
  hide_temporary_offers_by_default: true
  raw_snapshot_retention_days: 90
admin:
  authentication: github
  allowed_users: [stsyg]
features:
  public_adviser: true
  rss: true
  discord: false
  web_push: false
""",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)
    problems = "\n".join(excinfo.value.problems)
    assert "typo_field" in problems
    assert "application" in problems


# --------------------------------------------------------------------------- #
# Negative: malformed YAML reports file + line/column.
# --------------------------------------------------------------------------- #
def test_malformed_yaml_reports_line_and_column(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "broken.yaml",
        "schedules:\n  rss:\n    cron: '17 * * * *'\n   bad_indent: 1\n",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)
    problems = "\n".join(excinfo.value.problems)
    assert "YAML syntax error" in problems
    assert "line" in problems and "column" in problems


# --------------------------------------------------------------------------- #
# Negative: missing required field names the path.
# --------------------------------------------------------------------------- #
def test_missing_required_field_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "provider.yaml",
        """
provider:
  id: cloudflare
  official_domains: [cloudflare.com]
sources:
  - id: cloudflare-changelog
    type: rss
    trust_level: official
    url: https://developers.cloudflare.com/changelog/
    schedule_ref: rss
publishing:
  automatic_threshold: 0.90
  uncertain_threshold: 0.70
  require_official_source: true
  require_deterministic_numeric_validation: true
""",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)
    problems = "\n".join(excinfo.value.problems)
    assert "provider.name" in problems
    assert "type=missing" in problems


# --------------------------------------------------------------------------- #
# Negative: bad cron and threshold ordering are rejected.
# --------------------------------------------------------------------------- #
def test_bad_cron_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "schedules.yaml",
        """
schedules:
  rss:
    cron: "not a cron"
  structured_apis:
    cron: "23 */6 * * *"
  mcp_documentation:
    cron: "35 3 * * *"
  official_pages:
    cron: "15 4 * * *"
  full_reconciliation:
    cron: "0 5 * * 0"
  conflict_recheck:
    delay_minutes: 15
    maximum_attempts: 3
""",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)
    assert "cron" in "\n".join(excinfo.value.problems)


def test_threshold_ordering_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "provider.yaml",
        """
provider:
  id: cloudflare
  name: Cloudflare
  official_domains: [cloudflare.com]
sources:
  - id: cloudflare-changelog
    type: rss
    trust_level: official
    url: https://developers.cloudflare.com/changelog/
    schedule_ref: rss
publishing:
  automatic_threshold: 0.50
  uncertain_threshold: 0.70
  require_official_source: true
  require_deterministic_numeric_validation: true
""",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)
    assert "automatic_threshold" in "\n".join(excinfo.value.problems)


# --- service_categories (F008 slice S1) -------------------------------------


def _coverage_block(
    *,
    states: dict[str, str] | None = None,
    provenance: dict[str, str] | None = None,
    rationales: dict[str, str] | None = None,
    omit: set[str] | None = None,
    extra: dict[str, str] | None = None,
) -> str:
    """Render a `coverage:` block.

    Defaults to a minimal *valid* block: three evidence-backed entries (the Q9-A
    floor) and eleven honest ``unknown``s. Every keyword narrows or breaks it so
    a test can pin exactly one failure mode.
    """

    if states is None:
        states = {
            "serverless-functions": "verified_free",
            "containers-app-hosting": "verified_free",
            "networking-cdn-dns": "offered_no_z0",
        }
    provenance = (
        provenance
        if provenance is not None
        else {
            "serverless-functions": "source: cloudflare-changelog",
            "containers-app-hosting": "source: cloudflare-changelog",
            "networking-cdn-dns": "evidence_url: https://www.cloudflare.com/plans/",
        }
    )
    rationales = rationales or {}
    omit = omit or set()

    lines = ["coverage:"]
    for slug in canonical_slugs():
        if slug in omit:
            continue
        lines.append(f"  {slug}:")
        lines.append(f"    state: {states.get(slug, 'unknown')}")
        if slug in provenance:
            lines.append(f"    {provenance[slug]}")
        if slug in rationales:
            lines.append(f"    rationale: {rationales[slug]}")
    for slug, state in (extra or {}).items():
        lines.append(f"  {slug}:")
        lines.append(f"    state: {state}")
    return "\n".join(lines) + "\n"


def _provider_yaml(service_categories_block: str, coverage_block: str | None = None) -> str:
    return (
        """
provider:
  id: cloudflare
  name: Cloudflare
  official_domains: [cloudflare.com]
sources:
  - id: cloudflare-changelog
    type: rss
    trust_level: official
    url: https://developers.cloudflare.com/changelog/
    schedule_ref: rss
publishing:
  automatic_threshold: 0.90
  uncertain_threshold: 0.70
  require_official_source: true
  require_deterministic_numeric_validation: true
"""
        + (coverage_block if coverage_block is not None else _coverage_block())
        + service_categories_block
    )


def test_service_categories_accepts_canonical_slugs(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "provider.yaml",
        _provider_yaml(
            """service_categories:
  Cloudflare Workers: serverless-functions
  Cloudflare R2: object-file-storage
"""
        ),
    )
    model = load_and_validate(path)
    assert model.service_categories == {
        "Cloudflare Workers": "serverless-functions",
        "Cloudflare R2": "object-file-storage",
    }


def test_service_categories_defaults_to_empty_when_absent(tmp_path: Path) -> None:
    path = _write(tmp_path, "provider.yaml", _provider_yaml(""))
    assert load_and_validate(path).service_categories == {}


def test_service_categories_rejects_unknown_slug(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "provider.yaml",
        _provider_yaml(
            """service_categories:
  Cloudflare Workers: serverless
"""
        ),
    )
    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)
    problems = "\n".join(excinfo.value.problems)
    # Actionable: names the service, the bad slug, and points at the valid set.
    assert "Cloudflare Workers" in problems
    assert "serverless" in problems
    assert "serverless-functions" in problems


def test_service_categories_rejects_blank_service_name(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "provider.yaml",
        _provider_yaml(
            """service_categories:
  "   ": serverless-functions
"""
        ),
    )
    with pytest.raises(ConfigError):
        load_and_validate(path)


def test_service_categories_unknown_service_name_is_accepted(tmp_path: Path) -> None:
    """A service that does not exist yet is a no-op, not a validation error.

    Config is declared ahead of ingestion; the mapping is applied by slug when
    (and if) the service appears.
    """

    path = _write(
        tmp_path,
        "provider.yaml",
        _provider_yaml(
            """service_categories:
  Totally Unshipped Service: ai-inference-embeddings
"""
        ),
    )
    model = load_and_validate(path)
    assert model.service_categories == {"Totally Unshipped Service": "ai-inference-embeddings"}


# --- coverage (F008 slice S2) ------------------------------------------------


def _load_coverage(tmp_path: Path, coverage_block: str) -> object:
    path = _write(tmp_path, "provider.yaml", _provider_yaml("", coverage_block))
    return load_and_validate(path)


def _coverage_problems(tmp_path: Path, coverage_block: str) -> str:
    path = _write(tmp_path, "provider.yaml", _provider_yaml("", coverage_block))
    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)
    return "\n".join(excinfo.value.problems)


def test_coverage_happy_path_declares_all_fourteen(tmp_path: Path) -> None:
    model = _load_coverage(tmp_path, _coverage_block())
    assert set(model.coverage) == set(canonical_slugs())
    assert len(model.coverage) == 14
    assert model.coverage["serverless-functions"].state == "verified_free"
    assert model.coverage["relational-databases"].state == "unknown"


def test_coverage_is_mandatory(tmp_path: Path) -> None:
    """A provider file with no coverage block at all must not load."""

    path = _write(tmp_path, "provider.yaml", _provider_yaml("", ""))
    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)
    assert "coverage" in "\n".join(excinfo.value.problems)


def test_coverage_rejects_missing_slug(tmp_path: Path) -> None:
    problems = _coverage_problems(
        tmp_path, _coverage_block(omit={"object-file-storage", "auth-identity"})
    )
    # Actionable: names exactly which slugs are missing.
    assert "object-file-storage" in problems
    assert "auth-identity" in problems
    assert "missing" in problems


def test_coverage_rejects_unknown_slug(tmp_path: Path) -> None:
    problems = _coverage_problems(tmp_path, _coverage_block(extra={"quantum-computers": "unknown"}))
    assert "quantum-computers" in problems
    assert "serverless-functions" in problems  # points at the valid set


def test_coverage_rejects_not_offered_without_rationale(tmp_path: Path) -> None:
    block = _coverage_block(
        states={
            "serverless-functions": "verified_free",
            "containers-app-hosting": "verified_free",
            "networking-cdn-dns": "offered_no_z0",
            "compute-vms": "not_offered",
        }
    )
    problems = _coverage_problems(tmp_path, block)
    assert "compute-vms" in problems
    assert "rationale" in problems


def test_coverage_accepts_not_offered_with_rationale(tmp_path: Path) -> None:
    block = _coverage_block(
        states={
            "serverless-functions": "verified_free",
            "containers-app-hosting": "verified_free",
            "networking-cdn-dns": "offered_no_z0",
            "compute-vms": "not_offered",
        },
        rationales={"compute-vms": "Cloudflare publishes no general-purpose VM product."},
    )
    model = _load_coverage(tmp_path, block)
    assert model.coverage["compute-vms"].state == "not_offered"


def test_coverage_rejects_verified_free_without_provenance(tmp_path: Path) -> None:
    block = _coverage_block(
        provenance={
            "containers-app-hosting": "source: cloudflare-changelog",
            "networking-cdn-dns": "evidence_url: https://www.cloudflare.com/plans/",
        }
    )
    problems = _coverage_problems(tmp_path, block)
    assert "serverless-functions" in problems
    assert "evidence_url" in problems or "source" in problems


def test_coverage_rejects_undeclared_source_reference(tmp_path: Path) -> None:
    block = _coverage_block(
        provenance={
            "serverless-functions": "source: some-other-providers-source",
            "containers-app-hosting": "source: cloudflare-changelog",
            "networking-cdn-dns": "evidence_url: https://www.cloudflare.com/plans/",
        }
    )
    problems = _coverage_problems(tmp_path, block)
    assert "some-other-providers-source" in problems


# --- Q9-A evidence floor -----------------------------------------------------


def test_coverage_floor_rejects_all_unknown_block(tmp_path: Path) -> None:
    """Fourteen honest 'unknown's is a placeholder, not a catalogue entry.

    This must be a HARD load failure, not a warning: a provider nobody has
    verified anything about must be unable to reach the catalogue at all.
    """

    problems = _coverage_problems(tmp_path, _coverage_block(states={}, provenance={}))
    assert "at least 3" in problems
    assert "only 0" in problems


def test_coverage_floor_rejects_two_evidence_backed_entries(tmp_path: Path) -> None:
    block = _coverage_block(
        states={
            "serverless-functions": "verified_free",
            "containers-app-hosting": "verified_free",
        },
        provenance={
            "serverless-functions": "source: cloudflare-changelog",
            "containers-app-hosting": "source: cloudflare-changelog",
        },
    )
    problems = _coverage_problems(tmp_path, block)
    assert "only 2" in problems
    assert "1 more needed" in problems


def test_coverage_floor_passes_with_three_evidence_backed_entries(tmp_path: Path) -> None:
    model = _load_coverage(tmp_path, _coverage_block())
    backed = [
        slug
        for slug, entry in model.coverage.items()
        if entry.state in ("verified_free", "offered_no_z0") and entry.has_provenance
    ]
    assert len(backed) == 3


def test_coverage_floor_ignores_unbacked_claims(tmp_path: Path) -> None:
    """Three verified_free entries with no provenance cannot satisfy the floor.

    They cannot even load -- the provenance rule fires first -- which is the
    point: an unsupported free claim never counts as coverage.
    """

    block = _coverage_block(
        states={
            "serverless-functions": "verified_free",
            "containers-app-hosting": "verified_free",
            "networking-cdn-dns": "verified_free",
        },
        provenance={},
    )
    problems = _coverage_problems(tmp_path, block)
    assert "without provenance" in problems


def test_shipped_cloudflare_config_satisfies_the_floor() -> None:
    model = load_and_validate(EXAMPLES / "providers" / "cloudflare.example.yaml")
    assert set(model.coverage) == set(canonical_slugs())
    backed = [
        slug
        for slug, entry in model.coverage.items()
        if entry.state in ("verified_free", "offered_no_z0") and entry.has_provenance
    ]
    assert len(backed) >= MIN_EVIDENCE_BACKED_COVERAGE


def test_the_load_time_and_post_sync_floors_cannot_drift_apart() -> None:
    """Q9-A is enforced twice; both sites must share one definition.

    ``ProviderConfig.validate_coverage_floor`` guards the config file and
    ``app.ingest.config_sync._assert_persisted_coverage_floor`` guards the rows
    that actually landed in the database. If the two ever disagreed about the
    threshold or about which states count as evidence-backed, one of them would
    silently stop protecting anything.
    """

    import app.config.models as config_models
    import app.ingest.config_sync as config_sync

    assert config_sync.MIN_EVIDENCE_BACKED_COVERAGE is config_models.MIN_EVIDENCE_BACKED_COVERAGE
    assert config_sync.EVIDENCE_BACKED_COVERAGE_STATES is EVIDENCE_BACKED_COVERAGE_STATES

    source = inspect.getsource(config_sync._assert_persisted_coverage_floor)
    assert "MIN_EVIDENCE_BACKED_COVERAGE" in source, (
        "the persisted-row floor must use the shared threshold, not a literal"
    )
    assert "EVIDENCE_BACKED_COVERAGE_STATES" in source, (
        "the persisted-row floor must use the shared evidence-backed state set"
    )
    # A hard failure, not a note: silence is what let the catalogue erode.
    assert issubclass(config_sync.CoverageFloorError, ValueError)
    assert "raise CoverageFloorError" in source


def test_mcp_source_requires_capabilities(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "provider.yaml",
        """
provider:
  id: cloudflare
  name: Cloudflare
  official_domains: [cloudflare.com]
sources:
  - id: cloudflare-docs-mcp
    type: mcp
    trust_level: official
    schedule_ref: mcp_documentation
publishing:
  automatic_threshold: 0.90
  uncertain_threshold: 0.70
  require_official_source: true
  require_deterministic_numeric_validation: true
""",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)
    assert "capabilities" in "\n".join(excinfo.value.problems)


# --------------------------------------------------------------------------- #
# Security: inline secrets are rejected; only *_env references are allowed.
# --------------------------------------------------------------------------- #
def test_inline_secret_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "llm.yaml",
        """
llm:
  mode: hybrid
  public_adviser:
    ai_requests_per_ip_per_day: 3
    deterministic_requests_per_ip_per_day: 10
    concurrent_requests_per_session: 1
    maximum_input_characters: 2000
    maximum_output_tokens: 4000
    require_captcha: true
    reject_urls: true
    allow_file_uploads: false
    fallback_to_deterministic: true
  providers:
    gemini:
      enabled: true
      api_key: sk-should-never-be-here
""",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)
    problems = "\n".join(excinfo.value.problems)
    assert "inline secret" in problems
    assert "api_key_env" in problems


def test_env_reference_must_look_like_env_name(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "llm.yaml",
        """
llm:
  mode: hybrid
  public_adviser:
    ai_requests_per_ip_per_day: 3
    deterministic_requests_per_ip_per_day: 10
    concurrent_requests_per_session: 1
    maximum_input_characters: 2000
    maximum_output_tokens: 4000
    require_captcha: true
    reject_urls: true
    allow_file_uploads: false
    fallback_to_deterministic: true
  providers:
    gemini:
      enabled: true
      api_key_env: not-an-env-name
""",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)
    assert "api_key_env" in "\n".join(excinfo.value.problems)


# --------------------------------------------------------------------------- #
# Loader edge cases.
# --------------------------------------------------------------------------- #
def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(tmp_path / "does-not-exist.yaml")
    assert "file not found" in "\n".join(excinfo.value.problems)


def test_unrecognised_family_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "mystery.yaml", "unknown_root:\n  a: 1\n")
    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)
    assert "family" in "\n".join(excinfo.value.problems)


# --------------------------------------------------------------------------- #
# JSON Schema export.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("family", sorted(FAMILY_MODELS))
def test_emit_schema_is_valid_json_schema(family: str) -> None:
    schema = FAMILY_MODELS[family].model_json_schema()
    assert schema["type"] == "object"
    assert "properties" in schema
    # Round-trips through JSON without error.
    json.dumps(schema)


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #
def test_cli_validate_success_on_examples() -> None:
    files = [str(p) for p in _example_files()]
    assert cli_main(["validate", *files]) == 0


def test_cli_validate_failure_on_bad_file(tmp_path: Path) -> None:
    path = _write(tmp_path, "mystery.yaml", "unknown_root: 1\n")
    assert cli_main(["validate", str(path)]) == 1


def test_cli_emit_schema_success(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["emit-schema", "application"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["type"] == "object"
