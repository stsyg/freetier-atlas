"""A registered config family model must be *consulted*, not just *validated*.

Every family in :data:`app.config.models.FAMILY_MODELS` is validated by
``python -m app.config.cli validate`` and by the loader. Validation alone is a
trap: a model can pass every schema check and still be dead, because nothing in
the running application ever reads it. ``AdminSection.allowed_users`` was exactly
that -- a config-file allowlist that duplicated the enforced
``Settings.admin_allowlist`` control, validated and then ignored, so an operator
who edited it (or, worse, *revoked* someone through it) saw no effect and no
error. This guard makes that hazard class detectable: a family model that no app
code reads must either become consulted or be listed, with a stated reason, in
:data:`ALLOWED_VALIDATION_ONLY`.

**Why AST and not a name-grep.** A name-grep answers "who mentions this string",
not "what reads this value". The two are different, and the difference is the
entire point: ``public_adviser`` also names an unrelated LLM-limits section and
``rss`` also names the ``application/rss+xml`` MIME type, so a substring scan
reports those flags as "used" when nothing reads them. This guard counts a real
read only -- an ``ast.Name`` load of the class in a consumer module. A mention in
a docstring is a ``str`` constant, not a ``Name``; a mention in a comment is not
in the AST at all; an ``import`` binds an ``alias``, not a ``Name`` load. All
three are correctly excluded, so what remains is genuine consultation
(``isinstance(cfg, ProviderConfig)``, an annotation ``cfg: ProviderConfig``, an
attribute chain rooted at the class, ...).

**Why the family set is derived, not hand-written.** Two hand-maintained lists in
one file are edited in one breath and drift together -- the same failure mode the
dead field came from. The family roots are read from the ``FAMILY_MODELS`` dict
literal in ``models.py`` at test time, so a NEW family is covered the instant it
is registered, without anyone updating this guard.

**Consumers, precisely.** "Consulted" means read by the *application*, so the scan
covers ``apps/api/app`` (excluding the ``config`` package that defines and
re-exports the models) plus ``apps/worker`` and ``scripts``. ``tests/`` is
excluded on purpose: a test exercising a model does not make the product depend
on it.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_APP = REPO_ROOT / "apps" / "api" / "app"
CONFIG_DIR = API_APP / "config"
WORKER = REPO_ROOT / "apps" / "worker"
SCRIPTS = REPO_ROOT / "scripts"

#: The single source the family set is derived from.
MODELS_FILE = CONFIG_DIR / "models.py"

#: The family roots we know must exist. Purely an anti-vacuous cross-check: the
#: derivation reads them from source, and this set proves the derivation is not
#: silently empty. It is NOT the input to the guard.
KNOWN_FAMILY_ROOTS = frozenset(
    {"ApplicationConfig", "SchedulesConfig", "LlmProvidersConfig", "ProviderConfig"}
)

#: Families that are legitimately validation-only, each with a stated reason.
#: A model here is allowed to have zero consumers; every other family MUST be
#: read by app code. Keeping the exemption explicit and reviewable is the whole
#: point -- a silent skip would reproduce the bug this guard exists to catch.
ALLOWED_VALIDATION_ONLY: dict[str, str] = {
    "ApplicationConfig": (
        "Declarative application manifest (name/urls/environment, catalogue "
        "toggles, admin.authentication, feature flags). It is validated by "
        "'cli validate' but not yet wired into runtime behaviour; the feature "
        "flags are a deliberate separate decision. It must NOT regain an "
        "'allowed_users' allowlist: admin access is enforced solely via "
        "Settings.admin_allowlist."
    ),
    "SchedulesConfig": (
        "Declarative crawl/reconciliation cadence manifest. Validated by "
        "'cli validate' but not yet consumed by the scheduler; wiring it is a "
        "separate decision."
    ),
}


def _python_files(root: Path) -> Iterator[Path]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        yield path


def _consumer_files() -> list[Path]:
    """App/worker/script modules that could legitimately read a family model.

    Excludes the ``config`` package (which defines and re-exports the models) so
    a definition or an ``__all__`` re-export is never mistaken for consumption.
    """

    files: list[Path] = []
    for path in _python_files(API_APP):
        if CONFIG_DIR in path.parents or path == MODELS_FILE:
            continue
        files.append(path)
    files.extend(_python_files(WORKER))
    files.extend(_python_files(SCRIPTS))
    return files


def _family_roots() -> set[str]:
    """Class names registered as values in the ``FAMILY_MODELS`` dict literal."""

    tree = ast.parse(MODELS_FILE.read_text(encoding="utf-8"), filename=str(MODELS_FILE))
    for node in ast.walk(tree):
        target_is_family_models = (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "FAMILY_MODELS"
        ) or (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "FAMILY_MODELS" for t in node.targets)
        )
        if not target_is_family_models:
            continue
        value = node.value
        if not isinstance(value, ast.Dict):
            continue
        return {v.id for v in value.values if isinstance(v, ast.Name)}
    return set()


def _referenced_names(source: str) -> set[str]:
    """Every name *loaded* in ``source`` -- a real read, not a mention.

    Docstrings and other string literals are ``ast.Constant`` nodes, comments are
    absent from the AST, and imported symbols are ``alias`` nodes; none of them
    produce an ``ast.Name`` load, so none are counted here.
    """

    tree = ast.parse(source)
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def _consulted_roots(roots: set[str], files: list[Path]) -> set[str]:
    consulted: set[str] = set()
    for path in files:
        referenced = _referenced_names(path.read_text(encoding="utf-8"))
        consulted |= roots & referenced
    return consulted


# --------------------------------------------------------------------------- #
# Anti-vacuous: the derivation must actually have found the families, or every
# assertion below would pass against an empty set forever.
# --------------------------------------------------------------------------- #
def test_family_derivation_is_not_vacuous() -> None:
    roots = _family_roots()
    assert roots, (
        "Derived no family roots from FAMILY_MODELS in "
        f"{MODELS_FILE.relative_to(REPO_ROOT).as_posix()}; the dict literal parse "
        "is broken and this guard would pass vacuously."
    )
    assert KNOWN_FAMILY_ROOTS <= roots, (
        f"Expected the known family roots {sorted(KNOWN_FAMILY_ROOTS)} to be "
        f"registered in FAMILY_MODELS; derived {sorted(roots)}."
    )
    assert _consumer_files(), "Found no consumer modules to scan; the roots are wrong."


# --------------------------------------------------------------------------- #
# The load-bearing assertion.
# --------------------------------------------------------------------------- #
def test_every_family_model_is_consulted_or_explicitly_allowed() -> None:
    roots = _family_roots()
    consulted = _consulted_roots(roots, _consumer_files())

    dead = {name for name in roots if name not in consulted and name not in ALLOWED_VALIDATION_ONLY}
    assert not dead, (
        "These config family models are validated but never read by any app code, "
        f"and are not listed in ALLOWED_VALIDATION_ONLY: {sorted(dead)}. Either wire "
        "the model into the application (read it via isinstance/annotation/attribute "
        "access), or, if it is deliberately validation-only, add it to "
        "ALLOWED_VALIDATION_ONLY with a stated reason so the exemption is reviewable."
    )


def test_allowance_is_honest_no_consulted_model_is_exempted() -> None:
    """An allowed model that turns out to be consulted has a stale exemption."""

    roots = _family_roots()
    consulted = _consulted_roots(roots, _consumer_files())
    stale = sorted(name for name in ALLOWED_VALIDATION_ONLY if name in consulted)
    assert not stale, (
        f"These models are in ALLOWED_VALIDATION_ONLY but ARE consulted by app "
        f"code: {stale}. Remove them from the allowance -- they are load-bearing "
        "and the guard should protect them, not exempt them."
    )


def test_allowance_only_names_real_families() -> None:
    roots = _family_roots()
    unknown = sorted(name for name in ALLOWED_VALIDATION_ONLY if name not in roots)
    assert not unknown, (
        f"ALLOWED_VALIDATION_ONLY names {unknown}, which are not registered in "
        "FAMILY_MODELS. Remove the stale exemption."
    )


# --------------------------------------------------------------------------- #
# Precision controls: prove this is a read-check, not a name-ban.
# --------------------------------------------------------------------------- #
def test_consultation_detector_ignores_docstring_and_comment_mentions() -> None:
    source = (
        '"""This module talks about ProviderConfig at length in prose."""\n'
        "# ProviderConfig is also mentioned in this comment\n"
        "x = 1\n"
    )
    assert "ProviderConfig" not in _referenced_names(source)


def test_consultation_detector_ignores_a_bare_import() -> None:
    source = "from app.config.models import ProviderConfig\n__all__ = ['ProviderConfig']\n"
    assert "ProviderConfig" not in _referenced_names(source)


def test_consultation_detector_sees_a_real_isinstance_read() -> None:
    source = "def handle(cfg):\n    return isinstance(cfg, ProviderConfig)\n"
    assert "ProviderConfig" in _referenced_names(source)


def test_consultation_detector_sees_a_real_annotation_read() -> None:
    source = "def handle(cfg: ProviderConfig) -> None:\n    return None\n"
    assert "ProviderConfig" in _referenced_names(source)


def test_the_genuinely_consulted_families_are_detected() -> None:
    """ProviderConfig and LlmProvidersConfig are read by real app code today."""

    roots = _family_roots()
    consulted = _consulted_roots(roots, _consumer_files())
    assert {"ProviderConfig", "LlmProvidersConfig"} <= consulted, (
        "Expected ProviderConfig (ingest) and LlmProvidersConfig (adviser) to be "
        f"detected as consulted; got {sorted(consulted)}."
    )
