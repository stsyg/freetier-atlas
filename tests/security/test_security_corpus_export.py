"""S3 -- deployment ZIP export: fail-closed validator regression corpus.

Consolidates and extends the F007 slice-3 guarantees (docs/SECURITY_PRIVACY_ABUSE
"ZIPs: browser only"): every generated file is validated before it is returned,
``build_export`` is a pure function that persists nothing, and the output is
secret-free. These drive the standalone pure validators against crafted-bad
inputs (path traversal, non-allowlisted paths, invalid / incomplete Compose,
oversized bundles, NUL / control bytes, and a battery of planted secrets) plus
the HTTP layer's fail-closed 422 mapping.

All planted secrets are assembled from fragments at runtime so no contiguous
secret literal is ever committed (keeps the detect-secrets gate green).
"""

from __future__ import annotations

import builtins

import pytest
from app.adviser.export import (
    ALLOWED_PATHS,
    MAX_FILE_BYTES,
    MAX_FILES,
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
from app.main import app
from fastapi.testclient import TestClient

from tests.support.synthetic import build_catalogue

# --------------------------------------------------------------------------- #
# Deterministic result + valid-Compose fixtures.                              #
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


# --------------------------------------------------------------------------- #
# Path validation: absolute / traversal / backslash / off-allowlist rejected. #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_path",
    [
        "/etc/passwd",  # absolute (posix)
        "C:/secrets.txt",  # absolute (windows drive)
        "../docker-compose.yml",  # parent traversal
        "sub/../../escape.md",  # embedded traversal
        "config\\evil.yml",  # backslash separator
        "docker-compose.yml/../../x",  # deep + traversal
    ],
)
def test_validate_path_rejects_unsafe_paths(bad_path: str) -> None:
    with pytest.raises(ExportValidationError):
        validate_path(bad_path)


@pytest.mark.parametrize("bad_path", ["evil.sh", "compose.yaml", "secrets.env", "index.html"])
def test_validate_path_rejects_non_allowlisted_names(bad_path: str) -> None:
    assert bad_path not in ALLOWED_PATHS
    with pytest.raises(ExportValidationError):
        validate_path(bad_path)


def test_validate_path_accepts_every_allowlisted_name() -> None:
    for good in ALLOWED_PATHS:
        validate_path(good)  # must not raise


# --------------------------------------------------------------------------- #
# Text validation: NUL and control bytes rejected.                            #
# --------------------------------------------------------------------------- #


def test_validate_text_rejects_nul_byte() -> None:
    with pytest.raises(ExportValidationError):
        validate_text("README.md", "hello\x00world")


@pytest.mark.parametrize("ctrl", ["\x01", "\x07", "\x1f", "\x0b"])
def test_validate_text_rejects_control_bytes(ctrl: str) -> None:
    with pytest.raises(ExportValidationError):
        validate_text("README.md", f"line{ctrl}break")


def test_validate_text_allows_ordinary_whitespace() -> None:
    validate_text("README.md", "tab\tnewline\r\nok")  # must not raise


# --------------------------------------------------------------------------- #
# Secret scan: planted credentials (fragment-assembled) are all rejected.     #
# --------------------------------------------------------------------------- #


def _planted_secrets() -> dict[str, str]:
    """Assemble each planted secret from fragments (no committed literal)."""

    a32 = "A" * 32
    return {
        "aws_access_key": "AKIA" + "IOSFODNN7EXAMPLE",
        "github_pat": "gh" + "p_" + a32,
        "slack_bot_token": "xox" + "b-" + ("1" * 12) + "-" + a32,
        "openai_key": "sk-" + a32,
        "gcp_cred": "AIza" + ("B" * 35),
        "private_pem_block": "-----BEGIN RSA "
        + "PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----",
        "kw_assigned": "pass" + "word = " + '"' + "s3cr3t" + "LongValue123" + '"',
    }


@pytest.mark.parametrize("label,payload", list(_planted_secrets().items()))
def test_scan_secrets_flags_planted_credentials(label: str, payload: str) -> None:
    findings = scan_secrets(payload)
    assert findings, f"{label}: planted secret was not detected"


@pytest.mark.parametrize("label,payload", list(_planted_secrets().items()))
def test_validate_bundle_rejects_a_planted_secret(label: str, payload: str) -> None:
    files = [
        ("docker-compose.yml", _valid_compose()),
        ("README.md", f"see config\n{payload}\n"),
    ]
    with pytest.raises(ExportValidationError):
        validate_bundle(files)


def test_scan_secrets_ignores_env_example_placeholders() -> None:
    env = (
        "APP_PORT=8080\n"
        "POSTGRES_USER=atlas\n"
        "POSTGRES_PASSWORD=REPLACE_ME\n"
        "API_TOKEN=<your-token-here>\n"
    )
    assert scan_secrets(env) == []


@pytest.mark.parametrize("value", ["REPLACE_ME", "changeme", "<your-token>", "$SECRET", "example"])
def test_is_placeholder_recognises_non_secrets(value: str) -> None:
    assert is_placeholder(value) is True


# --------------------------------------------------------------------------- #
# Compose validation: invalid YAML + every structural guarantee.              #
# --------------------------------------------------------------------------- #


def test_validate_compose_rejects_invalid_yaml() -> None:
    with pytest.raises(ExportValidationError):
        validate_compose("services: [unbalanced\n  : :\n")


def test_validate_compose_rejects_non_mapping() -> None:
    with pytest.raises(ExportValidationError):
        validate_compose("- just\n- a\n- list\n")


