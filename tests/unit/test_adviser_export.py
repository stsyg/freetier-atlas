"""Unit tests for the non-persisted, fail-closed deployment-bundle generator.

These exercise the pure validators against crafted-bad inputs (planted secret,
path traversal, oversized, missing healthcheck, invalid YAML, non-multi-arch
image) plus the happy path of :func:`build_export` (manifest validity,
deterministic hashes) and a probe proving nothing is written to disk.

Planted secrets are assembled from fragments at runtime so no contiguous secret
literal ever appears in a committed file (keeps the detect-secrets gate green).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from app.adviser.export import (
    ALLOWED_PATHS,
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    ExportValidationError,
    build_export,
    enforce_size,
    is_placeholder,
    scan_secrets,
    validate_bundle,
    validate_compose,
    validate_path,
    validate_text,
)
from app.adviser.recommend import recommend
from app.adviser.schema import RecommendationRequest

from tests.support.synthetic import build_catalogue

# --------------------------------------------------------------------------- #
# Fixtures: deterministic recommendation results from a synthetic catalogue.  #
# --------------------------------------------------------------------------- #


def _pool(category: str = "relational-databases"):
    return build_catalogue(
        {
            "providers": [
                {
                    "id": 1,
                    "slug": "acme",
                    "name": "Acme",
                    "services": [
                        {
                            "id": 10,
                            "canonical_name": "acme svc",
                            "category_slug": category,
                            "deployment_model": "managed",
                            "portability_traits": ["open_source"],
                            "offers": [
                                {
                                    "id": 100,
                                    "offer_type": "recurring_quota",
                                    "zero_cost_class": "Z0_TRUE_FREE",
                                    "commercial_use_allowed": True,
                                    "personal_use_allowed": True,
                                    "requires_card": False,
                                    "has_paid_dependencies": False,
                                    "version": {
                                        "material_facts": {"confidence": 0.95},
                                        "quotas": [
                                            {
                                                "metric": "storage",
                                                "amount": "10",
                                                "unit": "GB",
                                                "reset_period": "month",
                                                "exhaustion_behaviour": "hard_stop",
                                            }
                                        ],
                                    },
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    ).pool()


def _result(category: str = "relational-databases"):
    request = RecommendationRequest(
        workload_name="demo app",
        requirements=[
            {
                "category": category,
                "demands": [{"metric": "storage", "amount": "5", "unit": "GB", "period": "month"}],
            }
        ],
    )
    return recommend(request, _pool(category))


# --------------------------------------------------------------------------- #
# Happy path: build_export produces a validated, secret-free, complete bundle. #
# --------------------------------------------------------------------------- #


def test_build_export_returns_the_four_fixed_files() -> None:
    export = build_export(_result())
    paths = [f.path for f in export.files]
    assert paths == ["docker-compose.yml", ".env.example", "README.md", "MANIFEST.json"]
    assert set(paths) <= ALLOWED_PATHS
    assert export.fully_zero_cost is True


def test_build_export_is_deterministic_and_hashes_match() -> None:
    first = build_export(_result())
    second = build_export(_result())
    assert first.model_dump() == second.model_dump()
    import hashlib

    for f in first.files:
        assert f.sha256 == hashlib.sha256(f.content.encode("utf-8")).hexdigest()
        assert f.size == len(f.content.encode("utf-8"))


def test_manifest_reports_validation_and_platforms() -> None:
    manifest = build_export(_result()).manifest
    assert manifest.platforms == ["linux/amd64", "linux/arm64"]
    assert all(manifest.validation.values())
    assert manifest.file_count == 3  # the three content files (manifest excluded)
    assert manifest.total_bytes <= MAX_TOTAL_BYTES
    assert manifest.architecture[0]["offer_id"] == 100


def test_generated_compose_parses_with_healthchecks_and_dual_arch() -> None:
    export = build_export(_result())
    compose = next(f.content for f in export.files if f.path == "docker-compose.yml")
    report = validate_compose(compose)
    assert report["healthchecks_present"] is True
    assert report["multi_arch"] is True


def test_no_generated_file_contains_a_secret() -> None:
    export = build_export(_result())
    for f in export.files:
        assert scan_secrets(f.content) == []


def test_env_example_only_contains_placeholders() -> None:
    export = build_export(_result())
    env = next(f.content for f in export.files if f.path == ".env.example")
    # No secret-like material anywhere in the example env file.
    assert scan_secrets(env) == []
    # Any secret-bearing key must use a placeholder value, never a real secret.
    secretish = ("password", "secret", "token", "key")
    for line in env.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if any(word in name.lower() for word in secretish):
            assert is_placeholder(value), f"non-placeholder secret value: {line!r}"


# --------------------------------------------------------------------------- #
# NOT-persisted probe: build_export writes nothing to disk.                    #
# --------------------------------------------------------------------------- #


def test_build_export_writes_nothing_to_disk(tmp_path: Path) -> None:
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        before = set(tmp_path.rglob("*"))
        export = build_export(_result())
        after = set(tmp_path.rglob("*"))
    finally:
        os.chdir(cwd)
    assert before == after, "build_export must not create any file on disk"
    assert export.files, "sanity: the bundle is still produced in-memory"


# --------------------------------------------------------------------------- #
# Fail-closed validators: crafted-bad inputs are rejected.                     #
# --------------------------------------------------------------------------- #


def test_secret_scan_rejects_a_planted_aws_key() -> None:
    planted = "AKIA" + "IOSFODNN7EXAMPLE"  # assembled so no literal lives in source
    assert scan_secrets(f"aws_key={planted}") != []


def test_secret_scan_rejects_a_planted_token() -> None:
    planted = "ghp_" + ("a" * 32)
    assert scan_secrets(planted) != []


def test_secret_scan_rejects_a_planted_private_key_header() -> None:
    header = "-----BEGIN " + "RSA PRIVATE KEY-----"
    assert scan_secrets(header) != []


def test_secret_scan_rejects_keyword_assigned_real_value() -> None:
    assert scan_secrets("password = " + "hunter2hunter2") != []


def test_secret_scan_allows_placeholder_assignments() -> None:
    assert scan_secrets("POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-REPLACE_ME}") == []
    assert scan_secrets("api_key=<your-key-here>") == []


def test_validate_bundle_rejects_a_planted_secret() -> None:
    planted = "AKIA" + "IOSFODNN7EXAMPLE"
    files = [
        ("docker-compose.yml", _valid_compose()),
        (".env.example", f"AWS_ACCESS_KEY_ID={planted}"),
    ]
    with pytest.raises(ExportValidationError, match="secret-like"):
        validate_bundle(files)


def test_validate_path_rejects_traversal() -> None:
    with pytest.raises(ExportValidationError):
        validate_path("../etc/passwd")


def test_validate_path_rejects_absolute_and_backslash() -> None:
    with pytest.raises(ExportValidationError):
        validate_path("/etc/hosts")
    with pytest.raises(ExportValidationError):
        validate_path("C:/Windows/system32")
    with pytest.raises(ExportValidationError):
        validate_path("dir\\file")


def test_validate_path_rejects_non_allowlisted_name() -> None:
    with pytest.raises(ExportValidationError, match="allowlist"):
        validate_path("Makefile")


def test_validate_path_accepts_the_allowlisted_paths() -> None:
    for path in ALLOWED_PATHS:
        validate_path(path)  # must not raise


def test_validate_text_rejects_nul_and_control_bytes() -> None:
    with pytest.raises(ExportValidationError):
        validate_text("x", "abc\x00def")
    with pytest.raises(ExportValidationError):
        validate_text("x", "abc\x07def")


def test_enforce_size_rejects_oversized_file() -> None:
    huge = "a" * (MAX_FILE_BYTES + 1)
    with pytest.raises(ExportValidationError, match="cap"):
        enforce_size([("README.md", huge)])


def test_enforce_size_rejects_oversized_total() -> None:
    chunk = "a" * (MAX_FILE_BYTES - 1)
    files = [("README.md", chunk), ("MANIFEST.json", chunk), (".env.example", chunk)]
    with pytest.raises(ExportValidationError, match="total"):
        enforce_size(files)


def test_validate_compose_rejects_invalid_yaml() -> None:
    with pytest.raises(ExportValidationError):
        validate_compose("services: [: :\n  - bad")


def test_validate_compose_rejects_missing_healthcheck() -> None:
    compose = (
        "services:\n"
        "  app:\n"
        "    image: nginx:1.27-alpine\n"
        "x-freetier-atlas:\n"
        "  supported_platforms: [linux/amd64, linux/arm64]\n"
    )
    with pytest.raises(ExportValidationError, match="healthcheck"):
        validate_compose(compose)


def test_validate_compose_rejects_non_multi_arch_image() -> None:
    compose = (
        "services:\n"
        "  app:\n"
        "    image: some/random-image:latest\n"
        "    healthcheck:\n"
        "      test: [CMD, true]\n"
        "x-freetier-atlas:\n"
        "  supported_platforms: [linux/amd64, linux/arm64]\n"
    )
    with pytest.raises(ExportValidationError, match="multi-arch"):
        validate_compose(compose)


def test_validate_compose_rejects_missing_arch_assertion() -> None:
    compose = (
        "services:\n"
        "  app:\n"
        "    image: nginx:1.27-alpine\n"
        "    healthcheck:\n"
        "      test: [CMD, true]\n"
        "x-freetier-atlas:\n"
        "  supported_platforms: [linux/amd64]\n"
    )
    with pytest.raises(ExportValidationError, match="arm64"):
        validate_compose(compose)


def _valid_compose() -> str:
    return (
        "services:\n"
        "  app:\n"
        "    image: nginx:1.27-alpine\n"
        "    healthcheck:\n"
        "      test: [CMD, true]\n"
        "x-freetier-atlas:\n"
        "  supported_platforms: [linux/amd64, linux/arm64]\n"
    )
