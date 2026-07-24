"""Guard: the deterministic adviser core must not import the LLM package.

The recommendation must remain a pure function of the structured request and the
published catalogue. Importing the deterministic core
(``recommend`` / ``select`` / ``schema`` / ``schemas``) must therefore NOT pull
in ``app.adviser.llm`` (which depends on providers/config). This is asserted in
a *fresh* subprocess so it is unaffected by whatever the test process already
imported (the router legitimately imports both).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[2] / "apps" / "api"

_PROBE = """
import sys
import app.adviser.recommend  # noqa: F401
import app.adviser.select  # noqa: F401
import app.adviser.schema  # noqa: F401
import app.adviser.schemas  # noqa: F401

leaked = sorted(m for m in sys.modules if m.startswith("app.adviser.llm"))
if leaked:
    print("LEAKED:" + ",".join(leaked))
    sys.exit(1)
print("OK")
"""


def test_core_does_not_import_llm_package() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=str(_APP_DIR),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "deterministic adviser core imported the LLM package: "
        f"{result.stdout.strip()} {result.stderr.strip()}"
    )
    assert result.stdout.strip() == "OK"
