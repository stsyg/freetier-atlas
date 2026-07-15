# Local Development

This guide covers running FreeTier Atlas on your machine. The repository is the
source of truth for development commands; Codex and CI use these same scripts.

## Prerequisites

| Tool    | Version | Notes                                  |
| ------- | ------- | -------------------------------------- |
| Docker  | 24+     | Engine/daemon must be running.         |
| Node.js | 20+     | Ships with npm.                        |
| Python  | 3.13+   | Used for the API and tooling.          |

Verify everything is present:

```bash
scripts/check-env.sh      # or: pwsh -File scripts/check-env.ps1
```

The check fails with an actionable message if a runtime is missing or the Docker
daemon is not reachable. It never prints secrets or full environment dumps.

## One-time bootstrap

```bash
scripts/bootstrap-dev.sh  # or: pwsh -File scripts/bootstrap-dev.ps1
```

This creates `.venv`, installs the Python project with its runtime and dev
dependencies (`pip install -e ".[dev]"`), and installs Node dev dependencies
(`npm install`). Both `.venv` and `node_modules` are git-ignored.

## Environment configuration

Copy the example environment file and adjust if needed:

```bash
cp .env.example .env
```

`.env` is git-ignored and holds **non-secret local development values only**.
Real credentials are supplied by the deployment environment, never committed.
Compose also provides safe defaults, so `.env` is optional for local runs.

Variable names (values live in your environment, not the repo):

- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT`
- `DATABASE_URL` — SQLAlchemy URL used by the API
- `APP_ENV`, `API_PORT`

## The development stack

Slice 1 of the scaffold runs two services: `postgres` and `api`. The `web`,
`worker`, and `scheduler` services arrive in a later increment.

```bash
scripts/stack-up.sh       # build + start; waits for API liveness
scripts/stack-smoke.sh    # verify /health, /health/ready, and migrations
scripts/stack-down.sh     # stop and remove containers (keeps data)
scripts/stack-down.sh --volumes   # also remove the database volume
```

PowerShell equivalents:

```powershell
pwsh -File scripts/stack-up.ps1
pwsh -File scripts/stack-smoke.ps1
pwsh -File scripts/stack-down.ps1            # -Volumes to drop data
```

### What the stack exposes

- API liveness: <http://localhost:8000/health>
- API readiness: <http://localhost:8000/health/ready> (200 when PostgreSQL is reachable, 503 otherwise)
- OpenAPI docs: <http://localhost:8000/docs>

The API container runs `alembic upgrade head` on startup, so migrations are
applied automatically. The baseline migration creates a scaffold `app_meta`
table; the real domain model is added in F003.

## Tests and checks

```bash
scripts/test.sh           # pytest (unit tests; no database required)
scripts/test.sh --full    # pytest + the full repo check suite
scripts/check.sh --node-audit   # ruff, pytest, prettier, eslint, secret scan, audits
```

Unit tests do not need the stack running. The optional live integration tests in
`tests/integration/` are skipped unless `ATLAS_STACK_BASE_URL` is set (for
example `http://localhost:8000`); the stack smoke scripts are the authoritative
live verification.

## Typical loop

```bash
scripts/check-env.sh
scripts/bootstrap-dev.sh
scripts/test.sh
scripts/stack-up.sh
scripts/stack-smoke.sh
# ... work ...
scripts/stack-down.sh
```
