"""Validated YAML config-diff view (READ / VALIDATE only -- never writes).

The admin surface can paste a *candidate* configuration and see (a) whether it
passes the EXISTING config validators (:func:`app.config.loader.load_and_validate`,
which rejects malformed YAML, inline secrets, and schema violations) and (b) a
unified diff against the running/committed configuration the API actually loads.

This endpoint never writes configuration anywhere. The candidate is validated in
a throwaway temporary file (the loader takes a path) that is always deleted, and
the committed side is read-only. There is no user-controlled server path: the
committed path is chosen by the server (the running ``llm_config_path``); only
the candidate *text* comes from the admin and it is fully validated before being
shown back.
"""

from __future__ import annotations

import difflib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config.loader import ConfigError, load_and_validate


@dataclass(frozen=True)
class ConfigDiffResult:
    """Outcome of validating a candidate config and diffing it vs the running one."""

    target: str
    valid: bool
    problems: list[str]
    diff: list[str]
    committed_present: bool


def _validate_candidate(candidate_text: str) -> list[str]:
    """Validate candidate YAML via the existing loader; return problems (empty=ok).

    The text is written to a private temporary file because the loader operates
    on a path; the file is removed in all cases and is never placed in any config
    directory.
    """

    handle, tmp_name = tempfile.mkstemp(prefix="atlas-config-candidate-", suffix=".yaml")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as tmp:
            tmp.write(candidate_text)
        try:
            load_and_validate(tmp_name)
        except ConfigError as exc:
            return list(exc.problems)
        except ValueError as exc:  # defensive: unexpected loader error
            return [str(exc)]
        return []
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def build_config_diff(candidate_text: str, committed_path: str | None) -> ConfigDiffResult:
    """Validate ``candidate_text`` and diff it against the committed config.

    ``committed_path`` is the running configuration path (server-chosen). When it
    is missing or unset the committed side is treated as empty and the diff shows
    the candidate as all-additions; ``committed_present`` reports which case.
    """

    problems = _validate_candidate(candidate_text)

    committed_text = ""
    committed_present = False
    target = committed_path or "<no running config configured>"
    if committed_path:
        path = Path(committed_path)
        if path.exists():
            committed_text = path.read_text(encoding="utf-8")
            committed_present = True

    diff = list(
        difflib.unified_diff(
            committed_text.splitlines(),
            candidate_text.splitlines(),
            fromfile=f"committed:{target}",
            tofile="candidate",
            lineterm="",
        )
    )

    return ConfigDiffResult(
        target=target,
        valid=not problems,
        problems=problems,
        diff=diff,
        committed_present=committed_present,
    )


__all__ = ["ConfigDiffResult", "build_config_diff"]
