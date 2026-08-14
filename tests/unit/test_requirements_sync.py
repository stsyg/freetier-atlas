"""Guard test: runtime dependency pins must stay in sync.

apps/api/requirements.txt (used by the Docker image) and the
[project.dependencies] table in pyproject.toml must declare exactly the same
pinned runtime dependencies, so the container and local environments match.

apps/worker/requirements.txt uses a subset of those runtime dependencies; every
worker pin must match the corresponding pyproject pin exactly (no drift).

requirements-dev.txt and the [project.optional-dependencies] dev group are
likewise mirrors. That was previously asserted only by a comment, and the
comment did not hold: `cryptography` had to be added to both by hand, and had it
reached only one, `pip install -e ".[dev]"` and `pip install -r
requirements-dev.txt` would have produced different environments -- the second
of which is what the dependency audit gates on. A claim no test enforces is a
claim that drifts, so it is enforced here.
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


def _declared_dev_pins() -> set[str]:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(pyproject["project"]["optional-dependencies"]["dev"])


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


def test_dev_pins_in_sync() -> None:
    declared = _declared_dev_pins()

    requirements = _parse_requirements(
        (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    )

    assert declared == requirements, (
        "Development dependency drift between the pyproject.toml dev extra and "
        f"requirements-dev.txt.\n  pyproject: {sorted(declared)}\n  "
        f"requirements: {sorted(requirements)}"
    )


def test_test_only_pins_never_reach_a_shipped_image() -> None:
    """The dev extra must not leak into either production requirements file.

    `cryptography` is installed so the end-to-end TLS tests run in CI. It is a
    TEST dependency: adding it to a shipped image would widen the runtime attack
    surface for no runtime benefit, and the two production audits gate on those
    files.
    """

    shipped = _parse_requirements(
        (REPO_ROOT / "apps" / "api" / "requirements.txt").read_text(encoding="utf-8")
    ) | _parse_requirements(
        (REPO_ROOT / "apps" / "worker" / "requirements.txt").read_text(encoding="utf-8")
    )

    leaked = {pin for pin in shipped if pin.split("==")[0].strip().lower() == "cryptography"}
    assert not leaked, f"A test-only pin reached a production requirements file: {sorted(leaked)}"
