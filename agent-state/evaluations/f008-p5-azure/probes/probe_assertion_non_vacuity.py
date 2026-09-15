"""Scoped re-check instrument 2: is the strengthened App Service assertion non-vacuous?

The correction replaces a test whose NAME overclaimed with a test that asserts the
EXACT blocking-condition set. That is only an improvement if the new assertion can
actually FAIL when the blocking set changes -- otherwise a false name has been
replaced by a false guarantee.

This locates the App Service blocking-set test by CONTENT rather than by name (the
test is being renamed, so pinning a name here would silently stop testing anything),
establishes it GREEN, then perturbs the classifier result three ways:

  1. drop the paid-dependency condition   -> the wrong world the builder described
  2. add a spurious extra condition       -> a superset
  3. return the identical set unchanged   -> DISCRIMINATION CONTROL, must still pass

Mutations 1 and 2 MUST raise AssertionError. Mutation 3 must not. A test that
survives 1 and 2 is vacuous and the fix is cosmetic.

Usage:  python probe_assertion_non_vacuity.py <repo_root>
"""

from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
sys.path.insert(0, str(REPO / "apps" / "api"))
sys.path.insert(0, str(REPO))

TEST_PATH = REPO / "tests" / "unit" / "test_adapter_azure.py"


def load_test_module():
    spec = importlib.util.spec_from_file_location("test_adapter_azure_probe", TEST_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {TEST_PATH}")
    module = importlib.util.module_from_spec(spec)
    # MUST be registered before exec_module or dataclasses/typing resolution fails.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def find_candidates(module):
    """Every zero-argument test that reads App Service AND inspects blocking conditions."""

    found = []
    for name, obj in vars(module).items():
        if not name.startswith("test_") or not callable(obj):
            continue
        try:
            source = inspect.getsource(obj)
        except (OSError, TypeError):
            continue
        if "azure-app-service-quotas" not in source or "blocking_conditions" not in source:
            continue
        if inspect.signature(obj).parameters:
            continue
        found.append((name, obj, source))
    return found


def run(fn) -> tuple[bool, str]:
    try:
        fn()
    except AssertionError as exc:
        return False, f"AssertionError: {str(exc)[:120]}"
    except Exception as exc:  # noqa: BLE001 - any error means the probe misfired
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"
    return True, "passed"


def main() -> None:
    module = load_test_module()
    candidates = find_candidates(module)

    print("=" * 78)
    print("DISCOVERY (by content, not by name)")
    print("=" * 78)
    if not candidates:
        raise SystemExit(
            "NO test reads azure-app-service-quotas AND inspects blocking_conditions. "
            "The strengthened assertion does not exist, or it no longer inspects the set."
        )
    for name, _, _ in candidates:
        print(f"  found: {name}")

    real_classify = module.classify
    real_underscore = getattr(module, "_classify", None)

    def install(transform) -> None:
        def wrapper(*args, **kwargs):
            return transform(real_classify(*args, **kwargs))

        module.classify = wrapper
        if real_underscore is not None:

            def underscore_wrapper(*args, **kwargs):
                return transform(real_underscore(*args, **kwargs))

            module._classify = underscore_wrapper

    def restore() -> None:
        module.classify = real_classify
        if real_underscore is not None:
            module._classify = real_underscore

    def drop_paid_deps(result):
        kept = tuple(c for c in result.blocking_conditions if "paid dependencies" not in c)
        return dataclasses.replace(result, blocking_conditions=kept, reasons=kept)

    def add_spurious(result):
        extra = (*result.blocking_conditions, "Whether the moon is unknown.")
        return dataclasses.replace(result, blocking_conditions=extra, reasons=extra)

    def identity(result):
        return dataclasses.replace(result, blocking_conditions=tuple(result.blocking_conditions))

    mutations = (
        ("drop paid-dependency condition", drop_paid_deps, False),
        ("add spurious extra condition", add_spurious, False),
        ("identical set (DISCRIMINATION CONTROL)", identity, True),
    )

    overall_ok = True
    for name, fn, _source in candidates:
        print()
        print("=" * 78)
        print(f"TEST: {name}")
        print("=" * 78)

        restore()
        ok, detail = run(fn)
        print(f"  BASELINE (real engine): {'GREEN' if ok else 'RED'} - {detail}")
        if not ok:
            print("  -> baseline is not green; every result below is meaningless.")
            overall_ok = False
            continue

        for label, transform, must_pass in mutations:
            install(transform)
            try:
                passed, detail = run(fn)
            finally:
                restore()
            good = passed if must_pass else not passed
            verdict = "OK" if good else "VACUOUS" if not must_pass else "OVER-SENSITIVE"
            print(f"  PATCHED [{label}]")
            print(f"    result: {'passed' if passed else 'FAILED'} - {detail}")
            print(f"    expected {'pass' if must_pass else 'FAIL'}  -> {verdict}")
            if not good:
                overall_ok = False

    print()
    print("=" * 78)
    print(f"NON-VACUITY VERDICT: {'PROVEN' if overall_ok else 'NOT PROVEN'}")
    print("=" * 78)
    if not overall_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
