# FreeTier Atlas worker & scheduler

Slice 2 of the F002 application scaffold. Two long-running Python services that
operate a real **PostgreSQL-backed job queue** — no external broker.

## Services

| Service   | Command                      | Role                                                              |
| --------- | ---------------------------- | ----------------------------------------------------------------- |
| worker    | `python -m worker.main`      | Claims the oldest pending job (`FOR UPDATE SKIP LOCKED`) and runs it. |
| scheduler | `python -m worker.scheduler` | Enqueues a `heartbeat` job on a fixed interval.                   |

Both share this image (`apps/worker/Dockerfile`); the scheduler overrides the
default command in `docker-compose.yml`.

## Job queue

Two infrastructure tables (created by Alembic migration `0002_worker_queue`,
applied by the API service on startup — **not** the F003 domain model):

- `job_queue` — `id, kind, payload (jsonb), status, enqueued_at, started_at,
  finished_at, attempts, locked_by, last_error`.
- `service_heartbeat` — `service (pk), last_beat_at, detail`. Upserted per
  cycle, so restarts never create duplicate rows.

The only job kind in this slice is a no-op `heartbeat` that proves the pipeline
works end to end. Real scan/extraction jobs arrive in F004+.

## Health

Neither service is HTTP, so liveness is database-backed:

```
python -m worker.health --service worker
python -m worker.health --service scheduler
```

Exit `0` only when the database is reachable **and** the service's heartbeat is
fresh within `HEARTBEAT_STALE_SECONDS`; otherwise exit `1`. Output never
contains connection strings or credentials, so it is safe as a Docker health
check.

## Configuration

Environment variables (see the repository `.env.example`); values are non-secret
local development defaults and must be overridden elsewhere:

| Variable                     | Default                        | Meaning                                   |
| ---------------------------- | ------------------------------ | ----------------------------------------- |
| `DATABASE_URL`               | local compose postgres         | SQLAlchemy/psycopg connection URL.        |
| `WORKER_POLL_INTERVAL_SECONDS` | `2.0`                        | Idle poll interval for the worker.        |
| `SCHEDULER_INTERVAL_SECONDS` | `5.0`                          | Enqueue interval for the scheduler.       |
| `HEARTBEAT_STALE_SECONDS`    | `30.0`                         | Freshness threshold for the health check. |
