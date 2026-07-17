"""Command-line entry point for validating FreeTier Atlas configuration.

Usage::

    python -m app.config.cli validate config/examples/application.example.yaml
    python -m app.config.cli emit-schema application

``validate`` returns exit code 0 when every file validates and 1 when any file
fails, printing actionable problems to stderr. ``emit-schema`` prints the JSON
Schema for a configuration family.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .loader import ConfigError, load_and_validate
from .models import FAMILY_MODELS


def _cmd_validate(paths: Sequence[str]) -> int:
    failures = 0
    for raw in paths:
        path = Path(raw)
        try:
            model = load_and_validate(path)
        except ConfigError as exc:
            failures += 1
            print(f"FAIL {exc.path}", file=sys.stderr)
            for problem in exc.problems:
                print(f"       - {problem}", file=sys.stderr)
            continue
        print(f"OK   {path} ({type(model).__name__})")
    total = len(paths)
    if failures:
        print(f"\n{failures} of {total} configuration file(s) failed validation.", file=sys.stderr)
        return 1
    print(f"\nAll {total} configuration file(s) valid.")
    return 0


def _cmd_emit_schema(family: str) -> int:
    model = FAMILY_MODELS.get(family)
    if model is None:
        choices = ", ".join(sorted(FAMILY_MODELS))
        print(f"unknown family {family!r}; choices: {choices}", file=sys.stderr)
        return 2
    print(json.dumps(model.model_json_schema(), indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.config.cli",
        description="Validate FreeTier Atlas declarative YAML configuration.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate one or more YAML config files.")
    validate.add_argument("paths", nargs="+", help="Configuration files to validate.")

    schema = sub.add_parser("emit-schema", help="Print the JSON Schema for a config family.")
    schema.add_argument("family", choices=sorted(FAMILY_MODELS), help="Configuration family.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate":
        return _cmd_validate(args.paths)
    if args.command == "emit-schema":
        return _cmd_emit_schema(args.family)
    parser.error("no command")
    return 2  # pragma: no cover - argparse exits before reaching here


if __name__ == "__main__":
    raise SystemExit(main())
