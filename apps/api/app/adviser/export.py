"""Server-validated, non-persisted deployment-bundle generator (F007 slice 3).

Given a deterministic :class:`~app.adviser.recommend.RecommendationResult`, this
module generates a small deployment bundle -- a portable *local self-hosting
scaffold* consistent with the product's portability / exit-plan posture -- and
returns the file **contents** plus a generation **manifest** as an in-memory
:class:`ExportResponse`.

Load-bearing security posture (docs/SECURITY_PRIVACY_ABUSE.md "ZIPs: browser
only" + "ZIP security"):

* **Nothing is persisted.** :func:`build_export` is a pure function: it never
  opens a file for writing, never touches the database, and never caches. The
  content exists only in the returned object; the browser assembles the ``.zip``
  client-side.
* **Validated, fail closed.** Every generated file is validated before it is
  returned: fixed safe relative paths (no traversal, no absolute paths, no
  backslashes, allowlisted names), text-only (no binaries / NUL bytes), a secret
  scan that rejects any secret-like material (env files carry only
  ``.env.example`` placeholders), a ``docker-compose.yml`` that parses as YAML
  with a non-empty ``services`` map where **every** service declares a
  ``healthcheck`` and uses a **multi-arch** image (amd64 + arm64 asserted), and a
  total-size cap. Any violation raises :class:`ExportValidationError`.
* **No user-controlled URLs / no SSRF.** The generator consumes only the
  recommendation (itself computed from a structured request that already rejects
  URL-like input) and internal ids; no field is ever fetched.

The validators are exposed as standalone pure functions so they can be exercised
directly against crafted-bad inputs (a planted secret, a traversal path, invalid
YAML, an oversized bundle, a missing healthcheck) to prove they fail closed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import yaml
from pydantic import BaseModel

from . import explain
from .recommend import RecommendationResult

#: Human-readable generator identifier stamped into the manifest (no version
#: churn / no timestamp -> the whole bundle is deterministic for identical input).
GENERATOR = "freetier-atlas-deployment-export/1"

#: The platforms every generated bundle asserts support for.
SUPPORTED_PLATFORMS: tuple[str, ...] = ("linux/amd64", "linux/arm64")

# --- Bounds ----------------------------------------------------------------- #
MAX_TOTAL_BYTES = 262_144  # 256 KiB across the whole bundle
MAX_FILE_BYTES = 131_072  # 128 KiB for any single file
MAX_FILES = 12
MAX_PATH_LENGTH = 120
MAX_PATH_DEPTH = 4

#: The only relative paths a generated bundle may contain (fixed + safe).
ALLOWED_PATHS: frozenset[str] = frozenset(
    {"docker-compose.yml", ".env.example", "README.md", "MANIFEST.json"}
)

#: A safe relative path: segments of ``[A-Za-z0-9._-]`` joined by ``/`` only,
#: each segment non-empty and not a dot-segment. No leading slash, no backslash.
_SAFE_PATH_SEGMENT = re.compile(r"^\.?[A-Za-z0-9][A-Za-z0-9._-]*$")

#: Container images the generator may emit. Each is an official, **multi-arch**
#: image published for linux/amd64 **and** linux/arm64, so the generated Compose
#: runs on both architectures. Validation rejects any image outside this set.
MULTI_ARCH_IMAGES: frozenset[str] = frozenset(
    {"nginx:1.27-alpine", "postgres:16-alpine", "redis:7-alpine"}
)


@dataclass(frozen=True)
class _Container:
    """A deterministic local self-hosting stand-in for a recommended category."""

    service: str
    image: str
    healthcheck: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()


#: Category -> a portable, multi-arch, healthcheckable local stand-in. Categories
#: without a safe OSS container equivalent are documented in the README as
#: managed-only (never guessed into the Compose).
_CATEGORY_CONTAINERS: dict[str, _Container] = {
    "relational-databases": _Container(
        service="db",
        image="postgres:16-alpine",
        healthcheck=("CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-atlas}"),
        environment=(
            ("POSTGRES_USER", "${POSTGRES_USER:-atlas}"),
            ("POSTGRES_PASSWORD", "${POSTGRES_PASSWORD:-REPLACE_ME}"),
            ("POSTGRES_DB", "${POSTGRES_DB:-atlas}"),
        ),
    ),
    "nosql-key-value": _Container(
        service="kv",
        image="redis:7-alpine",
        healthcheck=("CMD", "redis-cli", "ping"),
    ),
    "queues-messaging-jobs": _Container(
        service="queue",
        image="redis:7-alpine",
        healthcheck=("CMD", "redis-cli", "ping"),
    ),
}

#: The always-present application tier of the scaffold.
_APP_CONTAINER = _Container(
    service="app",
    image="nginx:1.27-alpine",
    healthcheck=("CMD-SHELL", "wget -qO- http://localhost/ || exit 1"),
)


class ExportValidationError(ValueError):
    """A generated bundle failed a fail-closed validation check."""


# --------------------------------------------------------------------------- #
# Response models                                                             #
# --------------------------------------------------------------------------- #


class ExportFileOut(BaseModel):
    path: str
    content: str
    sha256: str
    size: int


class ManifestOut(BaseModel):
    schema_version: int = 1
    generator: str = GENERATOR
    workload_name: str | None = None
    fully_zero_cost: bool
    platforms: list[str] = []
    files: list[dict[str, object]] = []
    total_bytes: int
    file_count: int
    validation: dict[str, bool] = {}
    architecture: list[dict[str, object]] = []
    self_hosting_required: list[dict[str, object]] = []
    notes: list[str] = []


class ExportResponse(BaseModel):
    workload_name: str | None = None
    fully_zero_cost: bool
    files: list[ExportFileOut] = []
    manifest: ManifestOut


# --------------------------------------------------------------------------- #
# Validators (pure; fail closed)                                              #
# --------------------------------------------------------------------------- #

#: Values that are obviously non-secret placeholders and must never be flagged.
_PLACEHOLDER = re.compile(
    r"(?ix)"
    r"(replace[_-]?me|change[_-]?me|placeholder|example|your[_-].+|<[^>]+>"
    r"|x{3,}|todo|none|null|localhost|atlas)"
)

#: Secret-like patterns. Deliberately specific (known key formats + keyword
#: assignments of a non-placeholder value) so it catches planted secrets without
#: false-positiving on sha256 hashes or ``.env.example`` placeholders.
_AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")
_TOKEN_PREFIX = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|AIza[0-9A-Za-z_\-]{20,})"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")  # pragma: allowlist secret
_KEYWORD_ASSIGN = re.compile(
    r"""(?ix)
    (?:^|[^a-z0-9_])
    (password|passwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret|private[_-]?key)
    ["']?\s*[:=]\s*["']?
    ([^\s"']{8,})
    """
)


def is_placeholder(value: str) -> bool:
    """True when ``value`` is an obvious non-secret placeholder."""

    stripped = value.strip().strip("\"'")
    if not stripped:
        return True
    if stripped.startswith("$") or stripped.startswith("<"):
        return True
    return bool(_PLACEHOLDER.search(stripped))


def scan_secrets(content: str) -> list[str]:
    """Return a list of secret-like findings in ``content`` (empty when clean)."""

    findings: list[str] = []
    if _PRIVATE_KEY.search(content):
        findings.append("private key block")
    if _AWS_KEY.search(content):
        findings.append("AWS access key id")
    if _TOKEN_PREFIX.search(content):
        findings.append("credential-token literal")
    for match in _KEYWORD_ASSIGN.finditer(content):
        keyword, value = match.group(1), match.group(2)
        if not is_placeholder(value):
            findings.append(f"{keyword.lower()} assigned a non-placeholder value")
    return findings


def validate_text(path: str, content: str) -> None:
    """Reject binary / control content (only text bundles are produced)."""

    if "\x00" in content:
        raise ExportValidationError(f"{path}: binary/NUL content is not allowed.")
    for char in content:
        if char in "\t\n\r":
            continue
        if ord(char) < 0x20:
            raise ExportValidationError(f"{path}: control characters are not allowed.")


def validate_path(path: str) -> None:
    """Reject unsafe paths: absolute, traversal, backslash, or off the allowlist."""

    if not path or len(path) > MAX_PATH_LENGTH:
        raise ExportValidationError("Empty or over-long file path.")
    if "\\" in path:
        raise ExportValidationError(f"{path}: backslashes are not allowed in a path.")
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise ExportValidationError(f"{path}: absolute paths are not allowed.")
    segments = path.split("/")
    if len(segments) > MAX_PATH_DEPTH:
        raise ExportValidationError(f"{path}: path is too deeply nested.")
    for segment in segments:
        if segment in ("", ".", ".."):
            raise ExportValidationError(f"{path}: path traversal / empty segment is not allowed.")
        if not _SAFE_PATH_SEGMENT.match(segment):
            raise ExportValidationError(f"{path}: unsafe characters in path segment '{segment}'.")
    if path not in ALLOWED_PATHS:
        raise ExportValidationError(f"{path}: not an allowlisted bundle path.")


def enforce_size(files: Sequence[tuple[str, str]]) -> int:
    """Enforce per-file and total byte caps; return the total byte count."""

    if len(files) > MAX_FILES:
        raise ExportValidationError("Too many files in the bundle.")
    total = 0
    for path, content in files:
        size = len(content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise ExportValidationError(f"{path}: file exceeds the {MAX_FILE_BYTES}-byte cap.")
        total += size
    if total > MAX_TOTAL_BYTES:
        raise ExportValidationError(f"Bundle exceeds the {MAX_TOTAL_BYTES}-byte total cap.")
    return total


def validate_compose(content: str) -> dict[str, object]:
    """Parse + validate the Compose file; return a small validation report.

    Fails closed unless: it parses as a YAML mapping; ``services`` is a non-empty
    mapping; **every** service declares a ``healthcheck`` with a non-empty
    ``test``; every service ``image`` is on the multi-arch allowlist; and the
    top-level ``x-freetier-atlas.supported_platforms`` asserts amd64 **and**
    arm64.
    """

    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:  # pragma: no cover - message detail only
        raise ExportValidationError(f"docker-compose.yml is not valid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ExportValidationError("docker-compose.yml must be a YAML mapping.")

    services = parsed.get("services")
    if not isinstance(services, dict) or not services:
        raise ExportValidationError("docker-compose.yml must declare a non-empty 'services' map.")

    for name, service in services.items():
        if not isinstance(service, dict):
            raise ExportValidationError(f"service '{name}' must be a mapping.")
        image = service.get("image")
        if not isinstance(image, str) or image not in MULTI_ARCH_IMAGES:
            raise ExportValidationError(
                f"service '{name}' uses a non-allowlisted (or non-multi-arch) image."
            )
        healthcheck = service.get("healthcheck")
        if not isinstance(healthcheck, dict) or not healthcheck.get("test"):
            raise ExportValidationError(f"service '{name}' is missing a healthcheck.")

    meta = parsed.get("x-freetier-atlas")
    platforms = meta.get("supported_platforms") if isinstance(meta, dict) else None
    if not isinstance(platforms, list) or not {"linux/amd64", "linux/arm64"}.issubset(
        set(platforms)
    ):
        raise ExportValidationError(
            "docker-compose.yml must assert linux/amd64 and linux/arm64 support."
        )

    return {
        "services": len(services),
        "healthchecks_present": True,
        "multi_arch": True,
    }


def validate_bundle(files: Sequence[tuple[str, str]]) -> None:
    """Run every fail-closed check over a candidate bundle (paths, text, secrets,
    size, and the Compose file). Raises :class:`ExportValidationError`."""

    seen: set[str] = set()
    compose_seen = False
    for path, content in files:
        validate_path(path)
        if path in seen:
            raise ExportValidationError(f"{path}: duplicate path in bundle.")
        seen.add(path)
        validate_text(path, content)
        findings = scan_secrets(content)
        if findings:
            raise ExportValidationError(f"{path}: secret-like content ({'; '.join(findings)}).")
        if path == "docker-compose.yml":
            validate_compose(content)
            compose_seen = True
    if not compose_seen:
        raise ExportValidationError("bundle is missing docker-compose.yml.")
    enforce_size(files)


# --------------------------------------------------------------------------- #
# Generation (pure; no I/O)                                                   #
# --------------------------------------------------------------------------- #


def _containers_for(result: RecommendationResult) -> list[_Container]:
    """The deterministic set of local stand-in services for the recommendation.

    Always includes the ``app`` tier, plus one backing container per distinct
    recommended category that has a safe multi-arch equivalent.
    """

    containers: list[_Container] = [_APP_CONTAINER]
    used: set[str] = set()
    for component in result.components:
        stand_in = _CATEGORY_CONTAINERS.get(component.category)
        if stand_in is not None and stand_in.service not in used:
            containers.append(stand_in)
            used.add(stand_in.service)
    return containers


def _compose_document(result: RecommendationResult) -> str:
    services: dict[str, object] = {}
    for container in _containers_for(result):
        service: dict[str, object] = {
            "image": container.image,
            "restart": "unless-stopped",
            "healthcheck": {
                "test": list(container.healthcheck),
                "interval": "10s",
                "timeout": "5s",
                "retries": 5,
            },
        }
        if container.service == "app":
            service["ports"] = ["${APP_PORT:-8080}:80"]
        if container.environment:
            service["environment"] = {key: value for key, value in container.environment}
        services[container.service] = service

    document = {
        "x-freetier-atlas": {
            "generator": GENERATOR,
            "supported_platforms": list(SUPPORTED_PLATFORMS),
            "note": (
                "Portable local self-hosting scaffold generated from your $0 recommendation. "
                "Copy .env.example to .env and fill placeholders. No secrets are included."
            ),
        },
        "services": services,
    }
    return yaml.safe_dump(document, sort_keys=True, default_flow_style=False, width=100)


def _env_example(result: RecommendationResult) -> str:
    lines = [
        "# FreeTier Atlas deployment scaffold environment.",
        "# Copy this file to .env and replace every placeholder with your own value.",
        "# This file intentionally contains NO real secrets -- only placeholders.",
        "",
        "APP_PORT=8080",
    ]
    for container in _containers_for(result):
        for key, _value in container.environment:
            if key == "POSTGRES_USER":
                lines.append("POSTGRES_USER=atlas")
            elif key == "POSTGRES_DB":
                lines.append("POSTGRES_DB=atlas")
            elif key == "POSTGRES_PASSWORD":
                lines.append("POSTGRES_PASSWORD=REPLACE_ME")
            else:  # pragma: no cover - defensive placeholder for any future key
                lines.append(f"{key}=REPLACE_ME")
    return "\n".join(lines) + "\n"


def _readme(result: RecommendationResult) -> str:
    name = result.workload_name or "your workload"
    status = (
        "Every requirement is met by a truly-free (Z0) offer."
        if result.fully_zero_cost
        else "One or more requirements have no fitting free (Z0) offer (see below)."
    )
    lines = [
        f"# Deployment scaffold for {name}",
        "",
        "This bundle is a **portable local self-hosting scaffold** generated from your",
        "FreeTier Atlas $0 recommendation. It is designed so you can run the *shape* of",
        "the recommended architecture locally on any machine (amd64 or arm64) and keep a",
        "low-lock-in exit path -- it is not a managed-provider deployment.",
        "",
        "**No secrets are included.** `.env.example` holds only placeholders; copy it to",
        "`.env` and fill in your own values before running `docker compose up`.",
        "",
        "## $0 status",
        "",
        f"- {status}",
    ]
    for line in explain.zero_cost_proof(result):
        lines.append(f"- {line}")

    lines += ["", "## Recommended free (Z0) components"]
    if result.components:
        for component in result.components:
            candidate = component.candidate
            title = component.label or component.category
            lines.append("")
            lines.append(f"### {title}")
            lines.append(
                f"- Offer: **{candidate.service_name}** by {candidate.provider_name} "
                f"(offer id {candidate.offer_id}, class {candidate.zero_cost_class}, "
                f"confidence {candidate.confidence_label})"
            )
            portability = candidate.portability
            lines.append(
                f"- Portability: {portability.label}; lock-in: {portability.lock_in_label}"
            )
            for step in portability.exit_plan:
                lines.append(f"  - Exit plan: {step}")
    else:
        lines.append("")
        lines.append("- None: no requirement had a fitting free (Z0) offer.")

    if result.impossible:
        lines += ["", "## Requirements with no $0 option (self-hosting guidance)"]
        for item in result.impossible:
            title = item.label or item.category
            lines.append("")
            lines.append(f"### {title}")
            lines.append(f"- {item.blocking_reason}")
            for option in item.self_hosting:
                lines.append(f"  - Self-host: {option.note}")

    lines += [
        "",
        "## Local scaffold services",
        "",
        "See `docker-compose.yml`. Every service pins a multi-arch image and declares a",
        "healthcheck. Categories without a safe open-source container equivalent are",
        "listed above as managed offers rather than invented into the Compose file.",
        "",
    ]
    return "\n".join(lines)


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _architecture_summary(result: RecommendationResult) -> list[dict[str, object]]:
    summary: list[dict[str, object]] = []
    for component in result.components:
        candidate = component.candidate
        summary.append(
            {
                "requirement_index": component.requirement_index,
                "category": component.category,
                "provider_slug": candidate.provider_slug,
                "service_name": candidate.service_name,
                "offer_id": candidate.offer_id,
                "zero_cost_class": candidate.zero_cost_class,
            }
        )
    return summary


def build_export(result: RecommendationResult) -> ExportResponse:
    """Generate + validate the deployment bundle for ``result`` (pure, no I/O).

    Never writes a file and never touches the database: the bundle exists only in
    the returned :class:`ExportResponse`. Raises :class:`ExportValidationError`
    if any generated file fails a fail-closed check.
    """

    content_files: list[tuple[str, str]] = [
        ("docker-compose.yml", _compose_document(result)),
        (".env.example", _env_example(result)),
        ("README.md", _readme(result)),
    ]

    # Validate the generated content before building the manifest (fail closed).
    validate_bundle(content_files)
    total_bytes = enforce_size(content_files)

    manifest_files = [
        {"path": path, "sha256": _sha256(content), "size": len(content.encode("utf-8"))}
        for path, content in sorted(content_files, key=lambda pair: pair[0])
    ]

    manifest = ManifestOut(
        workload_name=result.workload_name,
        fully_zero_cost=result.fully_zero_cost,
        platforms=list(SUPPORTED_PLATFORMS),
        files=manifest_files,
        total_bytes=total_bytes,
        file_count=len(content_files),
        validation={
            "paths_safe": True,
            "text_only": True,
            "secret_scan_passed": True,
            "compose_parsed": True,
            "healthchecks_present": True,
            "multi_arch": True,
            "within_size_cap": True,
        },
        architecture=_architecture_summary(result),
        self_hosting_required=[
            {"requirement_index": item.requirement_index, "category": item.category}
            for item in result.impossible
        ],
        notes=[
            "Generated content is validated and secret-free; nothing is persisted server-side.",
            "This bundle is a portable local self-hosting scaffold, not a managed deployment.",
        ],
    )

    manifest_content = json.dumps(
        manifest.model_dump(), sort_keys=True, indent=2, ensure_ascii=False
    )
    # The manifest file itself is validated + secret-scanned like every other file.
    all_files: list[tuple[str, str]] = [*content_files, ("MANIFEST.json", manifest_content)]
    validate_bundle(all_files)
    enforce_size(all_files)

    file_outs = [
        ExportFileOut(
            path=path,
            content=content,
            sha256=_sha256(content),
            size=len(content.encode("utf-8")),
        )
        for path, content in all_files
    ]

    return ExportResponse(
        workload_name=result.workload_name,
        fully_zero_cost=result.fully_zero_cost,
        files=file_outs,
        manifest=manifest,
    )


__all__: Iterable[str] = (
    "GENERATOR",
    "SUPPORTED_PLATFORMS",
    "MULTI_ARCH_IMAGES",
    "ALLOWED_PATHS",
    "ExportValidationError",
    "ExportFileOut",
    "ManifestOut",
    "ExportResponse",
    "is_placeholder",
    "scan_secrets",
    "validate_text",
    "validate_path",
    "validate_compose",
    "validate_bundle",
    "enforce_size",
    "build_export",
)
