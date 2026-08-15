"""Re-check instrument 4: is the azure.py change documentation-only?

The builder claims the corrected `azure.py` differs from the evaluated revision
only in prose, and proved it by loading both revisions and comparing profiles
field by field. That is a stronger method than reading a diff, so it is
reproduced here INDEPENDENTLY rather than accepted.

Both revisions are imported from their own extracted trees under distinct module
names, and every registered profile is compared structurally: name, mode, header
signature, matrix metric/tier headers, matrix rows, required fields, and every
assertion's text/field/value/scope/required flag.

A structural difference means the change is NOT documentation-only and the
earlier evaluation's verdict chain must be re-derived rather than composed.

Usage:  python probe_profiles_structurally_identical.py <tree_a> <tree_b>
"""

from __future__ import annotations

import dataclasses
import importlib
import sys
from pathlib import Path


def load_profiles(tree: Path, tag: str) -> dict:
    """Import the profile registry from ``tree`` in an isolated module namespace."""

    api = str((tree / "apps" / "api").resolve())
    saved_path = list(sys.path)
    saved_modules = {k: v for k, v in sys.modules.items() if k.startswith("app")}
    for name in list(sys.modules):
        if name.startswith("app"):
            del sys.modules[name]
    sys.path.insert(0, api)
    try:
        ingest = importlib.import_module("app.ingest")
        profiles = importlib.import_module("app.ingest.adapters.profiles")
        importlib.import_module("app.ingest.adapters.profiles.azure")
        names = (
            [n for n in profiles.registered_html_profiles()]
            if hasattr(profiles, "registered_html_profiles")
            else None
        )
        if names is None:
            names = [
                "azure_free_account",
                "azure_free_services",
                "azure_cosmos_db_free_tier",
                "azure_app_service_quotas",
                "azure_static_web_apps_plans",
                "azure_devops_services",
                "azure_students",
            ]
        snapshot = {name: describe(ingest.resolve_profile(name)) for name in names}
    finally:
        sys.path[:] = saved_path
        for name in list(sys.modules):
            if name.startswith("app"):
                del sys.modules[name]
        sys.modules.update(saved_modules)
    print(f"  loaded {len(snapshot)} profiles from {tag}")
    return snapshot


def describe(profile) -> dict:
    """Reduce a profile to a comparable, ordering-stable structure."""

    assertions = []
    for assertion in profile.assertions:
        item = dataclasses.asdict(assertion) if dataclasses.is_dataclass(assertion) else {}
        assertions.append({k: repr(v) for k, v in sorted(item.items())})

    rows = {}
    for key, row in dict(getattr(profile, "matrix_rows", {}) or {}).items():
        item = dataclasses.asdict(row) if dataclasses.is_dataclass(row) else {}
        rows[str(key)] = {k: repr(v) for k, v in sorted(item.items())}

    return {
        "name": profile.name,
        "mode": profile.mode,
        "header_signature": repr(getattr(profile, "header_signature", None)),
        "matrix_metric_header": repr(getattr(profile, "matrix_metric_header", None)),
        "matrix_tier_header": repr(getattr(profile, "matrix_tier_header", None)),
        "ignored_matrix_rows": repr(getattr(profile, "ignored_matrix_rows", None)),
        "required_fields": repr(getattr(profile, "required_fields", None)),
        "trusted_assertions": repr(getattr(profile, "trusted_assertions", None)),
        "matrix_rows": rows,
        "assertions": assertions,
    }


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    tree_a, tree_b = Path(sys.argv[1]), Path(sys.argv[2])

    print("=" * 78)
    print("LOADING BOTH REVISIONS")
    print("=" * 78)
    a = load_profiles(tree_a, f"A={tree_a}")
    b = load_profiles(tree_b, f"B={tree_b}")

    print()
    print("=" * 78)
    print("STRUCTURAL COMPARISON")
    print("=" * 78)
    if set(a) != set(b):
        print(f"  PROFILE SETS DIFFER: only-A={set(a) - set(b)}  only-B={set(b) - set(a)}")
        raise SystemExit(1)

    total_assertions = 0
    total_rows = 0
    differences = []
    for name in sorted(a):
        pa, pb = a[name], b[name]
        total_assertions += len(pa["assertions"])
        total_rows += len(pa["matrix_rows"])
        same = pa == pb
        print(
            f"  {name:<32} assertions={len(pa['assertions']):>2} "
            f"rows={len(pa['matrix_rows']):>2}  {'IDENTICAL' if same else 'DIFFERS'}"
        )
        if not same:
            for key in pa:
                if pa[key] != pb[key]:
                    differences.append((name, key, pa[key], pb[key]))

    print()
    print(f"  profiles compared : {len(a)}")
    print(f"  assertions compared: {total_assertions}")
    print(f"  matrix rows compared: {total_rows}")

    if differences:
        print()
        print("  STRUCTURAL DIFFERENCES FOUND:")
        for name, key, va, vb in differences:
            print(f"    {name}.{key}")
            print(f"      A: {str(va)[:200]}")
            print(f"      B: {str(vb)[:200]}")

    print()
    print("=" * 78)
    print(f"DOCUMENTATION-ONLY: {not differences}")
    print("=" * 78)
    if differences:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
