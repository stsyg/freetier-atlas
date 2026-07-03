# Provider Adapter Strategy

## Cloudflare

First vertical slice. Use official Cloudflare MCP servers, documentation, changelogs/RSS, pricing and limits pages, and public APIs.

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
