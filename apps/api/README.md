# FreeTier Atlas API

Minimal FastAPI service introduced in F002 slice 1 of the application scaffold.

## Endpoints

| Method | Path            | Purpose                                                        |
| ------ | --------------- | -------------------------------------------------------------- |
| GET    | `/health`       | Liveness. Always 200 while the process serves.                 |
| GET    | `/health/ready` | Readiness. 200 when PostgreSQL answers `SELECT 1`, else 503.   |
| GET    | `/`             | Service descriptor.                                            |
| GET    | `/docs`         | OpenAPI UI (FastAPI default).                                  |

## Layout

```text
apps/api/
├── app/
│   ├── __init__.py     # version
│   ├── settings.py     # pydantic-settings; reads DATABASE_URL, APP_ENV
│   ├── db.py           # SQLAlchemy engine + SELECT 1 readiness check
│   └── main.py         # FastAPI app + health routes
├── entrypoint.sh       # alembic upgrade head, then uvicorn
├── Dockerfile          # build context = repo root (needs migrations/)
└── requirements.txt    # pinned runtime deps (synced with pyproject.toml)
```

## Configuration

All configuration comes from environment variables (names only in the repo, see
`.env.example`):

- `DATABASE_URL` — SQLAlchemy URL, e.g. `postgresql+psycopg://atlas:atlas@postgres:5432/atlas` <!-- pragma: allowlist secret -->
- `APP_ENV` — environment label (`development` by default)

## Local development

The API is normally run through Docker Compose:

```bash
scripts/stack-up.sh      # build + start postgres + api
scripts/stack-smoke.sh   # verify /health, /health/ready, migrations
scripts/stack-down.sh    # stop and remove containers
```

For unit tests without a database, use `scripts/test.sh` (or `.ps1`).

## Not in this slice

The worker, scheduler, and React frontend are added in F002 slice 2. The domain
model and catalogue tables are F003.
