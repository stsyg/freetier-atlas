# Ingest fixtures

Every fixture in this tree is served **offline** by `FixtureFetcher`
(`app.ingest.runner.build_fixture_fetcher`). The test suite and CI perform **zero
socket operations**; `LiveFetcher` is constructed only by
`scripts/capture_fixture.py`, which is owner-run and never invoked by tests or CI.

## Layout

```
tests/fixtures/ingest/<provider>/<adapter>/<case>/
    source.html | source.json | source.xml   # the document the adapter parses
    expected.json                            # the asserted extraction result
    capture.json                             # provenance sidecar (real providers only)
```

`--fixtures-root` points at `tests/fixtures/ingest/<provider>/<adapter>`; the
runner resolves `<source id>/source.<ext>` first and then a flat
`<source id>.<ext>`. The extension determines the MIME the fixture is served
with (`html` -> `text/html`, `json` -> `application/json`, `xml` ->
`application/rss+xml` for an `rss` source and `application/xml` otherwise). A
source with no resolvable fixture is simply **not registered** -- the scan fails
with a not-found, it never falls back to the network and it never guesses a MIME.

`<case>` uses the vocabulary in `docs/TEST_STRATEGY.md`:
`unchanged | changed | partial | malformed | contradictory | withdrawn | stale`.
`withdrawn` and `stale` are pipeline-level cases driven by
`tests/support/fixtures.py` (`drive_withdrawn`, `drive_stale`) rather than by a
single captured document.

## Copyright posture (decision Q2-A)

Commit **minimal official excerpts only** -- the specific table or section the
extraction profile reads. Do **not** mirror whole pages or bulk provider
content. Attribute every real-provider fixture in the table below, and record
how the excerpt was produced in `capture.json`'s `trim_method`.

The `example/` corpus is synthetic: it is written by hand to exercise adapter
shapes and represents no real provider, so it carries no `capture.json` (there
is no provenance to record) and no attribution.

## `capture.json`

`scripts/capture_fixture.py` writes this sidecar next to `source.<ext>`. All
nine keys are required for every real-provider fixture; a value that is
genuinely unknown is `null` rather than guessed.

| Key | Meaning |
| --- | --- |
| `url` | The official URL that was fetched (final, post-redirect). |
| `fetched_at` | ISO-8601 UTC timestamp of the capture, or `null` if unrecorded. |
| `http_status` | HTTP status of the final response, or `null` if unrecorded. |
| `sha256_original` | SHA-256 of the bytes as fetched, before trimming, or `null`. |
| `sha256_stored` | SHA-256 of the bytes actually committed as `source.<ext>`. |
| `trim_method` | How the committed excerpt was produced (`none` for a whole document). |
| `robots_allowed` | The operator's robots.txt outcome: `true`, `false` or `null`. |
| `tos_note` | Free-text note recording the terms-of-service check. |
| `captured_by` | Who ran the capture. |

`tests/unit/test_capture_sidecar.py` asserts the sidecar is **present** for every
real-provider fixture, that every key exists, and that `sha256_stored` matches
the bytes on disk. It deliberately asserts **nothing about freshness**: a
"fixture must be newer than N days" check in CI is a time bomb that reddens the
build on a calendar boundary instead of on a real defect. Freshness is a
**runtime** concern, enforced by `assess_staleness` withholding publication for a
stale source.

If you re-trim a committed fixture, update `sha256_stored` (and `trim_method`)
in the same commit -- the integrity test prints the correct digest when it fails.

## Capture workflow

```
python scripts/capture_fixture.py config/examples/providers/<provider>.example.yaml \
    --source <source id> \
    --out tests/fixtures/ingest/<provider>/<adapter>/<case> \
    --robots-allowed yes --tos-note "checked <date>" \
    --yes-i-am-the-owner
```

Then: trim to the minimal excerpt, update `trim_method` + `sha256_stored`, add
the attribution row below, and write `expected.json`.

## Attribution

| Fixture | Source | Owner |
| --- | --- | --- |
| `cloudflare/html/cloudflare-workers-limits/` | <https://developers.cloudflare.com/workers/platform/limits/> | Cloudflare, Inc. |
| `cloudflare/html/cloudflare-pages-limits/` | <https://developers.cloudflare.com/pages/platform/limits/> | Cloudflare, Inc. |
| `vercel/html/vercel-sandbox-pricing/` | <https://vercel.com/docs/sandbox/pricing> | Vercel Inc. |
