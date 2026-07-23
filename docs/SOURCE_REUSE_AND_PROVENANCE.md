# Source Reuse and Provenance

## Rule

Community repositories may discover candidates and expose coverage gaps. Official providers establish facts.

## `ripienaar/free-for-dev`

Useful for broad service discovery and category ideas. It excludes self-hosted software and short trials.

No `LICENSE` file was found in checked paths. Do not copy, scrape, mirror, or bulk-import its prose or curated data. Use manually for discovery and independently verify all records.

## `iSoumyaDey/Awesome-Web-Hosting-2026`

Useful for candidate static hosts, full-stack platforms, serverless, databases, object storage, AI/GPU, VPS, and Z3 self-hosted PaaS options.

The README shows a CC0 badge, but the repository `LICENSE` contains MIT. Apply MIT requirements unless clarified. Use only as a candidate source; all claims require official verification.

## `costgoat/aws-free-tier`

Useful AWS regression checklist covering account models, always-free services, trials, organization behaviour, and billing traps.

No licence file was found. Link and use as a topic checklist only. Do not copy tables/descriptions. Verify every fact through AWS.

## `hashirahmad/Best-always-free-tier-cloud-platforms`

Useful evaluation dimensions: card requirement, vendor freedom, operational burden, subdomain usability, and upgrade path.

No licence file was found, and the content includes historical free-tier values. Use ideas only; do not ingest data.

## `255kb/stack-on-a-budget`

MIT-licensed. Strong reusable contribution structure:

- Free tier
- Pros
- Limitations
- What happens after quota
- Credit-card requirement

Adapt these fields with attribution. Candidate names/official URLs may be imported into a quarantined discovery table, never directly into verified catalogue state.

## Provenance fields

Every community-derived candidate records repository, URL, licence, discovery date, import method, and official-verification status.

No community prose is shown as public evidence.

## Enforcement (two layers)

The rule above is enforced as an invariant, not just a convention:

- **Application layer** — `app.ingest.trust` centralises the trust decision
  (`is_official_source`) and guards evidence creation
  (`assert_evidence_permitted`). `app.ingest.scan.run_scan` routes official
  sources to `Candidate(official=True)` + `Evidence` and community/unverified
  sources to quarantined `discovery_candidate` rows only — never `Evidence`,
  `Offer`, or `OfferVersion`.
- **Database layer** — migration `0006_quarantine_separation` installs two
  triggers: a `candidate` may be `official` only if its source's `trust_level`
  is `official` (so a community candidate can never be flagged or *promoted* to
  official, blocking in-place relabelling), and an `evidence` row may reference
  only an official candidate. Combined with the structural isolation of
  `discovery_candidate` (no FK into `evidence`/`offer_version`), community data
  cannot cross into the verified pipeline even via raw SQL.

There is no automated promotion-to-published path: any promotion requires
explicit official evidence and human/admin disposition. Unknown is better than
guessed.

## URLs

- https://github.com/ripienaar/free-for-dev
- https://github.com/iSoumyaDey/Awesome-Web-Hosting-2026
- https://github.com/costgoat/aws-free-tier
- https://github.com/hashirahmad/Best-always-free-tier-cloud-platforms
- https://github.com/255kb/stack-on-a-budget
