#!/usr/bin/env python3
"""Refresh ``.secrets.baseline`` without wiping or backslashing it.

Why a wrapper rather than a documented command
----------------------------------------------
Measured, not recalled: ``detect-secrets scan --baseline .secrets.baseline``
rewrites the file IN PLACE, exits 0 SILENTLY, and on Windows emits every result
key with backslashes, because ``convert_local_os_path`` rewrites ``/`` to
``os.sep`` on load and on scan while ``SecretsCollection.json()`` never converts
back. It also writes CRLF line endings on Windows and injects an extra
``is_baseline_file`` filter the committed file does not carry.

So the raw command cannot be made safe by telling people to be careful; it has to
be wrapped. This wrapper runs the same refresh and then:

* normalises every result key and entry ``filename`` back to posix;
* drops the injected ``is_baseline_file`` filter, which every invocation route in
  this repository re-adds at run time by passing ``--baseline`` anyway, so the
  committed shape stays stable and diffs stay small;
* sorts result keys and writes LF with a trailing newline, matching the
  committed file byte-for-byte in style;
* REFUSES to write when a tracked file would disappear or a per-file entry count
  would drop, restoring the original bytes instead. Losing an entry is the
  failure this whole slice exists to prevent, so it is never the silent default.

Exit codes: 0 when the baseline is already current or was safely updated, 1 when
the refresh was REFUSED and the original restored.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from check_secrets_baseline import (  # noqa: E402
    BASELINE_NAME,
    REPO_ROOT,
    direction_problems,
    entry_problems,
    existence_problems,
    posix_key_problems,
)

INJECTED_FILTER = "detect_secrets.filters.common.is_baseline_file"


def normalise(baseline: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with posix result keys, sorted, and the injected filter removed."""
    results = baseline.get("results") or {}
    fixed: dict[str, list[dict[str, Any]]] = {}
    for key in sorted(results, key=lambda k: k.replace("\\", "/")):
        posix = key.replace("\\", "/")
        entries = []
        for entry in results[key]:
            entry = dict(entry)
            if "filename" in entry:
                entry["filename"] = entry["filename"].replace("\\", "/")
            entries.append(entry)
        fixed[posix] = entries

    out = dict(baseline)
    out["results"] = fixed
    filters = out.get("filters_used")
    if isinstance(filters, list):
        out["filters_used"] = [f for f in filters if f.get("path") != INJECTED_FILTER]
    return out


def serialise(baseline: dict[str, Any]) -> str:
    """Match detect-secrets' own format: indent 2, trailing newline, LF."""
    return json.dumps(baseline, indent=2) + "\n"


def run_scan(baseline_path: pathlib.Path, root: pathlib.Path) -> int:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "detect_secrets",
            "scan",
            "--baseline",
            str(baseline_path.name),
        ],
        cwd=root,
    )
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--allow-removals",
        action="store_true",
        help=(
            "permit a tracked file to leave the baseline or an entry count to drop. "
            "Required whenever a scanned fixture is deliberately deleted, so that "
            "losing an entry is always an explicit decision."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what a refresh would change and restore the original bytes",
    )
    args = parser.parse_args(argv)

    root = REPO_ROOT
    baseline_path = root / BASELINE_NAME
    if not baseline_path.is_file():
        print(f"refresh FAILED: {baseline_path} does not exist", file=sys.stderr)
        return 1

    original_bytes = baseline_path.read_bytes()
    original = json.loads(original_bytes.decode("utf-8"))
    backup = baseline_path.with_suffix(baseline_path.suffix + ".orig")
    shutil.copyfile(baseline_path, backup)

    def restore() -> None:
        baseline_path.write_bytes(original_bytes)
        backup.unlink(missing_ok=True)

    try:
        code = run_scan(baseline_path, root)
        if code != 0:
            print(f"refresh FAILED: detect-secrets scan exited {code}", file=sys.stderr)
            restore()
            return 1

        refreshed = normalise(json.loads(baseline_path.read_text(encoding="utf-8")))
        results = refreshed["results"]

        problems = posix_key_problems(results)
        problems += entry_problems(results)
        problems += existence_problems(results, root)
        if not args.allow_removals:
            problems += direction_problems(results, original.get("results") or {})

        if problems:
            restore()
            print(
                f"refresh REFUSED: {len(problems)} problem(s); the original baseline "
                "has been restored byte-for-byte and nothing was changed.",
                file=sys.stderr,
            )
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            print(
                "\nIf a scanned file was deliberately deleted, re-run with "
                "--allow-removals so the loss is an explicit decision.",
                file=sys.stderr,
            )
            return 1

        text = serialise(refreshed)
        if args.dry_run:
            restore()
            changed = text.encode("utf-8") != original_bytes
            print(
                "refresh dry run: the baseline WOULD change."
                if changed
                else "refresh dry run: the baseline is already current."
            )
            return 0

        # newline="" so Windows does not translate LF into CRLF, which is what the
        # raw detect-secrets write does and what the committed file must never have.
        baseline_path.write_text(text, encoding="utf-8", newline="")
        backup.unlink(missing_ok=True)
    except Exception:
        restore()
        raise

    before = original.get("results") or {}
    after = refreshed["results"]
    if text.encode("utf-8") == original_bytes:
        print("refresh complete: the baseline was already current, nothing changed.")
        return 0

    print(
        f"refresh complete: the baseline CHANGED. "
        f"{len(before)} -> {len(after)} tracked file(s), "
        f"{sum(len(v) for v in before.values())} -> {sum(len(v) for v in after.values())} "
        "entr(y/ies), all keys posix, nothing lost. "
        f"Review the diff and `git add {BASELINE_NAME}`."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
