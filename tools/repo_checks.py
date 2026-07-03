from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__", "node_modules"}
TEXT_SUFFIXES = {
    ".cfg",
    ".conf",
    ".css",
    ".env",
    ".example",
    ".graphql",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsonl",
    ".jsx",
    ".lock",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".scss",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

REQUIRED_LEGAL_FILES = [
    "LICENSE",
    "NOTICE",
    "ADDITIONAL_TERMS.md",
    "TRADEMARKS.md",
    "AUTHORS.md",
    "THIRD_PARTY_NOTICES.md",
    "CONTRIBUTING.md",
]

REQUIRED_FOUNDATION_FILES = [
    *REQUIRED_LEGAL_FILES,
    ".editorconfig",
    ".gitignore",
    ".pre-commit-config.yaml",
    ".github/pull_request_template.md",
    ".github/workflows/ci.yml",
    "docs/BRANCH_PROTECTION.md",
    "package.json",
    "pyproject.toml",
    "scripts/test.ps1",
    "scripts/test.sh",
    "tools/node_check.mjs",
    "tools/repo_checks.py",
]

SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9_]{36,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"fta_test_secret_[A-Za-z0-9_]{16,}"),
]

ENV_DUMP_PATTERNS = [
    re.compile(r"(^|[;&|]\s*)env(\s|$)"),
    re.compile(r"(^|[;&|]\s*)printenv(\s|$)"),
    re.compile(r"Get-ChildItem\s+Env:", re.IGNORECASE),
    re.compile(r"\bdir\s+env:", re.IGNORECASE),
]


@dataclass(frozen=True)
class CheckResult:
    name: str
    failures: list[str]


def run_git_ls_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line.strip() for line in result.stdout.splitlines() if line.strip()]


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def is_text_file(path: Path) -> bool:
    if path.name in {"LICENSE", "Dockerfile", ".npmrc"}:
        return True
    return path.suffix.lower() in TEXT_SUFFIXES


def tracked_text_files() -> list[Path]:
    return [path for path in run_git_ls_files() if path.is_file() and is_text_file(path)]


def controlled_temp_files() -> list[Path]:
    tmp = ROOT / ".tmp"
    if not tmp.exists():
        return []
    return [path for path in tmp.glob("controlled-*") if path.is_file()]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check_required_files() -> CheckResult:
    failures = [path for path in REQUIRED_FOUNDATION_FILES if not (ROOT / path).is_file()]
    return CheckResult("required-files", [f"missing required file: {path}" for path in failures])


def check_license_policy() -> CheckResult:
    failures: list[str] = []
    license_text = read_text(ROOT / "LICENSE") if (ROOT / "LICENSE").exists() else ""
    notice_text = read_text(ROOT / "NOTICE") if (ROOT / "NOTICE").exists() else ""
    pyproject = tomllib.loads(read_text(ROOT / "pyproject.toml")) if (ROOT / "pyproject.toml").exists() else {}
    package = json.loads(read_text(ROOT / "package.json")) if (ROOT / "package.json").exists() else {}

    if "GNU AFFERO GENERAL PUBLIC LICENSE" not in license_text:
        failures.append("LICENSE does not identify the GNU Affero General Public License")
    if "Version 3" not in license_text:
        failures.append("LICENSE does not identify version 3")
    if "AGPL-3.0" not in notice_text:
        failures.append("NOTICE does not mention AGPL-3.0")
    if pyproject.get("project", {}).get("license") != "AGPL-3.0-only":
        failures.append("pyproject.toml must declare AGPL-3.0-only")
    if package.get("license") != "AGPL-3.0-only":
        failures.append("package.json must declare AGPL-3.0-only")
    return CheckResult("license-policy", failures)


def check_json_files() -> CheckResult:
    failures: list[str] = []
    paths = [p for p in run_git_ls_files() if p.suffix == ".json"]
    paths.extend(p for p in controlled_temp_files() if p.suffix == ".json")
    for path in sorted(paths):
        try:
            json.loads(read_text(path))
        except Exception as exc:  # noqa: BLE001 - report parser details.
            failures.append(f"{relative(path)}: invalid JSON: {exc}")
    return CheckResult("json", failures)


