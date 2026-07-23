# Provider Adapter Strategy

## Cloudflare

First vertical slice. Use official Cloudflare MCP servers, documentation, changelogs/RSS, pricing and limits pages, and public APIs.

**F005 slice 1 (official free-tier extraction).** The first end-to-end
extraction targets the official Workers and Pages free-tier limits pages on
`developers.cloudflare.com`. Two declarative HTML extraction profiles —
`cloudflare_workers_limits` and `cloudflare_pages_limits` — live as *data* in
`app.ingest.adapters.html.HTML_EXTRACTION_PROFILES`; the generic
`HtmlDocAdapter` (reached only through the Fetcher seam) walks the profile's
selected table and maps header labels to fact fields. Each profile reads one
offer-centric row per product (`service`, `offer_type=always_free`,
`requires_card=No`, `has_paid_dependencies=No`, plus per-limit columns). Every
per-limit value is coerced verbatim as `text` (never `list`), so a real quota
such as `100,000/day` is captured exactly rather than split on its thousands
separator; a column that is absent yields `None` (UNKNOWN) — never a guessed
number. Extraction is deterministic and reproducible: the same captured fixture
always yields identical `CandidateFacts` and an identical content hash
(`tests/fixtures/ingest/cloudflare/html/<source id>/`, driven offline by
`FixtureFetcher`).

The `config/examples/providers/cloudflare.example.yaml` sources are synced into
`provider`/`source` rows by `app.ingest.config_sync.sync_provider` (idempotent on
`Provider.slug` / `Source.slug`) and scanned by
`app.ingest.runner.run_provider_scans` (offline, producing only pre-publication
`candidate` + official `evidence`; there is no publication path in this slice).

### YAML → database field bridge

`app.ingest.config_sync` is the single place that reconciles the config field
names with the ORM `source` columns:

| YAML (`config.models.Source`) | database (`models.domain.Source`)   |
| ----------------------------- | ----------------------------------- |
| `id`                          | `slug` (idempotent-sync key)        |
| `type`                        | `adapter_type`                      |
| `url`                         | `endpoint`                          |
| `extraction_profile`          | `parser_profile`                    |
| `schedule_ref`                | `schedule`                          |
| `trust_level`                 | `trust_level` (+ derived `official`)|

`Provider.type` has no counterpart in the config schema; the sync records the
neutral default `"cloud"` (structural metadata, never an offer fact).

## GitHub

Use official GitHub MCP, Docs, changelog, REST/GraphQL APIs, and plan/Actions/Pages documentation.

## AWS

Use:

- AWS Free Tier API (`GetFreeTierUsage`)
- Official AWS Free Tier pages and docs
- AWS MCP Server documentation search
- Service pricing pages
- Price List APIs only where appropriate

AWS states that bulk price lists are not a complete source for limited-period Free Tier offers.

Use `costgoat/aws-free-tier` for regression topics and gotcha test ideas only. Do not ingest or copy its tables because no licence file was found.

## Google Cloud

Use managed Google/Google Cloud MCP servers, free-program docs, product pricing, release-note data/feeds, and public APIs.

## Azure

Use Microsoft Learn MCP, Azure free/pricing pages, Azure updates, Azure Retail Prices API where useful, and Azure MCP for deployment/operational verification.

## Vercel

Use official Vercel MCP, plans/limits docs, changelog, and public APIs.

## Oracle Cloud

Use Oracle free-tier and service docs, changelogs/release notes, APIs, and database-specific MCP only where relevant.

## Provider onboarding requirements

A new provider needs:

1. Provider YAML
2. Approved official domains
3. One or more adapters
4. Category coverage declaration
5. Parser/extraction fixtures
6. Publication rules
7. Evidence-location strategy
8. Health checks
9. Documentation
10. Tests

## Reliability hierarchy

1. Structured official API/dataset
2. Official RSS/changelog
3. Official static docs
4. Official browser-rendered page
5. Official MCP retrieval
6. Manual official evidence
7. Community source for discovery only
