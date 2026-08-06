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
dependencies (`pip install -e ".[dev]"`), installs the root Node dev dependencies
(`npm install`), and — when `apps/web` is present — installs the web frontend
dependencies (`npm install` in `apps/web`). `.venv` and all `node_modules` are
git-ignored.

### npm registry (mirrors / proxies)

The committed `apps/web/package-lock.json` pins **public** `registry.npmjs.org`
URLs, so it works anywhere by default. If your environment blocks or mirrors the
public registry, point npm at your approved feed and npm remaps the lockfile URLs
automatically (no lockfile changes needed):

- Host installs: set `registry=<your-feed>` in your user `~/.npmrc` (keep
  `replace-registry-host=npmjs`, the default).
- The web Docker image: set `NPM_REGISTRY=<your-feed>` in `.env`; it is passed to
  the image build as a build arg (defaults to the public registry).

### Python package index (mirrors / proxies)

The `api` and `worker` images install their dependencies with `pip` from **public
PyPI** by default, so they build anywhere with no configuration. If your network
blocks or mirrors PyPI, point the builds at your approved feed with these build
args, which both Dockerfiles declare:

| Build arg | pip equivalent |
| --- | --- |
| `PIP_INDEX_URL` | `--index-url` |
| `PIP_EXTRA_INDEX_URL` | `--extra-index-url` |
| `PIP_TRUSTED_HOST` | `--trusted-host` (for a feed whose TLS chain the base image does not trust) |

They are declared with **no default**. A build arg that is never supplied is
absent from the build environment rather than set to an empty string, so leaving
them alone reproduces the previous public-PyPI behaviour exactly — the
`pip install` line in each Dockerfile is unchanged and needs no conditional
flags, because pip reads all three names natively from its environment.

Via Compose (`scripts/stack-up.sh`, `stack-up.ps1`, or `docker compose build`),
export the variable or set it in `.env`; Compose forwards it to the `api`,
`worker`, and `scheduler` builds only when it is set:

```bash
export PIP_INDEX_URL=https://packages.example.com/pypi/simple
scripts/stack-up.sh
```

Or for a single image, without involving `.env` at all:

```bash
docker build -f apps/api/Dockerfile \
  --build-arg PIP_INDEX_URL=https://packages.example.com/pypi/simple .
```

Both URLs above are **placeholders**. Feed URLs are environment-specific and
often private, so no default is committed and none should be added: supply yours
at build time.

One caveat worth knowing: a value passed this way is recorded in the built
image's layer history (`docker history --no-trunc` shows it on the `ARG` entry
and on every `RUN` in its scope, even under BuildKit). It does **not** appear in
`docker inspect` and is **not** present in the running container's environment,
but treat an image built against a private feed as carrying that feed's name —
never put credentials in the URL, and don't push such an image to a public
registry.

## Environment configuration

Copy the example environment file and adjust if needed:

```bash
cp .env.example .env
```

`.env` is git-ignored and holds **non-secret local development values only**.
Real credentials are supplied by the deployment environment, never committed.
Compose also provides safe defaults, so `.env` is optional for local runs.

The `stack-up` and `stack-smoke` helper scripts ask Compose for its own resolved
model (`docker compose config`) rather than reading `.env` themselves, so a port
set only in `.env` is honoured by the scripts and by the containers alike.
Compose's precedence still applies: a value exported in your shell overrides the
same key in `.env`.

Variable names (values live in your environment, not the repo):

- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT`
- `DATABASE_URL` — SQLAlchemy URL used by the API
- `APP_ENV`, `API_PORT`
- `WORKER_POLL_INTERVAL_SECONDS`, `SCHEDULER_INTERVAL_SECONDS`, `HEARTBEAT_STALE_SECONDS`
- `WEB_PORT` — host port for the nginx-served web frontend (default `8080`)
- `NPM_REGISTRY` — npm registry used when building the web image (default public)
- `PIP_INDEX_URL`, `PIP_EXTRA_INDEX_URL`, `PIP_TRUSTED_HOST` — optional Python
  package index overrides for the api/worker image builds (default public PyPI)

## The development stack

The stack runs five services: `postgres`, `api`, `worker`, `scheduler`, and
`web`. The `web` service is a Vite + React single-page app built to static assets
and served by nginx; it reverse-proxies `/api/` to the `api` service so the
browser talks to a single origin.

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

- Web frontend: <http://localhost:8080/> (nginx-served SPA; `WEB_PORT` overrides)
- API liveness: <http://localhost:8000/health>
- API readiness: <http://localhost:8000/health/ready> (200 when PostgreSQL is reachable, 503 otherwise)
- OpenAPI docs: <http://localhost:8000/docs>
- Web → API proxy: <http://localhost:8080/api/health> (served through the web container)

The API container runs `alembic upgrade head` on startup, so migrations are
applied automatically. The baseline migration creates a scaffold `app_meta`
table; the real domain model is added in F003.

## Tests and checks

```bash
scripts/test.sh           # pytest, then web unit tests + build when apps/web deps are present
scripts/test.sh --full    # the above + the full repo check suite
scripts/check.sh --node-audit   # ruff, pytest, prettier, eslint, secret scan, audits
```

Unit tests do not need the stack running. `scripts/test` also runs the web unit
tests (`vitest run`) and a production `vite build` when `apps/web/node_modules`
is present; it prints a skip note (never a fake pass) when they are absent. The
optional live integration tests in `tests/integration/` are skipped unless
`ATLAS_STACK_BASE_URL` is set (for example `http://localhost:8000`); the stack
smoke scripts are the authoritative live verification.

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