def check_toml_files() -> CheckResult:
    failures: list[str] = []
    for path in sorted(p for p in run_git_ls_files() if p.suffix == ".toml"):
        try:
            tomllib.loads(read_text(path))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{relative(path)}: invalid TOML: {exc}")
    return CheckResult("toml", failures)


def check_yaml_files() -> CheckResult:
    failures: list[str] = []
    paths = [p for p in run_git_ls_files() if p.suffix in {".yaml", ".yml"}]
    paths.extend(p for p in controlled_temp_files() if p.suffix in {".yaml", ".yml"})
    for path in sorted(paths):
        stack: list[tuple[int, set[str]]] = [(-1, set())]
        block_scalar_indent: int | None = None
        for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "\t" in raw_line:
                failures.append(f"{relative(path)}:{line_number}: tabs are not allowed in YAML indentation")
            stripped = raw_line.strip()
            indent = len(raw_line) - len(raw_line.lstrip(" "))
            if block_scalar_indent is not None:
                if not stripped:
                    continue
                if indent > block_scalar_indent:
                    continue
                block_scalar_indent = None
            if not stripped or stripped.startswith("#") or stripped == "---":
                continue
            if indent % 2 != 0:
                failures.append(f"{relative(path)}:{line_number}: indentation must use multiples of two spaces")
            while stack and indent <= stack[-1][0]:
                stack.pop()
            if stripped.startswith("- "):
                item = stripped[2:].strip()
                item_keys: set[str] = set()
                if ":" in item:
                    key = item.split(":", 1)[0].strip()
                    if key:
                        item_keys.add(key)
                stack.append((indent, item_keys))
                if item.endswith(":") and item != ":":
                    stack.append((indent + 2, set()))
                continue
            if ":" not in stripped:
                failures.append(f"{relative(path)}:{line_number}: expected a mapping key or list item")
                continue
            key = stripped.split(":", 1)[0].strip()
            if not key or " " in key:
                failures.append(f"{relative(path)}:{line_number}: mapping key must be a non-empty simple token")
                continue
            current_keys = stack[-1][1]
            if key in current_keys:
                failures.append(f"{relative(path)}:{line_number}: duplicate key '{key}' in the same mapping")
            current_keys.add(key)
            value = stripped.split(":", 1)[1].strip()
            if value in {"|", ">", "|-", ">-", "|+", ">+"}:
                block_scalar_indent = indent
            if stripped.endswith(":"):
                stack.append((indent, set()))
    return CheckResult("yaml-structure", failures)


def check_formatting() -> CheckResult:
    failures: list[str] = []
    for path in sorted(tracked_text_files()):
        data = path.read_bytes()
        rel = relative(path)
        if b"\r\n" in data or b"\r" in data:
            failures.append(f"{rel}: text files must use LF line endings")
        if data and not data.endswith(b"\n"):
            failures.append(f"{rel}: file must end with a newline")
        for line_number, line in enumerate(data.splitlines(), start=1):
            if rel.endswith(".md"):
                continue
            if line.rstrip(b" \t") != line:
                failures.append(f"{rel}:{line_number}: trailing whitespace")
    return CheckResult("formatting", failures)


def check_secret_patterns() -> CheckResult:
    failures: list[str] = []
    scan_paths = sorted(set(tracked_text_files() + controlled_temp_files()))
    for path in scan_paths:
        rel = relative(path)
        text = read_text(path)
        for line_number, line in enumerate(text.splitlines(), start=1):
            for pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    failures.append(f"{rel}:{line_number}: potential secret matched {pattern.pattern}")
    return CheckResult("secret-scan", failures)


