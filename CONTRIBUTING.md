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
secrets baseline shape check, a detect-secrets scan, and a dependency audit. See
`docs/AGENT_HARNESS.md` if you are an automated agent.

## Changing `.secrets.baseline`

Never refresh the baseline by running `detect-secrets` directly. Use:

```bash
python scripts/refresh_secrets_baseline.py
```

**Why this is not optional.** `detect-secrets` keys its results by OS-native path.
It converts `/` to `os.sep` when it loads a baseline and when it scans a file, but
it never converts back when it saves. The asymmetry is one-way, so on Windows
*every* write path emits `tests\fixtures\...` where the committed file holds
`tests/fixtures/...`. This has corrupted the baseline four times, in three
different disguises:

- entries silently **deleted** instead of updated, because a refresh keyed by
  native path does not match the committed posix keys;
- every filename **rewritten with backslashes**, which the pre-commit hook does
  automatically, and which `detect-secrets scan --baseline` does at exit 0 with no
  output at all;
- **exit code 3 misread as a finding**. It is not. `detect-secrets-hook` exits `0`
  clean, `1` when it finds a secret that is not in the baseline, and `3` *after it
  has already rewritten the baseline file*. If you see 3, the file on disk has
  changed; restore it with `git checkout -- .secrets.baseline` and refresh through
  the wrapper.

The wrapper runs the same scan, then normalises keys back to posix, writes LF,
keeps the committed filter set stable, and **refuses to write** if a tracked file
would vanish or an entry count would drop, restoring the original bytes instead.
Pass `--allow-removals` only when a scanned file was deliberately deleted, so that
losing an entry is always a decision rather than an accident.

`scripts/check_secrets_baseline.py` enforces the result in CI, in
`scripts/check.ps1` / `scripts/check.sh`, and as a pre-commit hook that runs
immediately after `detect-secrets`. It fails the build on a non-posix key, a
per-file entry count that decreased, a tracked file that disappeared, a malformed
digest, or a stale entry naming a deleted file. It deliberately does **not** fail
on `generated_at` churn: a legitimate refresh always updates it, and a check that
fires on correct work only teaches people to bypass it.

## Development environment

- Python 3.13+ with a local virtual environment (`.venv`).
- Node.js 20+ (LTS) with npm.
- Install Python dev tools: `pip install -e ".[dev]"` (or from
  `requirements-dev.txt`).
- Install Node dev tools: `npm install`.