def test_validate_compose_rejects_empty_services() -> None:
    with pytest.raises(ExportValidationError):
        validate_compose("services: {}\nx-freetier-atlas:\n  supported_platforms: [a]\n")


def test_validate_compose_rejects_missing_healthcheck() -> None:
    doc = (
        "services:\n"
        "  app:\n"
        "    image: nginx:1.27-alpine\n"
        "x-freetier-atlas:\n"
        "  supported_platforms: [linux/amd64, linux/arm64]\n"
    )
    with pytest.raises(ExportValidationError):
        validate_compose(doc)


def test_validate_compose_rejects_empty_healthcheck_test_list() -> None:
    doc = (
        "services:\n"
        "  app:\n"
        "    image: nginx:1.27-alpine\n"
        "    healthcheck:\n"
        "      test: []\n"
        "x-freetier-atlas:\n"
        "  supported_platforms: [linux/amd64, linux/arm64]\n"
    )
    with pytest.raises(ExportValidationError):
        validate_compose(doc)


def test_validate_compose_rejects_non_allowlisted_image() -> None:
    doc = _valid_compose().replace("nginx:1.27-alpine", "sketchy/image:latest")
    with pytest.raises(ExportValidationError):
        validate_compose(doc)


def test_validate_compose_rejects_missing_amd64_platform() -> None:
    doc = (
        "services:\n"
        "  app:\n"
        "    image: nginx:1.27-alpine\n"
        "    healthcheck:\n"
        "      test: [CMD, true]\n"
        "x-freetier-atlas:\n"
        "  supported_platforms: [linux/arm64]\n"
    )
    with pytest.raises(ExportValidationError):
        validate_compose(doc)


def test_validate_compose_rejects_missing_meta_block() -> None:
    doc = (
        "services:\n"
        "  app:\n"
        "    image: nginx:1.27-alpine\n"
        "    healthcheck:\n"
        "      test: [CMD, true]\n"
    )
    with pytest.raises(ExportValidationError):
        validate_compose(doc)


def test_validate_compose_accepts_a_valid_multi_arch_document() -> None:
    report = validate_compose(_valid_compose())
    assert report["multi_arch"] is True


# --------------------------------------------------------------------------- #
# Size + structural bundle bounds.                                            #
# --------------------------------------------------------------------------- #


def test_enforce_size_rejects_oversized_single_file() -> None:
    files = [("README.md", "x" * (MAX_FILE_BYTES + 1))]
    with pytest.raises(ExportValidationError):
        enforce_size(files)


def test_enforce_size_rejects_oversized_total() -> None:
    chunk = "y" * (MAX_FILE_BYTES - 1)
    files = [("README.md", chunk) for _ in range((MAX_TOTAL_BYTES // len(chunk)) + 2)]
    with pytest.raises(ExportValidationError):
        enforce_size(files)


def test_enforce_size_rejects_too_many_files() -> None:
    files = [("README.md", "ok") for _ in range(MAX_FILES + 1)]
    with pytest.raises(ExportValidationError):
        enforce_size(files)


def test_validate_bundle_rejects_missing_compose() -> None:
    with pytest.raises(ExportValidationError):
        validate_bundle([("README.md", "no compose here")])


def test_validate_bundle_rejects_duplicate_path() -> None:
    files = [
        ("docker-compose.yml", _valid_compose()),
        ("README.md", "first"),
        ("README.md", "second"),
    ]
    with pytest.raises(ExportValidationError):
        validate_bundle(files)


# --------------------------------------------------------------------------- #
# build_export: secret-free output, deterministic, and persists nothing.      #
# --------------------------------------------------------------------------- #


def test_build_export_output_is_secret_free_and_valid() -> None:
    export = build_export(_result())
    for f in export.files:
        assert scan_secrets(f.content) == [], f"{f.path}: unexpected secret finding"
    # The four fixed files must be exactly the allowlist and self-validate.
    assert {f.path for f in export.files} == set(ALLOWED_PATHS)
    validate_bundle([(f.path, f.content) for f in export.files if f.path != "MANIFEST.json"])


def test_build_export_persists_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_export must never open a file for writing (pure, no I/O)."""

    real_open = builtins.open
    write_calls: list[tuple[str, str]] = []

    def _spy_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            write_calls.append((str(file), mode))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _spy_open)
    build_export(_result())
    assert write_calls == []


# --------------------------------------------------------------------------- #
# HTTP layer: a validation failure fails closed as 422 (no content echoed).   #
# --------------------------------------------------------------------------- #


def _export_body() -> dict[str, object]:
    return {
        "workload_name": "demo app",
        "requirements": [
            {
                "category": "relational-databases",
                "demands": [{"metric": "storage", "amount": "5", "unit": "GB", "period": "month"}],
            }
        ],
    }


def test_export_endpoint_maps_validation_error_to_422(monkeypatch: pytest.MonkeyPatch) -> None:
    router = __import__("app.adviser.router", fromlist=["*"])
    from app.db import get_session

    monkeypatch.setattr(router, "_enforce_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(router, "gather_candidates", lambda session, **_kw: ())
    monkeypatch.setattr(router, "recommend", lambda request, pool: object())

    def _boom(result):
        raise ExportValidationError("planted traversal path")

    monkeypatch.setattr(router, "build_export", _boom)
    app.dependency_overrides[get_session] = lambda: None
    try:
        client = TestClient(app)
        resp = client.post("/adviser/export", json=_export_body())
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "rejected" in detail.lower()
