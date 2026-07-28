# Test Strategy

## Unit

Z classification, quota units, dates, eligibility, region/residency, confidence, reductions, architecture scoring, ZIP safety.

## Provider fixtures

Every adapter includes unchanged, changed, malformed, partial, and contradictory official-source fixtures with expected candidate facts and evidence locations.

### Case vocabulary

| Case | Kind | What it proves |
| --- | --- | --- |
| `unchanged` | extraction | The expected facts and evidence are extracted from a nominal document. |
| `changed` | extraction | A modified document yields modified facts (and so a different `content_hash`). |
| `partial` | extraction | Missing columns become UNKNOWN (`None`), never a fabricated value. |
| `malformed` | extraction | A broken document is rejected by `validate`, not half-parsed. |
| `contradictory` | extraction | Two official sources disagreeing raises a pending review item, auto-resolving nothing. |
| `withdrawn` | **pipeline** | An offer that vanishes between scans yields one `material` `withdrawn` change event. |
| `stale` | **pipeline** | Data older than the source's schedule window is flagged and never published. |

The first five are single-document extraction shapes, proved by loading one
captured document. `withdrawn` and `stale` (added in F008) only exist *across
time*, so they are driven end-to-end against real ORM rows by
`tests/support/fixtures.py` (`drive_withdrawn`, `drive_stale`) rather than by a
captured file.

Both pipeline helpers are **time-injected**: they pass an explicit `now` into
`reconcile_scan` / `publish_scan`, derived from the fixture's own snapshot and
the source's schedule window. Nothing sleeps and nothing reads the wall clock,
so the same inputs give the same rows on every run, on any machine, at any hour.

### Harness and offline guarantee

`tests/support/fixtures.py` loads `(provider, adapter, case)` from
`tests/fixtures/ingest/<provider>/<adapter>/<case>/`, drives the right adapter
through a `FixtureFetcher`, and asserts facts, evidence locations and a stable
`content_hash`. Adding a provider fixture therefore adds coverage without adding
test code.

CI performs **zero socket operations**. `LiveFetcher` may be constructed only by
`app/ingest/fetch.py` (which defines it), by `scripts/capture_fixture.py`
(owner-run, never invoked by tests or CI) and by `tests/unit/test_ingest_fetch.py`
(a loopback-only server started by the test itself). `tests/unit/test_no_live_fetcher_in_tests.py`
enforces that with an AST check.

Committed real-provider fixtures carry a `capture.json` provenance sidecar whose
presence, completeness and `sha256_stored` are asserted by
`tests/unit/test_capture_sidecar.py`. That test deliberately asserts **nothing
about freshness**: a "newer than N days" check in CI is a time bomb that reddens
the build on a calendar boundary rather than on a defect. Freshness is a runtime
concern, enforced by `assess_staleness` withholding publication.

## Integration

Fetch-to-candidate, candidate-to-verified, version history, conflict review, YAML reload, RSS, Discord, OAuth.

## Adviser evaluations

Static site, Python API + PostgreSQL, container, scheduler, object storage, high bandwidth, AI inference, impossible storage, commercial restriction, and regional requirement.

Verify no hidden Z1 component, sufficient quotas, current evidence, explainable scores, sensible reductions, and delayed self-host fallback.

## End-to-end

Clean Compose startup, catalogue journey, adviser-to-ZIP, admin conflict resolution, scheduled scan, public deployment health/headroom.

## Non-functional

Accessibility, performance, rate limits, SSRF, secrets, dependencies, containers, backup/restore, amd64/arm64.
