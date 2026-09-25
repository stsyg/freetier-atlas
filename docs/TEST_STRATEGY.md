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

The fetch guard's SSRF controls are tested adversarially rather than only
positively. `tests/unit/test_ingest_fetch.py` drives `LiveFetcher` through a
**hostile resolver** that answers the validation lookup and the connect-time
lookup differently — the DNS-rebinding shape — and asserts the connection is
refused and the stand-in internal service records nothing. Those tests open no
external connection: the hostile resolver steers every lookup after the first to
the loopback server the test started, so even the "public" address it advertises
is never contacted. A **positive control** runs the same sequence with no guard
in the way and asserts the bad outcome *is* observable, because a green test that
cannot detect the failure it guards against proves nothing. TLS is covered in
both directions: that a pinned connection still negotiates and verifies against
the URL's hostname, and that a certificate for a different name is still
rejected.

Those two TLS directions are proved by a **real handshake** against a throwaway
CA minted into a temporary directory, which is why `cryptography` is a declared
test dependency rather than an optional convenience — do not remove it to slim
the dev extra. It was previously undeclared, so both tests skipped in CI, and
the skip was rationalised on the grounds that the standard-library-only tests
above enforced the same property. That was measured and found false. Those tests
assert `server_hostname`, `check_hostname` and `CERT_REQUIRED` and infer the
rest from the stdlib contract, so a defect that weakens the context *injected*
into the handler, or that retries with verification off after an SSL error,
satisfies every one of them while defeating certificate verification entirely.
Both stayed green until the handshake tests were made to run. A third mutation —
verifying against the dialled IP literal instead of the hostname — is caught by
the standard-library-only test, so that test is not vacuous; it enforces part of
the property and infers the rest, and the handshake closes the inferred part.

Committed real-provider fixtures carry a `capture.json` provenance sidecar whose
presence, completeness and `sha256_stored` are asserted by
`tests/unit/test_capture_sidecar.py`. That test deliberately asserts **nothing
about freshness**: a "newer than N days" check in CI is a time bomb that reddens
the build on a calendar boundary rather than on a defect. Freshness is a runtime
concern, enforced by `assess_staleness` withholding publication.

### What a passing ingest fixture test attests — and what it cannot

Read this before you take a green ingest suite as evidence that the fixtures still
match the provider. **They do not prove that, and nothing in CI can.** Every ingest
fixture test resolves in one of two directions, and neither reaches a live page:

- **profile-to-capture.** `asserted_block_sha256` hashes the *profile's* pinned
  `assertion.text` and compares it to the list the capture recorded
  (`test_adapter_*.py::test_asserted_blocks_match_the_pinned_capture_hashes`). It
  proves the profile and the capture agree with each other.
- **capture-to-capture.** `structure.headers` / `structure.rows` are produced by
  parsing the *committed bytes* and comparing to the structure the same capture
  recorded. It proves the committed excerpt still parses to the structure it claims.

Both checks are real and worth having — they catch a profile that drifts from its
evidence and an excerpt that stops parsing. Neither is a fidelity check against the
provider. The live comparison happened **exactly once**, at capture-generation time,
when the owner-run reconciling generator refused to write unless each pinned block
resolved uniquely against the live parse (see `docs/PROVIDER_ADAPTERS.md`). That was
an **act, not an artefact**: nothing in the repository can re-perform it, and the
offline property is machine-enforced by `tests/unit/test_no_live_fetcher_in_tests.py`
(an AST guard pinning that CI opens no socket). This is permanent by design, not a
defect to fix by making CI fetch; the honest response is this disclosure.

The two digests in each `capture.json` are frequently misread, so state plainly what
they attest:

- **`sha256_stored` is a tamper-evidence seal, not a fidelity control — and it is
  circular.** It is `sha256(committed bytes)`, computed *after* those bytes were
  written and re-checked by recomputing the same hash
  (`test_capture_sidecar.py::test_sha256_stored_matches_the_committed_bytes`). It
  detects a later silent edit of a committed excerpt. It establishes **no** link to
  the live page. The loophole that matters: if this digest goes red, it can be
  "fixed" either by re-running the live reconciliation *or* by simply recomputing the
  stored hash — and **the committed artefact looks identical either way, so a reviewer
  cannot tell from the diff which happened.** The genuine control is therefore
  **author discipline** (re-run the live reconciliation *after* updating the hash, not
  *instead of* it), not machinery. That every real capture carries a
  `sha256_stored_note` saying as much is enforced corpus-wide by
  `test_capture_sidecar.py::test_a_stored_digest_is_always_disclosed`; the guard
  enforces that the disclosure is *present*, never that the underlying discipline was
  *exercised*.
- **`sha256_original` exists and is inert.** The provider pages serve per-build
  markup, so a later fetch of an unchanged page yields a different digest and it can
  never be re-checked; and it hashes the whole document, so any unrelated edit reds
  it. Every real capture discloses this in a `sha256_original_note`, enforced by
  `test_capture_sidecar.py::test_a_real_original_digest_is_always_disclosed`.

The useful empirical detail: when Prettier reformatted captures mid-slice,
`sha256_stored` **changed** while `structure.*` and `asserted_block_sha256` did
**not**, because those digest normalised text rather than raw bytes. That is the
shape a real fidelity signature would need — invariant under formatting, sensitive to
content. Building one (a fetch-time structural signature) is the candidate remedy and
is deliberately **out of scope**: its discriminator between a legitimate re-capture
and a ratified drift must be **provenance (was there actually a fetch?), not
content**, because the committed bytes are identical either way — which rules out any
content-hash-based remedy as self-defeating and needs a capture-format decision this
disclosure does not take.

## Integration

Fetch-to-candidate, candidate-to-verified, version history, conflict review, YAML reload, RSS, Discord, OAuth.

## Adviser evaluations

Static site, Python API + PostgreSQL, container, scheduler, object storage, high bandwidth, AI inference, impossible storage, commercial restriction, and regional requirement.

Verify no hidden Z1 component, sufficient quotas, current evidence, explainable scores, sensible reductions, and delayed self-host fallback.

## End-to-end

Clean Compose startup, catalogue journey, adviser-to-ZIP, admin conflict resolution, scheduled scan, public deployment health/headroom.

## Non-functional

Accessibility, performance, rate limits, SSRF, secrets, dependencies, containers, backup/restore, amd64/arm64.
