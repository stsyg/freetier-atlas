# Contributing to FreeTier Atlas

Thank you for your interest in FreeTier Atlas. This guide covers licensing,
branching, commits, pull requests, and the local checks every change must pass.

## Licence and provenance

- By contributing, you agree your contributions are licensed under **AGPL-3.0**
  (see `LICENSE`) and that you have the right to submit them.
- **Official sources are authoritative.** Never publish an unsupported claim that
  a service is free. Community lists (see `THIRD_PARTY_NOTICES.md`) may only
  suggest candidates; facts require official evidence.
- Never commit secrets, credentials, tokens, or `.env` values.

## Branching (trunk-based)

- `main` is protected and always releasable.
- Use short-lived feature branches named `type/short-description`
  (for example `feat/cloudflare-adapter`, `fix/quota-parsing`).
- Provider work may use a provider-scoped branch (for example
  `provider/cloudflare`).
- Do not add a permanent `develop` branch.
- Open a **draft pull request early**; keep branches short-lived and rebased on
  `main`.

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(optional scope): <summary>
```

Allowed types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `build`,
`perf`. Keep the summary in the imperative mood and under ~72 characters. Keep
each commit focused; do not mix unrelated changes.

## Pull requests

- Fill in `.github/pull_request_template.md`.
- Keep PRs small and reviewable; one feature or one tightly coupled change.
- All CI checks must pass. Do not weaken or delete tests to make a check pass.
- PRs are merged by the owner. Contributors do not merge their own PRs.

## Protected `main` guidance

The owner should enable, on GitHub, for `main`:

- Require a pull request before merging (no direct pushes).
- Require the CI status check (`.github/workflows/ci.yml`) to pass.
- Require branches to be up to date before merging.
- Require conversation resolution.
- Disallow force pushes and branch deletion.

## Local development checks

Before pushing, run the same checks CI runs:

```powershell
# Windows PowerShell
scripts/check.ps1
```

```bash
# Linux/macOS/Git Bash
scripts/check.sh
```

These run: Ruff lint, Ruff format check, pytest, Prettier check, ESLint, a
detect-secrets scan, and a dependency audit. See `docs/AGENT_HARNESS.md` if you
are an automated agent.

## Development environment

- Python 3.13+ with a local virtual environment (`.venv`).
- Node.js 20+ (LTS) with npm.
- Install Python dev tools: `pip install -e ".[dev]"` (or from
  `requirements-dev.txt`).
- Install Node dev tools: `npm install`.
