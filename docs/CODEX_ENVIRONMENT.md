# Codex Environment

This document keeps Codex (and other automated agents) environments **thin**: the
repository owns the setup and verification commands, so an agent environment only
needs the base runtimes plus a checkout. Do not hard-code bespoke setup steps in
an agent UI that duplicate the repository scripts.

## Base runtimes expected

- Docker with a running daemon (Linux containers)
- Node.js 20+ and npm
- Python 3.13+
- PowerShell 7+ or a POSIX shell (Git Bash on Windows)

Confirm with:

```bash
scripts/check-env.sh      # or: pwsh -File scripts/check-env.ps1
```

## Canonical commands (single source of truth)

| Purpose                | POSIX                     | PowerShell                              |
| ---------------------- | ------------------------- | --------------------------------------- |
| Verify runtimes        | `scripts/check-env.sh`    | `pwsh -File scripts/check-env.ps1`      |
| Bootstrap dependencies | `scripts/bootstrap-dev.sh`| `pwsh -File scripts/bootstrap-dev.ps1`  |
| Run tests              | `scripts/test.sh`         | `pwsh -File scripts/test.ps1`           |
| Full check suite       | `scripts/check.sh --node-audit` | `pwsh -File scripts/check.ps1 -NodeAudit` |
| Start stack            | `scripts/stack-up.sh`     | `pwsh -File scripts/stack-up.ps1`       |
| Smoke the stack        | `scripts/stack-smoke.sh`  | `pwsh -File scripts/stack-smoke.ps1`    |
| Stop stack             | `scripts/stack-down.sh`   | `pwsh -File scripts/stack-down.ps1`     |

All scripts resolve the repository root from their own path, so they can be
invoked from any working directory.

## Safety rules for agent environments

- Scripts must **fail actionably** when a required runtime is missing.
- Scripts must **not** print secrets, tokens, or full environment dumps.
- No unapproved network access from tests, scripts, or application code.
- `.env` is git-ignored and holds non-secret local values only. Never commit real
  credentials.
- Prefer delegating to `scripts/init`, `scripts/smoke`, `scripts/test`, and the
  stack scripts rather than duplicating setup logic in the agent UI.

## Cloud vs local

Local Codex is preferred for scaffold work because it needs local Docker. Cloud
Codex may run bounded tasks that do not require local Docker Desktop, credentials,
or network access. See `docs/CODEX_AUTONOMY_POLICY.md` for the full policy.

## What exists in this slice

- `apps/api` — FastAPI service with `/health` and `/health/ready`
- `docker-compose.yml` — `postgres` + `api`
- `migrations/` — Alembic baseline migration

Worker, scheduler, and the React frontend are deferred to F002 slice 2.
