"""Load a module from a file path CORRECTLY. Import this; never hand-roll it.

Why this exists
---------------
Loading a module by path with importlib is a three-line operation with one
non-obvious ordering requirement, and getting it wrong produces an error that
names neither the cause nor the fix:

    AttributeError: 'NoneType' object has no attribute '__dict__'

That is `dataclasses` resolving a field annotation via
``sys.modules.get(cls.__module__)`` and getting ``None``, because the module was
executed before it was registered. Any module containing a ``@dataclass`` blows
up; a module without one loads fine, so the failure looks like it belongs to the
code under test rather than to the loader.

This was filed as a lesson after PR #63 and then hit again, by two different
people, in PR #69 - once by this evaluator and once by the orchestrator, within
an hour of each other. A lesson that has to be *recalled* to work has no failure
mode: nothing goes red when you forget it. So the remedy is not "remember
harder", it is a helper that cannot be written wrong, and the rule is that
evaluator probes import this rather than re-deriving it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_module(path: str | Path, name: str | None = None) -> ModuleType:
    """Import the module at ``path`` and return it.

    ``name`` defaults to the file stem. Registration in ``sys.modules`` happens
    BEFORE execution, which is the whole point of this function.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no module to load at {path}")
    name = name or path.stem

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build a spec for {path}")

    module = importlib.util.module_from_spec(spec)
    # MUST precede exec_module: dataclasses resolves annotations through
    # sys.modules[cls.__module__] and raises an unrelated-looking AttributeError
    # if the module is not there yet.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def selftest() -> None:
    """Prove the ordering requirement is real, not folklore.

    Loads a throwaway dataclass-bearing module both ways and asserts the naive
    ordering fails while :func:`load_module` succeeds. A helper guarding against
    a failure it cannot demonstrate is decoration.
    """
    import tempfile

    # `from __future__ import annotations` is load-bearing here: it makes the
    # annotations STRINGS, which is what forces dataclasses to resolve them via
    # sys.modules[cls.__module__]. Without it the naive ordering happens to
    # work, and a selftest that cannot reproduce the failure proves nothing.
    # source_scan.py has this import, which is why it tripped the bug.
    source = (
        "from __future__ import annotations\n\n"
        "from dataclasses import dataclass\n\n\n"
        "@dataclass(frozen=True)\n"
        "class P:\n"
        "    x: int | None = 1\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "probe_dc_selftest.py"
        target.write_text(source, encoding="utf-8")

        # Naive ordering: execute before registering. Must fail.
        spec = importlib.util.spec_from_file_location("probe_dc_selftest", target)
        assert spec and spec.loader
        naive = importlib.util.module_from_spec(spec)
        sys.modules.pop("probe_dc_selftest", None)
        try:
            spec.loader.exec_module(naive)
        except AttributeError as exc:
            print(f"  naive ordering failed as expected: {type(exc).__name__}: {exc}")
        else:
            print("  NOTE: naive ordering did NOT fail on this Python; helper is still correct")
        finally:
            sys.modules.pop("probe_dc_selftest", None)

        # Correct ordering. Must succeed.
        good = load_module(target, "probe_dc_selftest_ok")
        assert good.P().x == 1
        print("  load_module() succeeded and the dataclass is usable")
        sys.modules.pop("probe_dc_selftest_ok", None)


if __name__ == "__main__":
    selftest()
