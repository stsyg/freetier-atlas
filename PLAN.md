# FreeTier Atlas — Master MVP Plan

**Status:** Ready to begin implementation  
**Plan date:** 2026-07-03  
**Owner:** Sergiy (`stsyg`)  
**Repository:** public monorepo under the owner's personal GitHub account  
**Local working folder:** `freetier-atlas`

## Product objective

Build a portable, containerized application that discovers, verifies, classifies, and presents free cloud and developer services. It also recommends credible architectures that remain at exactly $0 whenever possible.

FreeTier Atlas is not merely another list. Its differentiators are:

1. Official evidence for every published claim.
2. Autonomous monitoring through MCP, APIs, RSS/Atom, changelogs, documentation, and web extraction.
3. Deterministic verification of quotas, dates, regions, eligibility, credit-card requirements, paid dependencies, and quota-exhaustion behaviour.
4. Strict Z0 safety classification.
5. Historical change tracking and contradiction handling.
6. A project adviser that produces a portable Docker Compose deployment package.
7. A public deployment proving the application itself can operate at $0.

## Non-negotiable principles

- Official sources are authoritative.
- LLMs may extract and explain; they may not independently establish that an offer is free.
- The deterministic adviser must work when all LLM routes are unavailable.
- Recommendation priorities are: exactly $0, portability, low vendor lock-in.
- Multi-provider recommendations are allowed.
- Public users need no account.
- Project descriptions are not persisted by default.
- Generated files are built in the browser and downloaded as an ephemeral ZIP.
- YAML stores declarative configuration; PostgreSQL stores operational data.
- The complete application runs through Docker Compose.
- The public deployment may distribute components across multiple verified Z0 services.

## MVP deliverables

### Public catalogue

- Provider cards and all-category coverage matrix
- Search, filters, sorting, and comparison
- Availability regions and data residency
- Card requirement, paid dependencies, eligibility, duration, and commercial-use conditions
- Quotas, periods, and exhaustion behaviour
- Z0/Z1/Z2/Z3 classification
- Simple public verification labels plus advanced numeric confidence
- Official evidence and permanent offer history
- Provider completeness and freshness scores

### Monitoring and verification

Adapters:

- MCP
- REST/GraphQL API
- RSS/Atom
- Structured datasets
- GitHub releases/repositories
- Official changelogs
- Static and browser-rendered HTML
- Manual YAML overrides

Pipeline:

1. Fetch and canonicalize.
2. Hash and detect change.
3. Extract deterministically where possible.
4. Use an LLM only where useful.
5. Validate schema, numbers, dates, and units.
6. Compare with prior versions.
7. Check contradictions.
8. Publish only after policy gates pass.
9. Route difficult cases to admin review.

### Architecture adviser

- Natural-language input
- Editable structured requirements
- Deterministic guided mode
- Portable Z0 recommendation
- Simpler alternative
- Requirement reductions when Z0 is impossible
- Self-hosted fallback
- Cost-safety, portability, lock-in, operations, durability, and upgrade-path scoring
- Browser-generated ZIP

### Admin

GitHub-authenticated and allowlisted:

- Source-health dashboard
- Manual scan trigger
- Evidence and scan inspection
- Review queue
- Validated YAML editing with diff
- LLM budgets and circuit breakers
- Hosting usage and Z0 compliance

### Alerts

MVP: public RSS and Discord.  
Later: web push.

## Implementation phases

### Phase 0 — Foundation

Create public repository, licence/notice files, monorepo scaffolding, CI, Docker Compose smoke test, and ADRs.

### Phase 1 — Domain model

Implement PostgreSQL migrations and typed models for providers, services, offers, quotas, regions, residency, sources, evidence, snapshots, versions, changes, scans, and reviews.

### Phase 2 — Cloudflare vertical slice

Cloudflare is the first complete provider because it offers strong official documentation, changelogs, MCP coverage, and useful Z0 products.

Implement source ingestion, extraction, verification, publication, API, frontend pages, completeness score, and admin review.

### Phase 3 — Catalogue UX

Hybrid homepage, provider cards, search, filters, compare, evidence, history, recent findings, and RSS.

### Phase 4 — Adviser and ZIP

Structured requirements, deterministic matcher, quota calculator, reduction engine, self-hosted fallback, optional LLM enhancement, and browser ZIP.

### Phase 5 — Remaining providers

Recommended order: GitHub, AWS, Google Cloud, Azure, Vercel, Oracle Cloud.

AWS must use official AWS Free Tier APIs, official documentation, pricing pages, and AWS MCP. CostGoat is a regression/checklist reference only.

### Phase 6 — Operations and hardening

Admin, Discord, abuse controls, privacy, retention, observability, security, accessibility, and performance.

### Phase 7 — Public Z0 deployment

Cloudflare Pages primary frontend if onboarding passes Z0 verification, GitHub Pages mirror, distributed dynamic Z0 components, public quota/headroom dashboard, and monthly re-verification.

## Configuration

Use YAML for schedules, providers, verification, notifications, and LLM routing. Use `.env` only for secrets and deployment overrides.

Default cadence:

- RSS/changelog: hourly
- Structured APIs: every 6 hours
- MCP documentation: daily
- Official pages: daily
- Full reconciliation: weekly
- Conflict recheck: immediate controlled retries

## Source reuse

- `255kb/stack-on-a-budget`: MIT; adapt contribution fields and candidate taxonomy with attribution.
- `iSoumyaDey/Awesome-Web-Hosting-2026`: actual `LICENSE` is MIT despite a CC0 README badge; use conservatively with MIT attribution.
- `ripienaar/free-for-dev`: no licence file found; discovery and gap checking only.
- `costgoat/aws-free-tier`: no licence file found; AWS regression/checklist reference only.
- `hashirahmad/Best-always-free-tier-cloud-platforms`: no licence file found and historical values; evaluation-dimension inspiration only.

## Hosting decision

Use Cloudflare Pages as the primary static frontend if a no-payment onboarding test passes, targeting `freetier-atlas.pages.dev`. Publish a GitHub Pages mirror at `stsyg.github.io/freetier-atlas/`.

## Immediate build order

1. Create repository and commit this planning package.
2. Scaffold monorepo and CI.
3. Add configuration and schemas.
4. Add domain models.
5. Build Cloudflare vertical slice.
6. Build catalogue UX.
7. Build deterministic adviser.
8. Add ZIP generation.
9. Add remaining providers.
10. Add admin and notifications.
11. Deploy and verify public Z0 architecture.
12. Complete the acceptance checklist.
