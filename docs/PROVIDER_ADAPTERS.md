# Provider Adapter Strategy

## Cloudflare

First vertical slice. Use official Cloudflare MCP servers, documentation, changelogs/RSS, pricing and limits pages, and public APIs.

**F005 slice 1 (official free-tier extraction).** The first end-to-end
extraction targets the official Workers and Pages free-tier limits pages on
`developers.cloudflare.com`. Two declarative HTML extraction profiles —
`cloudflare_workers_limits` and `cloudflare_pages_limits` — live as *data* in
`app.ingest.adapters.profiles.cloudflare` (registered into
`app.ingest.adapters.html.HTML_EXTRACTION_PROFILES` through the seam described
under [Provider onboarding requirements](#extraction-profile-registration-seam-f008-s3));
the generic
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

### Category coverage declaration (item 4)

Item 4 is **complete** as of F008 slice S2. It has two halves.

**(a) Service → category mapping.** A provider YAML declares `service_categories:`, a mapping
from a service's canonical name (exactly as it appears in the extracted candidate facts' `service`
field) to one of the fourteen canonical category slugs in `apps/api/app/read_api/taxonomy.py`. An
unknown slug fails config validation at load with an actionable error naming the provider, the
service, the bad slug, and the valid slug list.

The mapping is applied in two places, both idempotent and both slug-keyed:

- `ingest.config_sync.categorise_services()` back-fills already-existing services on every
  `sync_provider` run;
- `publish.publisher._resolve_service()` sets the category when a service row is first created and
  back-fills it on re-publish.

An undeclared service stays uncategorised (`category_id IS NULL`) and is surfaced honestly in the
`uncategorized` rollup. A category is **never inferred from a service name** — declare it with a
rationale or leave it unknown. The declaration is **withdrawable**: deleting a service's entry from
`service_categories` reverts `service.category_id` to `NULL` on the next sync rather than leaving
the old category in place.

**(b) Explicit coverage declaration.** A provider YAML must carry a `coverage:` block keyed by
category slug. It is **mandatory** and must contain **exactly the fourteen** canonical slugs — a
missing slug fails validation with the missing slugs listed, an unknown slug fails with the valid
list. Each entry is:

```yaml
coverage:
  serverless-functions:
    state: verified_free          # one of the seven COVERAGE_STATES
    source: cloudflare-workers-limits    # a source id declared in the same file
  compute-vms:
    state: not_offered
    rationale: >-                 # required for not_offered
      Cloudflare publishes no general-purpose VM/IaaS product.
```

Validation mirrors the DB CHECK constraints, so a bad config fails at load rather than at INSERT:
`not_offered` requires a non-empty `rationale`; `verified_free` / `offered_no_z0` require a `source`
or an `evidence_url`; a named `source` must be declared in the same file.

`ProviderConfig.validate_coverage_floor()` additionally enforces an **evidence floor** (decision
Q9-A): at least **three** entries must be `verified_free` or `offered_no_z0` **and** carry a
`source` or `evidence_url`. This makes an all-`unknown` provider YAML **unloadable** — a new
provider slice cannot ship a declaration block that asserts nothing.

`ingest.config_sync.sync_coverage()` upserts the block into `provider_category_coverage` on
`(provider_id, category_id)`. It is idempotent, convergent (a changed state overwrites) and prunes
rows for pairs the YAML no longer declares — but only in a run where every category reference resolved. An
unresolvable **`source`** reference keeps its pair registered as declared and leaves the stored row
untouched. An unresolvable **category** slug (drift such as a rename, which the FK's cascade does not
cover because the category row still exists) makes withdrawal unattributable, so the prune is
suppressed for the whole run. Either way a resolution failure is never treated as a withdrawal, and
both surface in the `unknown_source` / `unknown_category` outcome counts.

The evidence floor is re-checked **after** the sync against the rows actually persisted, so it is a
database invariant rather than only a config-load one. If fewer than three persisted rows are
evidence-backed, `sync_coverage()` raises `CoverageFloorError`; zero rows is the maximal erosion, not
an exemption. The shortfall does not commit, and that is structural rather than a property of the
callers: `sync_provider()` wraps all four of its writes in a **SAVEPOINT** which it rolls back before
re-raising, whichever of the four raised, so a sync that fails in those four writes leaves the
provider entirely untouched even if the caller swallows the exception and commits. The original exception is re-raised unchanged
in type and identity; a rollback that itself fails is attached to it as a note rather than replacing
it, and a note that itself fails to attach is discarded rather than allowed to displace it. The
savepoint covers the whole provider unit, not just the coverage block — a coverage-only savepoint
would commit a new provider with zero coverage rows. The one failure this does not cover is one
raised while the SAVEPOINT is being *released* — an `after_transaction_end` listener, dispatched once
`RELEASE SAVEPOINT` has already succeeded — because the writes have joined the caller's transaction
by then; no module under `apps/` registered such a listener at the time of writing, verified by
inspection -- a point-in-time observation rather than a standing property -- and a test asserts that importing the whole app
package in a fresh interpreter registers no new *class-level* `after_transaction_end` listener on
`Session` or a subclass, by querying SQLAlchemy's own listener registry rather than scanning source
text. That check has two documented limits -- an instance-level registration and one deferred inside a
function import never runs -- recorded in the test itself. See
`DATA_MODEL.md` for that boundary and why it is documented rather than guarded.

Finally, a pair declared `unknown` or `not_offered` while the derivation from published evidence
says `verified_free` / `offered_no_z0` is a **material contradiction**: it raises a pending
`review_item` and is asserted against by the reusable helper in `tests/support/coverage.py`
(`assert_no_coverage_contradictions()` for a DB session, `assert_declarations_match_signals()` for a
DB-free unit test). **A provider slice that silently declares `unknown` over a real published offer
fails its own tests.** Call it from your provider's test module.

With coverage declared, a category with no published offers for a provider is no longer ambiguous:
`not_offered` (deliberate, with a rationale) is now distinguishable from `unknown` (not yet
ingested), and the read API no longer guesses either.

### Extraction-profile registration seam (F008 S3)

Provider-specific extraction knowledge is **data**, and each provider owns one
module: `app/ingest/adapters/profiles/<provider>.py`. That module registers its
profiles through the package seam and is the **only** file a provider slice adds:

```python
from ..html import HtmlColumn, HtmlExtractionProfile
from . import register_html_profile

MY_PROVIDER_LIMITS = register_html_profile(
    HtmlExtractionProfile(
        name="myprovider_limits",          # unique across all providers
        table_id="myprovider-free-tier",
        columns={"service": HtmlColumn("service", "text"), ...},
    )
)
```

`register_json_profile` and `register_mcp_profile` are the structured-API and MCP
equivalents. `app.ingest.adapters` calls `load_provider_profiles()` at import
time, which imports every module in the package via `pkgutil`, so **dropping the
file in is the whole integration step**: no shared registry dict is edited, no
`__init__` list is appended to, and no other provider's file is touched. That is
what makes several provider slices safe to build concurrently — their footprints
are disjoint by construction.

Registration is additive only. A duplicate profile name raises
`ProfileConflictError` rather than silently shadowing the existing profile;
re-registering the identical object is a harmless no-op. Convention for names:
`<provider>_<document>`.

`app/ingest/adapters/html.py` keeps only the generic, provider-agnostic shapes
(`quota_document`, `pricing_document`). `profiles/cloudflare.py` is the template
to copy.

### Fixture layout and capture workflow

```
tests/fixtures/ingest/<provider>/<adapter>/<case>/
    source.html | source.json | source.xml
    expected.json
    capture.json
```

`--fixtures` (the runner's `--fixtures-root`) points at one
`tests/fixtures/ingest/<provider>/<adapter>` directory. `build_fixture_fetcher`
resolves `<source id>/source.<ext>` first, then a flat `<source id>.<ext>`, and
serves it with the MIME implied by the source's **declared** `type` —
`html` → `text/html`, `rss` → `application/rss+xml`, `reference-json` /
`structured-api` → `application/json`. The content type is never sniffed from the
bytes and never defaulted; an unresolvable or ambiguous fixture is simply not
registered, so the fetch is a graceful not-found rather than a guess or a network
reach.

Capture is **owner-run only**, never invoked by tests or CI:

```
python scripts/capture_fixture.py config/examples/providers/<provider>.example.yaml \
    --source <source id> \
    --out tests/fixtures/ingest/<provider>/<adapter>/<case> \
    --robots-allowed yes --tos-note "checked <date>" --yes-i-am-the-owner
```

It fetches through `LiveFetcher` with the provider's own official-domain
allowlist and writes `source.<ext>` plus a `capture.json` provenance sidecar
(`url`, `fetched_at`, `http_status`, `sha256_original`, `sha256_stored`,
`trim_method`, `robots_allowed`, `tos_note`, `captured_by`). Commit **minimal
official excerpts only**, with attribution in `tests/fixtures/ingest/README.md` —
not bulk mirrored pages. CI validates the sidecar's presence and hash, and
deliberately never asserts freshness.

## Reliability hierarchy

1. Structured official API/dataset
2. Official RSS/changelog
3. Official static docs
4. Official browser-rendered page
5. Official MCP retrieval
6. Manual official evidence
7. Community source for discovery only
