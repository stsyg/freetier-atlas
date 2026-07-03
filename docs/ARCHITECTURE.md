# Architecture

## Logical components

```text
Public static web application
  ├── Catalogue and comparison
  ├── Project adviser
  ├── Evidence and history
  ├── RSS links
  └── Browser-side ZIP generator
             │
             ▼
FastAPI application
  ├── Public catalogue API
  ├── Adviser API
  ├── Admin API
  ├── RSS generation
  └── Hosting-proof API
             │
             ▼
PostgreSQL
  ├── Providers and services
  ├── Offers and quotas
  ├── Regions and residency
  ├── Evidence and snapshots
  ├── Offer versions and changes
  ├── Scan jobs and source health
  └── Review queue
             ▲
             │
Workers and scheduler
  ├── MCP adapters
  ├── API adapters
  ├── RSS/changelog adapters
  ├── HTML/browser adapters
  ├── Extraction
  ├── Verification
  └── Publication
```

## Technology baseline

- Python 3.13+, FastAPI, Pydantic, SQLAlchemy, Alembic
- React with a static-build-capable framework
- PostgreSQL
- PostgreSQL-backed queue initially
- PostgreSQL full-text/trigram search; pgvector later if justified
- Official Python MCP SDK/client abstraction
- Docker Compose and multi-architecture images
- pytest, frontend unit/e2e tests, provider fixtures
- YAML validated by Pydantic/JSON Schema

## Source adapter contract

Every adapter implements:

- `discover()`
- `fetch()`
- `canonicalize()`
- `extract()`
- `validate()`
- `evidence()`
- `health()`

Adapters return source documents and candidate facts, never directly published offers.

## Verification states

`detected`, `extracting`, `candidate`, `verified`, `verified_with_caveats`, `conflict`, `stale`, `withdrawn`, `rejected`.

## Publication gate

Automatic publication requires:

- approved official source
- schema-complete candidate
- deterministic parsing of material numbers
- reproducible fetch/extraction
- evidence for material claims
- no blocking contradiction
- sufficient confidence
- source freshness within policy

## LLM routing

1. Deterministic parser/rules
2. Local model
3. Free hosted model with consent
4. Commercial model for configured escalation
5. Deterministic fallback

LLMs never receive provider credentials and never publish directly.

## Deployment profiles

Canonical Docker Compose: `web`, `api`, `worker`, `scheduler`, `postgres`.

The public Z0 deployment may separate static frontend, API, database, scheduler, and inference across verified Z0 providers. The exact dynamic-host choice requires real onboarding and quota tests.

## Retention

- Evidence excerpts, URLs, hashes, timestamps: indefinite
- Offer versions and change events: indefinite
- Raw compressed snapshots: 90 days
- Public project descriptions: not persisted
- Operational logs: short configurable retention with prompt/input exclusion
