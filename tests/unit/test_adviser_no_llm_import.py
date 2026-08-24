"""Guard: the deterministic adviser core must not import the LLM package.

The recommendation must remain a pure function of the structured request and the
published catalogue. Importing the deterministic core must therefore NOT pull in
``app.adviser.llm`` (which depends on providers/config). This is asserted in a
*fresh* subprocess so it is unaffected by whatever the test process already
imported (the router legitimately imports both).

Both failure directions, and which of them was instrumented
-----------------------------------------------------------
wrongly ACCEPT
    The core imports the LLM package and the guard stays green. Until this
    revision the repository contained **no evidence at all that the probe could
    detect a leak** - every assertion here was of the form "nothing was found",
    which is exactly what a probe that cannot find anything also reports. A test
    that is structurally incapable of failing for the reason it names is not a
    weak test, it is a decorative one. :func:`test_the_probe_can_detect_a_leak`
    is the positive control that closes that: it runs the *identical* probe
    against ``app.adviser.router``, measured to import seven ``app.adviser.llm``
    modules, and requires the probe to report them.

    The second way to wrongly accept is coverage drift: the probe constrains only
    the modules it names, so a core module added later is silently unguarded.
    Measured on the base commit, four deterministic modules - ``explain``,
    ``portability``, ``quota_math`` and ``export`` - were never probed. All four
    measured clean, so they are probed now, and
    :func:`test_every_probed_module_exists` keeps a rename from quietly emptying
    the list.

wrongly REJECT
    The probe subprocess fails for a reason that is nothing to do with a leak - a
    missing dependency, a syntax error, the wrong working directory - and the
    failure is reported as "the core imported the LLM package". That accusation
    sends the reader to the wrong place, and a guard that misdiagnoses is one
    people stop believing. :func:`test_a_probe_failure_is_not_reported_as_a_leak`
    measures that the two outcomes stay distinguishable.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[2] / "apps" / "api"
_ADVISER_DIR = _APP_DIR / "app" / "adviser"

#: The deterministic core: every adviser module that must resolve a
#: recommendation without the LLM package present.
CORE_MODULES = (
    "app.adviser.recommend",
    "app.adviser.select",
    "app.adviser.schema",
    "app.adviser.schemas",
    "app.adviser.explain",
    "app.adviser.portability",
    "app.adviser.quota_math",
    "app.adviser.export",
)

#: Measured, not assumed: importing this pulls in seven ``app.adviser.llm``
#: modules. It is the positive control, and it is the router precisely because
#: the router is *supposed* to import both halves.
LEAKING_MODULE = "app.adviser.router"

_PROBE_TEMPLATE = """
import importlib
import sys

for name in {modules!r}:
    importlib.import_module(name)

leaked = sorted(m for m in sys.modules if m.startswith("app.adviser.llm"))
if leaked:
    print("LEAKED:" + ",".join(leaked))
    sys.exit(1)
print("OK")
"""


def _probe(modules: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _PROBE_TEMPLATE.format(modules=list(modules))],
        cwd=str(_APP_DIR),
        capture_output=True,
        text=True,
    )


def _describe(result: subprocess.CompletedProcess[str]) -> str:
    return (
        f"exit={result.returncode} stdout={result.stdout.strip()!r} "
        f"stderr={result.stderr.strip()!r}"
    )


def test_core_does_not_import_llm_package() -> None:
    result = _probe(CORE_MODULES)
    # Say which of the two failures happened. "It leaked" and "the probe did not
    # run" are different problems with different owners, and conflating them is
    # how a guard earns a reputation for crying wolf.
    assert not result.stdout.startswith("LEAKED:"), (
        f"deterministic adviser core imported the LLM package: {_describe(result)}"
    )
    assert result.returncode == 0, f"the probe itself failed to run: {_describe(result)}"
    assert result.stdout.strip() == "OK", _describe(result)


def test_the_probe_can_detect_a_leak() -> None:
    """POSITIVE CONTROL. Without this, the test above proves nothing.

    Every other assertion in this module is satisfied by a probe that finds
    nothing because it is incapable of finding anything. This one requires the
    same probe, run the same way, to actually report a leak that is known to be
    there.
    """

    result = _probe((LEAKING_MODULE,))
    assert result.returncode == 1, (
        f"{LEAKING_MODULE} is supposed to import the LLM package, so the probe should "
        f"have failed. It did not, which means the probe cannot detect a leak: "
        f"{_describe(result)}"
    )
    assert result.stdout.startswith("LEAKED:"), _describe(result)
    leaked = result.stdout.strip().removeprefix("LEAKED:").split(",")
    assert all(name.startswith("app.adviser.llm") for name in leaked), _describe(result)
    assert len(leaked) >= 2, f"only {leaked} reported; the control is barely exercised"


def test_a_probe_failure_is_not_reported_as_a_leak() -> None:
    """The two outcomes must stay distinguishable, or the diagnosis misleads.

    A probe that cannot run exits non-zero with an empty stdout; a probe that
    finds a leak exits non-zero with ``LEAKED:`` on stdout. The tests above key
    off stdout rather than the exit code for exactly this reason.
    """

    result = _probe(("app.adviser.this_module_does_not_exist",))
    assert result.returncode != 0, _describe(result)
    assert not result.stdout.startswith("LEAKED:"), (
        "a probe that failed to import reported itself as a leak; the guard would "
        f"accuse the wrong code: {_describe(result)}"
    )
    assert "ModuleNotFoundError" in result.stderr, _describe(result)


def test_every_probed_module_exists() -> None:
    """A rename must redden this file rather than silently empty the guard.

    The probe constrains only the modules it names. If one is renamed away, the
    import raises, which the assertions above do catch - but only because they
    check stdout as well as the exit code. This states the requirement directly,
    against the files on disk, so the reason is legible without running Python.
    """

    assert CORE_MODULES, "the probe list is empty; the guard would constrain nothing"
    assert LEAKING_MODULE not in CORE_MODULES, (
        "the positive control's module is also claimed as deterministic core; the "
        "two tests would contradict each other"
    )
    for dotted in CORE_MODULES + (LEAKING_MODULE,):
        relative = Path(*dotted.split(".")).with_suffix(".py")
        assert (_APP_DIR / relative).is_file(), (
            f"{dotted} is probed but {relative.as_posix()} does not exist"
        )
    assert _ADVISER_DIR.is_dir()
