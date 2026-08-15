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

**Implemented (F008 slice P1).** `config/examples/providers/github.example.yaml` +
`apps/api/app/ingest/adapters/profiles/github.py` (declarative profiles only, no
provider logic in adapter code) + `tests/fixtures/ingest/github/html/`.

Five official `docs.github.com` sources, one captured excerpt each:

| source id                       | service                       | category                | verdict |
| ------------------------------- | ----------------------------- | ----------------------- | ------- |
| `github-actions-billing`        | GitHub Actions                | `cicd-source-control`   | Z0      |
| `github-packages-billing`       | GitHub Packages               | `object-file-storage`   | Z0      |
| `github-codespaces-billing`     | GitHub Codespaces             | `secrets-config-devtools` | Z0    |
| `github-pages-limits`           | GitHub Pages                  | `containers-app-hosting` | **not Z0** (unknown card status) |
| `github-enterprise-cloud-trial` | GitHub Enterprise Cloud trial | `cicd-source-control`   | **not Z0** |

Three things about this slice are worth copying, and one is worth avoiding.

**Perpetuity is checked, not assumed.** The last row is the deliberate non-Z0
case and the reason it exists: GitHub's own trial page says *"You do not need to
provide a payment method to start a trial."* and *"The trial lasts for 30 days
and includes the following features."* on the same page. No card is required and
it still must never be published as `$0` forever, so it is extracted with
`offer_type: trial` and classifies `Z2_TEMPORARY_OR_CONDITIONAL`. Every offer
marked `always_free` instead pins a verbatim perpetuity sentence as a required
`HtmlTextAssertion` (for example, Actions: *"The following amounts of time for
standard runners, artifact storage, and cache storage are included in your GitHub
plan. At the start of each month, the minutes used by the account are reset to
zero."*). A page that is merely silent about expiry is not evidence of
perpetuity.

**`hard_stop`, not `automatic_billing`.** The Actions, Packages and Codespaces
billing pages all state verbatim: *"If your account does not have a valid payment
method on file, usage is blocked once you use up your quota."* That sentence is
the whole basis for a Z0 verdict on those three services. Automatic billing
applies only to an account that has already added a card, which is not the `$0`
path being classified.

**The sentence itself is load-bearing.** Every material Z0 condition —
`service`, `offer_type`, `requires_card`, `has_paid_dependencies` and
`exhaustion_behaviour` — is a whole-block `HtmlTextAssertion` pinned to verbatim
source text, never a table cell. Rewording, truncating or deleting the sentence
above yields `assertion_not_found` and REJECTS the candidate, so a `$0` claim
cannot outlive the evidence for it. The profiles map no column onto those
fields, which is asserted in `tests/unit/test_adapter_github.py`.

**A fact with no source sentence is not published at all.** GitHub Pages is the
worked example. Measured on its live page, **none of its 65 text blocks** states
that no payment method is required — the page says only which plans include
Pages. An earlier revision pinned both `requires_card` and
`has_paid_dependencies` to that availability sentence, which never mentions
payment. Both facts are now **absent** rather than repinned, because there is
nothing honest to pin them to: they stay `UNKNOWN`, the classifier withholds Z0,
and the publication gate withholds the offer entirely. Pages still extracts
successfully and still publishes its perpetuity and its limits — only the
unsourced billing claims are gone. When a page does not state a material
condition, delete the assertion; do not hunt for a nearby sentence that can be
read as implying it.

**Two live pages carry no table at all.** Measured on 2026-08-13 and re-measured
on 2026-08-14: the GitHub Pages limits page and the Enterprise Cloud trial page
contain zero `<table>` elements — their allowances are published as `<li>`/`<p>`
prose. Both profiles are therefore **assertion-only** (`mode: "assertions"`):
they declare no table selector, read no table, and take 100% of their published
facts from pinned assertions. Each `capture.json` records the live re-verification.

An earlier revision of those two captures instead carried a fabricated one-cell
anchor table, constrained to map no column so it carried no claim. It was
committed only because extraction then required a table, and it was measurably
harmful: against the real pages both profiles returned `table_not_found`, so
they could not extract from the live document at all. The anchor tables have
been deleted and the engine now supports assertion-only profiles directly. **Do
not synthesize structure to satisfy the extractor** — GCP, Azure, Oracle and AWS
all state free-tier terms substantially in prose, and one fabricated table per
provider would institutionalise exactly the practice that made this provider
defective in the first place.

**Known limitation, disclosed in the captures.** The no-payment-method sentence
appears **twice** on the live Packages and Codespaces pages (once in the offer's
own section, once under budgets/spending). Whole-block equality requires exactly
one match, so against those unmodified live pages the engine would return
`ambiguous_assertion`. The committed excerpts retain the occurrence from the
offer's own section and declare the omission in
`capture.json → duplicate_live_blocks_not_retained`.

**Offer type fails closed at both gates.** `requires_card`,
`has_paid_dependencies` and `exhaustion_behaviour` each block Z0 when unknown.
An unrecognised `offer_type` also yields `UNKNOWN` in the classifier, while the
publication schema gate rejects a value outside the exact closed vocabulary
before resolving or inserting an offer. Profiles must still keep `offer_type`
required so malformed candidates fail during extraction rather than relying on
those later defenses.

**Constraint worth knowing:** `scripts/url-allowlist.txt` permits `github.com`,
`api.github.com` and `docs.github.com` only. The GitHub changelog lives on
`github.blog` and cannot be cited from this repo without an allowlist change, so
this slice deliberately has no changelog source rather than an unusable one.

Provider integration tests only roll back their own transactions; they do not
repair heap state for other modules. The physical-order regression in
`tests/integration/test_ingest_reconcile.py` owns its precondition: inside its
test transaction it creates a test-only descending candidate-ID index, runs
`CLUSTER`, and asserts that a sequential scan sees the intended adversarial
order. The transaction rollback removes the index and heap rewrite.

Heap order is not product semantics. Reconciliation remains deterministic
because its candidate loops explicitly order by `Candidate.id`; the regression
uses a controlled physical order only to prove that removing that product
ordering would be observable.

## AWS

Use:

- AWS Free Tier API (`GetFreeTierUsage`)
- Official AWS Free Tier pages and docs
- AWS MCP Server documentation search
- Service pricing pages
- Price List APIs only where appropriate

AWS states that bulk price lists are not a complete source for limited-period Free Tier offers.

Use `costgoat/aws-free-tier` for regression topics and gotcha test ideas only. Do not ingest or copy its tables because no licence file was found.

F008 P4 configures six official sources over six documents, all on
`aws.amazon.com`. The central hazard for this provider is that AWS markets
**three different free-offer kinds** under one brand — perpetual "Always Free"
offers, a time-limited introductory tier, and short-term trials and credits — and
only the first could ever be perpetual. Each source reads its own document and
pins its identity, its offer type and its term to blocks describing that offer,
so no profile can borrow another's facts.

| Profile | Mode | Live structure it reads |
| --- | --- | --- |
| `aws_free_tier_plan` | matrix | the 6-row `Benefits` / `Free plan` / `Paid plan` table |
| `aws_free_plan` | assertions | no table: the FAQ publishes its terms only as prose |
| `aws_12_month_free_tier` | assertions | no table: the Free Tier Terms are prose |
| `aws_dynamodb_free_tier` | assertions | 3 live tables, none header-selectable; allowance is a `<ul>` |
| `aws_api_gateway_free_tier` | assertions | no table: the free tier is prose |
| `aws_step_functions_free_tier` | assertions | 1 live table, a pricing example that publishes no allowance |

**Two AWS pages could not be used, and that is reported rather than worked
around.** MEASURED live (HTTP 200): `docs.aws.amazon.com` served 1166 bytes for
the Billing guide's free-tier page and 1083 bytes for the Lambda billing page.
Parsed with the repository's own `_DocumentCollector`, both yield zero tables,
zero headings and zero body blocks — their content is client-rendered. AWS
Lambda's pricing page was rejected for a different reason: its free-tier numbers
appear only inside six repeated worked pricing examples, so no block is unique
enough to pin without risking `ambiguous_assertion`.

**Only one AWS page in the sweep is matrix-extractable.** Across 13 probed
pricing pages, `_header_row` returned `expected one header row, found 0` for 4/4
Lambda tables, 3/3 DynamoDB tables and 8/8 S3 tables. Per-service free-tier
extraction on AWS is therefore assertion-based by necessity, not by preference.

**Nothing in this provider is Z0, and that is the finding rather than a gap.**
The FAQ page states, verbatim, "Yes, you are required to provide a valid payment
method to sign up for an AWS account, whether you choose a free plan or a paid
plan." That block names the free plan itself, so `requires_card=True` there is a
quotation rather than a composition, and the free-plan offer classifies Z1.
API Gateway states "If you exceed this number of calls per month, you will be
charged the API Gateway usage rates", which is `automatic_billing` and also Z1.
The plan-comparison, 12-month and DynamoDB offers classify UNKNOWN because their
own documents prove nothing about card requirements.

**The most important result is Step Functions.** Its page states, in a block of
its own, that its free tier "does not automatically expire at the end of your 12
month AWS Free Tier term, and is available to both existing and new AWS customers
indefinitely" — which satisfies rule 1 of `docs/DATA_MODEL.md`, so the offer is
genuinely `always_free`. The *same page* states "You are charged per state
transition above the free tier". A perpetual allowance whose overage is billed is
still `Z1_BILLING_EXPOSURE`. **Perpetual does not mean Z0**, and this provider
proves it from official text rather than from an author's summary.

`requires_card` is deliberately **absent** from the five non-FAQ profiles. The
payment-method block lives on the FAQ page; carrying it onto another document
would be cross-document composition, the same error the Google Cloud slice
avoided by recording `billing_account: required` instead of inventing a card
claim. No AWS profile claims a card is *not* required.

**Eleven of the fourteen categories are `unknown`, deliberately.** AWS publishes
its per-category free-offer list through a client-rendered widget: MEASURED on
`https://aws.amazon.com/free/`, the served HTML carries the headings "Free Tier
Categories", "Always free" and "Short-term trial" with no accompanying prose, and
the page's single served table compares account plans rather than services. Each
`unknown` records what was actually probed and what it did or did not say.
Declaring those categories `offered_no_z0` would assert offers this slice cannot
evidence; declaring them `not_offered` would be an unsupported claim in the other
direction. Both are refused.

**Four blocks are published twice by AWS** — the resource-reclaim, no-rollover,
offer-termination and region-aggregation clauses appear verbatim in both the
current and the Legacy sections of the Free Tier Terms. None is pinned, both
occurrences are retained in the capture so the fixture reproduces the live
ambiguity rather than hiding it, and
`tests/unit/test_adapter_aws.py::test_a_block_aws_publishes_twice_would_be_ambiguous_if_pinned`
proves that pinning one yields `ambiguous_assertion`.

## Google Cloud

Use managed Google/Google Cloud MCP servers, free-program docs, product pricing, release-note data/feeds, and public APIs.

F008 P3 configures four official sources over three documents. The central
hazard for this provider is that Google publishes **two different offers on one
page**: a perpetual Always Free tier and a 90-day, credit-backed Free Trial.
They are extracted as two separate profiles reading the page's own `#free-tier`
and `#free-trial` section anchors, so conflating them is structurally impossible
rather than merely discouraged. Each profile pins its identity, its offer type
and its exhaustion behaviour to prose inside its own section.

| Profile | Mode | Live structure it reads |
| --- | --- | --- |
| `gcp_free_tier_products` | matrix | the 29-row `Google Cloud product` / `Free Tier usage limits` table |
| `gcp_free_trial` | assertions | no table: the page publishes the trial's terms only as prose |
| `gcp_firestore_free_tier` | matrix | the 5-row `Free tier` / `Quota` table |
| `gcp_bigquery_free_tier` | matrix | the 2-row `Resource` / `Monthly free usage limits` / `Details` table |

**Nothing in this provider is Z0, and that is the finding rather than a gap.**
The free-program page states, verbatim, "Any usage that exceeds the Free Tier
usage limits is billed at standard rates." That is `automatic_billing`, which the
classifier treats as a definite billing exposure, so the Always Free tier
classifies Z1. The Free Trial page states, verbatim, "During the sign up, you
must provide a credit card or other payment method that is valid for the period
of the Free Trial", so `requires_card=True` is quoted rather than inferred and
the trial classifies Z1 as well. Firestore classifies UNKNOWN because its page
proves nothing about card requirements. BigQuery classifies Z1.

`requires_card` is deliberately **absent** from the three non-trial profiles.
The free-program page says a *billing account* is required ("To use products that
have a Free Tier, you need a Google Cloud billing account.") but no single block
says that account requires a payment method; the card sentence that exists is
scoped to Free Trial signup. Composing the two would be an inference rather than
a quotation, so the billing-account requirement is recorded as its own evidenced
fact and the card fact stays UNKNOWN. The Z0 verdict does not depend on it.

`gcp_free_tier_products` and `gcp_bigquery_free_tier` are `always_free` and
`recurring_quota` respectively, and the difference follows `docs/DATA_MODEL.md`
-> "Choosing between `always_free` and `recurring_quota`" applied per document.
The free-program page states "The Free Tier has no end date", which satisfies
rule 1. The two product pricing pages state no end date and no zero-priced tier
of their own; each identifies a replenishing free allowance on a service that is
itself metered, which is rule 2. Those three legs are pinned as separate facts on
the product profiles so a reviewer can check the determination against the rule
rather than against an author's intuition.

## Azure

Use Microsoft Learn MCP, Azure free/pricing pages, Azure updates, Azure Retail Prices API where useful, and Azure MCP for deployment/operational verification.

## Vercel

Use official Vercel MCP, plans/limits docs, changelog, and public APIs.

F008 P2 configures three canonical official pages: Hobby, Sandbox pricing, and
the Pro Trial. Each production profile selects a real table by normalized header
signature, pivots one published tier column, and maps exact title or prose blocks
from that same captured document. The captures retain every row and cell in each
target table. Their `capture.json` sidecars disclose target-table omissions
explicitly (none in the production captures) and pin the observed headers, rows,
cells, and asserted-block hashes.

No official Vercel page in this slice proves that a payment card is unnecessary
or that the offer has no paid dependency. The profiles therefore map neither
`requires_card` nor `has_paid_dependencies`. "Free", "no billing cycles", "will
not be charged", "you will not get charged", and instructions to add a payment
method only when upgrading are not treated as no-card evidence. All three
official candidates are routed to review as schema-incomplete; zero Vercel
offers, offer versions, or quotas are published. The Pro Trial is the deliberate
non-Z0 control: its page states a 14-day or usage-limit end condition and the
profile records `offer_type=trial`, but unknown material gates still prevent
publication.

### Generic same-document HTML matrices

`HtmlExtractionProfile` supports a provider-agnostic matrix mode for official
HTML pages whose stable structure is the visible table headers rather than an
`id` or durable class. A profile declares an order-insensitive normalized
`header_signature`; extraction requires exactly one matching table. Zero
matches return `table_not_found` with the required and observed headers, while
multiple matches return `ambiguous_table`. Legacy `table_id` / `table_class`
row profiles retain their original first-match behavior.

Matrix mode resolves one exact metric-label header and one exact tier header,
then maps exact normalized row labels into one candidate. Required rows,
columns, and row widths are fail-closed. Duplicate labels, duplicate tier
columns, conflicting values, and undeclared rows reject the candidate.
Non-material rows may be ignored only when the profile names them explicitly.
Selected cell text is preserved after the adapter's existing whitespace/entity
normalization; qualifiers such as `First`, `Up to`, units, and reset periods are
not parsed or discarded.

Trusted static profiles may also declare exact same-document text assertions.
The supported scopes are the complete normalized `<title>`, complete normalized
heading (`h1`-`h6`), and complete normalized body block (`p`/`li`). Equality is
whole-block and case-sensitive after whitespace normalization: substring,
near-match, and fuzzy inference are not accepted, and runtime/config users
cannot supply regex. A required missing or duplicate match rejects the
candidate; an optional missing match emits no field. Canonical mappings such as
`offer_type`, eligibility, boolean gates, or exhaustion behavior therefore
exist only behind reviewable source wording in the same captured document.
Profile construction also validates mapped offer types, exhaustion behaviours,
and boolean gates against their closed field vocabularies. Free-text values that
reach the UI, such as `notes`, must reproduce the asserted source wording
verbatim rather than paraphrase it.

Each matrix cell and assertion adds a field-specific `EvidenceLocation`
selector (for example, `matrix row[...] column[...] -> fact[...]`), so the
existing evidence schema persists per-fact provenance without a migration.
There is deliberately no source-set or cross-document composition API.

### Assertion-only profiles (pages with no table)

A table is **not** required. An official page that states its free-tier terms
entirely in prose is extracted with `mode: "assertions"`: the profile declares
no `table_id`, `table_class`, `header_signature`, `columns`, or matrix fields at
all, and every fact comes from a pinned `HtmlTextAssertion`. Declaring any table
field alongside `mode: "assertions"` is a construction-time `ValueError`, so an
assertion-only profile cannot quietly become table-backed.

Reach for this whenever the live page has no table. **Never commit a synthetic
anchor table to give the extractor something to select.** A fabricated table
does not exist on the live page, so the profile fails against the real document
even while the fixture passes — the fixture stops representing its own source,
which is the same class of defect as a synthesized header.

**The evidence floor is explicit, because the matrix is optional.** Before this
mode existed, the mandatory matrix doubled as an *accidental* evidence floor: a
profile that proved nothing could not emit a candidate, because it could not
select a table. Relaxing the matrix requirement dissolves that accident, so
`HtmlExtractionProfile` now states the floor directly, keyed by mode:

| mode         | accepted sources of facts |
| ------------ | ------------------------- |
| `rows`       | `columns` or `assertions` |
| `matrix`     | `matrix_rows`             |
| `assertions` | `assertions`              |

A profile satisfying none of its mode's sources raises at construction rather
than emitting a candidate backed by nothing — in this product an unsourced
candidate is a potential unsupported claim that a service is free, which is
worse than refusing to extract. The floor is deliberately **per mode**, not a
permissive "any of these fields is set": `matrix_rows` are inert in `rows` mode
and must not be mistaken for evidence there, and an unlisted mode raises rather
than defaulting to permitted. At runtime the floor holds too — an assertion-only
extraction that matches nothing (every assertion optional, every one absent)
returns `no_assertion_evidence` instead of an empty candidate. See
`tests/unit/test_adapter_html_assertions.py`.

The complete Vercel fixture vocabulary covers unchanged, changed, partial,
malformed, and structurally ambiguous documents offline. Withdrawn and stale
remain cross-scan behaviours and use the shared real-PostgreSQL harness. A
partial table, duplicate matching table, missing assertion, undeclared row, or
irregular row width fails closed rather than producing a friendlier subset.

The three declared service-category mappings are intentionally explicit:
Vercel Hobby and the Pro Trial map to application hosting, while Vercel Sandbox
maps to compute VMs. Each is arguable because the plans span multiple products;
the YAML records the rationale instead of inferring category from quota units.
All fourteen provider-category states remain explicitly declared and none is
`verified_free`.

## Oracle Cloud

Use Oracle free-tier and service docs, changelogs/release notes, APIs, and database-specific MCP only where relevant.

F008 P6 configures six official sources over six documents, split evenly between
`www.oracle.com` and `docs.oracle.com`. Oracle is the last of the six providers
and the riskiest, because it markets **"Always Free"** more prominently than any
other provider here — and, unlike the marketing-only cases, its Always Free tier
really is perpetual.

| Profile | Mode | Live structure it reads |
| --- | --- | --- |
| `oracle_always_free_resources` | matrix | the 6-row `Resource` / `Limit Name` / `Always Free` table |
| `oracle_free_tier` | assertions | no table: the OCI Free Tier doc is prose |
| `oracle_always_free_services` | assertions | no table: the Free Tier FAQ is prose |
| `oracle_cloud_free_tier` | assertions | no table: the Free Tier hub page is prose |
| `oracle_free_credit_promotion` | assertions | no table: the promotion-expiry doc is prose |
| `oracle_mysql_heatwave_always_free` | assertions | no table: the MySQL free page is prose |

**Oracle's Always Free tier IS perpetual, and this slice says so.** A
wrongly-omitted free offer is as much a defect as a wrongly-published one, so the
favourable finding is extracted rather than hedged away. Two independent
documents state it in blocks describing Always Free alone: the OCI docs say the
resources are "free of charge in the home region of the tenancy, for the life of
the account", and the FAQ says "Always Free services are available for an
unlimited time." Both satisfy rule 1 of `docs/DATA_MODEL.md` by quotation, so
`always_free` is not inferred from the word "free".

**And it is still not Z0.** Oracle states a payment-card requirement in blocks of
its own on four independent documents. From `docs.oracle.com` `.../FreeTier/freetier.htm`:
"For security purposes, most users need a mobile phone number and a credit card
to create an account. Your credit card will not be charged unless you upgrade
your account." From the FAQ, the hub page and the MySQL page: "We use your
contact information and credit/debit card information for account setup and
identity verification." Three providers have now been measured and all three
agree: **perpetual is not free.**

**The reading is stated so a reviewer can check it rather than trust it.**
Neither block contains the word "required", and the first hedges with "most
users". They are read as `requires_card=True` because each states that supplying
a card is part of creating a *free* Oracle Cloud account, and the second goes on
to say Oracle periodically checks the validity of "your card". No Oracle document
probed states that a card is *not* required. Leaving the field absent would
withhold Z0 too, via gate 4 rather than gate 3, so the choice changes no Z0
verdict — only whether the refusal is reported as a definite billing exposure or
as an unknown.

**Blocking conditions are reported by SHAPE, because the two shapes are not
equally strong.** Four offers are `Z1_BILLING_EXPOSURE` on a **quoted** card
sentence found on that offer's own document. Two are `UNKNOWN` on the **absence**
of any payment statement: the OCI Always Free Resources document and the
promotion-expiry document say nothing about payment, and importing the
requirement from another Oracle page would be cross-document composition. An
absence-based refusal can be flipped by anything that later supplies the field;
a quotation cannot. `tests/unit/test_adapter_oracle.py` asserts which sources
fall into which group so the distinction cannot quietly drift.

**A trap that was measured and avoided.** The block "You will only be charged for
services that you use that exceeds Always Free." reads like an automatic-billing
statement for Always Free. Its FAQ question — held in a `<div class="cb105w3">`
the parser does not capture — is "How do I know how much I am going be charged
for Pay As You Go services?". Its own context is a PAID account, so it is pinned
as an exhaustion behaviour nowhere in this slice. The nearest equivalent on the
hub page is carried whole as a note so the boundary stays visible.

**Publication and Z0 are different gates and are not conflated.** MEASURED
against a real PostgreSQL: all six Oracle candidates are held for review as
"uncertain evidence", and the reason is that their facts are pinned to prose
rather than to numeric quota rows, so they fail the gate's `schema_complete` and
`deterministic` hard conditions. It is *not* the card that stops publication — a
Z1 offer is a legitimate catalogue entry, it is simply not free.
`test_the_catalogue_never_labels_a_card_required_offer_z0` seeds two otherwise
identical candidates that differ only in `requires_card`: both publish, the
cleared one is labelled `Z0_TRUE_FREE` and the Oracle-shaped one
`Z1_BILLING_EXPOSURE`. The Z0 label is therefore reachable through that exact
path, which is what makes "no Oracle offer is Z0" a measurement.

**Only one probed Oracle page is matrix-extractable.** MEASURED with the
repository's own parser: the OCI Always Free Resources document carries one
`<table>` with the live header row `Resource` / `Limit Name` / `Always Free` and
6 body rows of Resource Manager limits. `www.oracle.com/cloud/price-list/` served
120 tables of which exactly one is header-selectable, and that one is a PAID
comparison table; `www.oracle.com/cloud/compute/pricing/` and
`.../networking/load-balancing/pricing/` both redirect to it, so they are one
document and not three. `www.oracle.com/database/nosql/pricing/` served 23
tables, one header-selectable and also paid, with no free-tier prose in its
served HTML at all. `.../storage/object-storage/pricing/` redirects to a
client-rendered cost estimator carrying no allowance. Two candidate URLs returned
HTTP 404. None is used and none was worked around.

**Nine of the fourteen categories are `offered_no_z0`** — far more than any other
provider here — because Oracle publishes a comprehensive Always Free enumeration
on two independent documents. The remaining five are `unknown` and **zero are
`not_offered`**: the enumeration omits them, which is suggestive but not proof,
and Oracle's own FAQ says "As new Always Free services become available, you will
automatically be able to use those as well". For `containers-app-hosting` the
rationale names the strongest counter-example against itself — "APEX Application
Development" and "Content Management Starter Edition" are in the list and could
arguably be read as application hosting — which is precisely why it is `unknown`
rather than `not_offered`.

**One block Oracle publishes twice is retained, not hidden.** "50,000 Object
Storage API requests per month" appears in both the Always-Free-only list and the
paid/trial list on the OCI docs page. It is not pinned, both occurrences are
retained in the capture so the fixture reproduces the live ambiguity, and
`test_a_block_oracle_publishes_twice_would_be_ambiguous_if_pinned` proves that
pinning it yields `ambiguous_assertion`.

**Every quotation was generated, not transcribed.** Each pinned block was
resolved against the live document's own parse by a short unique needle, and the
committed fixture was written from the resolved literal by a generator that
refuses to write when a needle matches zero or more than one live block, when a
resolved block occurs more than once live, or when the parsed target-table rows
differ from live. Transcription is where a *composed* quotation creeps in, and
generating from the resolved literal removes the opportunity rather than guarding
against it.

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
inspection — a point-in-time observation rather than a standing property, and nothing in this
repository detects it becoming false. An automated check was attempted and removed (see AMENDMENT 8
in `agent-state/current_contract.json`); the claim is deliberately an inspection result rather than
an enforced invariant. See
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
