"""Unit tests for the declarative configuration system (F003 slice 1).

Cover the good example files, unknown-field rejection, malformed YAML, missing
required fields, inline-secret rejection, environment-variable-name references,
JSON Schema export, and the CLI. All tests run offline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.config import ConfigError, load_and_validate
from app.config.cli import main as cli_main
from app.config.loader import detect_family
from app.config.models import FAMILY_MODELS

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
