"""Repository baseline tests for the FreeTier Atlas F001 foundation.

These tests assert repository invariants rather than application behaviour. They
give ``pytest`` something meaningful to run before any application code exists
and guard the licensing, notice, and agent-state files that F001 establishes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_LICENCE_FILES = [
    "LICENSE",
    "NOTICE",
    "ADDITIONAL_TERMS.md",
    "TRADEMARKS.md",
    "AUTHORS.md",
    "THIRD_PARTY_NOTICES.md",
    "CONTRIBUTING.md",
]

REQUIRED_TOOLING_FILES = [
    "pyproject.toml",
    "package.json",
    ".editorconfig",
    ".gitignore",
    ".pre-commit-config.yaml",
    ".secrets.baseline",
    ".github/workflows/ci.yml",
    ".github/pull_request_template.md",
]

REQUIRED_AGENT_STATE_JSON = [
    "agent-state/feature_list.json",
    "agent-state/current_contract.json",
    "agent-state/evaluation.json",
]


@pytest.mark.parametrize("relative_path", REQUIRED_LICENCE_FILES)
def test_required_licence_file_exists_and_non_empty(relative_path: str) -> None:
    path = REPO_ROOT / relative_path
    assert path.is_file(), f"missing required licence/notice file: {relative_path}"
    assert path.stat().st_size > 0, f"licence/notice file is empty: {relative_path}"


def test_license_is_agpl() -> None:
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text
    assert "Version 3" in text


@pytest.mark.parametrize("relative_path", REQUIRED_TOOLING_FILES)
def test_required_tooling_file_exists(relative_path: str) -> None:
    path = REPO_ROOT / relative_path
    assert path.is_file(), f"missing required tooling file: {relative_path}"


@pytest.mark.parametrize("relative_path", REQUIRED_AGENT_STATE_JSON)
def test_agent_state_json_is_valid(relative_path: str) -> None:
    path = REPO_ROOT / relative_path
    assert path.is_file(), f"missing agent-state file: {relative_path}"
    json.loads(path.read_text(encoding="utf-8"))


def test_feature_list_has_expected_shape() -> None:
    data = json.loads((REPO_ROOT / "agent-state/feature_list.json").read_text(encoding="utf-8"))
    features = data["features"]
    assert isinstance(features, list) and features
    ids = {feature["id"] for feature in features}
    assert {"F000", "F001"}.issubset(ids)
