"""Guard test: runtime dependency pins must stay in sync.

apps/api/requirements.txt (used by the Docker image) and the
[project.dependencies] table in pyproject.toml must declare exactly the same
pinned runtime dependencies, so the container and local environments match.

apps/worker/requirements.txt uses a subset of those runtime dependencies; every
worker pin must match the corresponding pyproject pin exactly (no drift).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse_requirements(text: str) -> set[str]:
    entries: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            entries.add(line)
    return entries


def _declared_pins() -> set[str]:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(pyproject["project"]["dependencies"])


def test_runtime_pins_in_sync() -> None:
    declared = _declared_pins()

    requirements = _parse_requirements(
        (REPO_ROOT / "apps" / "api" / "requirements.txt").read_text(encoding="utf-8")
    )

    assert declared == requirements, (
        "Runtime dependency drift between pyproject.toml and "
        f"apps/api/requirements.txt.\n  pyproject: {sorted(declared)}\n  "
        f"requirements: {sorted(requirements)}"
    )


def test_worker_pins_subset_of_declared() -> None:
    declared = _declared_pins()

    worker_requirements = _parse_requirements(
        (REPO_ROOT / "apps" / "worker" / "requirements.txt").read_text(encoding="utf-8")
    )

    assert worker_requirements, "apps/worker/requirements.txt must declare runtime pins."
    drift = worker_requirements - declared
    assert not drift, (
        "apps/worker/requirements.txt pins must match pyproject.toml exactly.\n  "
        f"drifted pins: {sorted(drift)}\n  pyproject: {sorted(declared)}"
    )
