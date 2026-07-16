# Database migrations

Alembic migrations for FreeTier Atlas.

- Configuration: `alembic.ini` (repo root). The database URL is read from
  `DATABASE_URL` at runtime and is never stored in the repository.
- Environment: `migrations/env.py`.
- Revisions: `migrations/versions/`.

## Commands

Run from the repository root with `DATABASE_URL` set (the API container runs
`alembic upgrade head` automatically on startup):

```bash
alembic upgrade head        # apply all migrations
alembic downgrade -1        # roll back one revision
alembic current             # show the applied revision
alembic history             # list revisions
```

## Slice 1 baseline

`0001_scaffold_baseline` creates a scaffold `app_meta` key/value table and seeds
a `scaffold_initialized` marker. The real domain model arrives in F003.
