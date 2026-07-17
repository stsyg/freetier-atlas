"""Load and validate FreeTier Atlas YAML configuration files.

The loader turns a YAML file into a validated, typed configuration model and
raises :class:`ConfigError` with actionable, file-scoped problem messages when a
file is missing, malformed, contains an inline secret, or fails schema
validation. It never reads or emits secret *values*: secrets are referenced by
environment-variable name only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .models import FAMILY_MODELS, _Base

# Keys that must never hold an inline credential. A configuration references a
# secret by environment-variable *name* using a ``*_env`` field instead.
_INLINE_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "secret_key",
        "password",
        "passwd",
        "token",
        "access_key",
        "private_key",
        "client_secret",
    }
)


class ConfigError(Exception):
    """A configuration file could not be loaded or failed validation.

    ``problems`` is a list of human-readable, file-scoped messages. ``path`` is
    the offending file.
    """

    def __init__(self, path: Path, problems: list[str]) -> None:
        self.path = Path(path)
        self.problems = problems
        joined = "; ".join(problems)
        super().__init__(f"{self.path}: {joined}")


def detect_family(data: dict[str, Any]) -> str:
    """Return the configuration family name for a parsed top-level mapping."""

    keys = set(data)
    if {"application", "catalogue", "admin", "features"} <= keys:
        return "application"
    if "schedules" in keys:
        return "schedules"
    if "llm" in keys:
        return "llm-providers"
    if {"provider", "sources"} <= keys:
        return "provider"
    raise ValueError(
        "could not determine configuration family from top-level keys "
        f"{sorted(keys)!r}; expected one of application/schedules/llm/provider"
    )


def _scan_for_inline_secrets(data: Any, location: str = "") -> list[str]:
    """Return problems for any inline secret keys found in ``data``."""

    problems: list[str] = []

    def walk(node: Any, loc: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                current = f"{loc}.{key}" if loc else str(key)
                if str(key).lower() in _INLINE_SECRET_KEYS:
                    problems.append(
                        f"{current}: inline secret key {key!r} is forbidden; reference an "
                        f"environment variable via '{key}_env' instead (values live in the "
                        "environment, never in the repository)"
                    )
                walk(value, current)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{loc}[{index}]")

    walk(data, location)
    return problems


def _format_validation_error(exc: ValidationError) -> list[str]:
    problems: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"]) or "<root>"
        problems.append(f"{loc}: {error['msg']} (type={error['type']})")
    return problems


def load_and_validate(path: str | Path) -> _Base:
    """Load ``path``, detect its family, and return the validated model.

    Raises :class:`ConfigError` with actionable problems on any failure.
    """

    path = Path(path)
    if not path.exists():
        raise ConfigError(path, ["file not found"])

    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        problem = getattr(exc, "problem", None) or "invalid YAML"
        if mark is not None:
            message = (
                f"YAML syntax error at line {mark.line + 1} column {mark.column + 1}: {problem}"
            )
        else:
            message = f"YAML syntax error: {exc}"
        raise ConfigError(path, [message]) from exc

    if data is None:
        raise ConfigError(path, ["file is empty"])
    if not isinstance(data, dict):
        raise ConfigError(path, ["top-level YAML must be a mapping"])

    secret_problems = _scan_for_inline_secrets(data)
    if secret_problems:
        raise ConfigError(path, secret_problems)

    try:
        family = detect_family(data)
    except ValueError as exc:
        raise ConfigError(path, [str(exc)]) from exc

    model = FAMILY_MODELS[family]
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(path, _format_validation_error(exc)) from exc


__all__ = ["ConfigError", "detect_family", "load_and_validate"]