def check_script_safety() -> CheckResult:
    failures: list[str] = []
    paths = [path for path in tracked_text_files() if relative(path).startswith(("scripts/", ".github/workflows/"))]
    for path in sorted(paths):
        rel = relative(path)
        for line_number, line in enumerate(read_text(path).splitlines(), start=1):
            for pattern in ENV_DUMP_PATTERNS:
                if pattern.search(line):
                    failures.append(f"{rel}:{line_number}: avoid printing full environment variables")
    return CheckResult("script-safety", failures)


def check_dependency_policy() -> CheckResult:
    failures: list[str] = []
    package = json.loads(read_text(ROOT / "package.json"))
    pyproject = tomllib.loads(read_text(ROOT / "pyproject.toml"))

    for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        deps = package.get(field, {})
        if not isinstance(deps, dict):
            failures.append(f"package.json: {field} must be an object")
            continue
        for name, version in deps.items():
            if not isinstance(version, str) or version.startswith(("^", "~", "*", ">")):
                failures.append(f"package.json: dependency {name} in {field} must be exactly pinned")

    dependencies = pyproject.get("project", {}).get("dependencies", [])
    if not isinstance(dependencies, list):
        failures.append("pyproject.toml: project.dependencies must be a list")
    for dependency in dependencies:
        if any(marker in dependency for marker in (">=", ">", "~=", "*")) and "==" not in dependency:
            failures.append(f"pyproject.toml: dependency must be exactly pinned for now: {dependency}")

    lock_files = ["package-lock.json", "requirements.lock", "uv.lock", "poetry.lock"]
    present_lock_files = [path for path in lock_files if (ROOT / path).exists()]
    if dependencies and not any(path in present_lock_files for path in ("requirements.lock", "uv.lock", "poetry.lock")):
        failures.append("Python dependencies require a committed lock file")
    if any(package.get(field) for field in ("dependencies", "devDependencies")) and "package-lock.json" not in present_lock_files:
        failures.append("Node dependencies require package-lock.json")
    return CheckResult("dependency-policy", failures)


def check_no_unsupported_free_claims() -> CheckResult:
    failures: list[str] = []
    risky = re.compile(r"\b(always\s+free|free\s+tier|no\s+cost|costs?\s+\$0)\b", re.IGNORECASE)
    allowed = {
        "README.md",
        "PLAN.md",
        "docs/PRODUCT_REQUIREMENTS.md",
        "docs/PROVIDER_ADAPTERS.md",
        "docs/SOURCE_REUSE_AND_PROVENANCE.md",
        "docs/DECISIONS.md",
        "docs/HOSTING_Z0.md",
        "docs/MVP_ACCEPTANCE.md",
        "docs/TEST_STRATEGY.md",
        "docs/LICENSING.md",
        "docs/CODEX_AUTONOMY_POLICY.md",
        "CODEX_TASKS.md",
        "AGENTS.md",
        "CONTRIBUTING.md",
        "agent-state/current_contract.json",
        "agent-state/feature_list.json",
        "agent-state/evaluation.json",
        "agent-state/progress.md",
    }
    for path in tracked_text_files():
        rel = relative(path)
        if rel in allowed or rel.startswith("config/examples/"):
            continue
        for line_number, line in enumerate(read_text(path).splitlines(), start=1):
            if risky.search(line):
                failures.append(f"{rel}:{line_number}: avoid unsupported free-tier or Z0 claims")
    return CheckResult("unsupported-claims", failures)


def run_all() -> int:
    checks = [
        check_required_files,
        check_license_policy,
        check_json_files,
        check_toml_files,
        check_yaml_files,
        check_formatting,
        check_secret_patterns,
        check_script_safety,
        check_dependency_policy,
        check_no_unsupported_free_claims,
    ]
    failed = False
    for check in checks:
        result = check()
        if result.failures:
            failed = True
            print(f"FAIL {result.name}", file=sys.stderr)
            for failure in result.failures:
                print(f"  - {failure}", file=sys.stderr)
        else:
            print(f"PASS {result.name}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run FreeTier Atlas repository foundation checks.")
    parser.add_argument("command", choices=["all"], nargs="?", default="all")
    args = parser.parse_args(argv)
    if args.command == "all":
        return run_all()
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
