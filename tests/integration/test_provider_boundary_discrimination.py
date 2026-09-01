"""Give two unobservable formulations a corpus that can tell them apart.

PR #103 built ``provider_boundary`` in
``tests/integration/test_catalogue_currency.py`` and ran mutations against it.
Two SURVIVED, and its author reported plainly that both survived for the same
reason: **the corpus could not discriminate them.**

* **M4** -- ``_published_claim_expiries`` selecting the OLDEST evidence row for a
  version rather than any other. Every published version in the ``_publish``
  corpus carries exactly ONE evidence row, so the discriminating population is
  EMPTY and ``min`` and ``max`` return the same row.
* **M2** -- the ``spread < 1s`` precondition inside the boundary derivation. The
  only corpus available stamps its claims a fraction of a second apart under a
  single window, so no input exists that the guard would refuse.

That is UNTESTED-BECAUSE-UNDISCRIMINATED, not untested-because-unchecked: the
formulation chosen is the more correct of the two, there was simply no data that
could tell it from the less correct one. It goes live UNGUARDED the moment any
provider publishes a version with two evidence rows, or two sources declare
different windows -- and because the fixture would keep passing whichever way it
was written, a regression there would be SILENT.

Why the published corpus cannot discriminate them -- measured, not assumed
--------------------------------------------------------------------------
Measured against ``config/examples/providers/cloudflare.example.yaml``, the only
provider config in the tree, at ``5aa7678``:

===========================================================  ==============
Population                                                   Count
===========================================================  ==============
Offers reachable from provider ``cloudflare`` after publish   2
...published, ``Z0_TRUE_FREE`` and evidence-backed            2
Evidence rows per published version                           **1 each**
DISTINCT declared schedules across their sources              **1**
...and that one schedule is ``official_pages``, which
``parse_schedule_window`` cannot parse, so every window in
the corpus is the 7-day FALLBACK                              1 window
Expiry spread across the two claims                           ~0.3 s
===========================================================  ==============

So the emptiness is doubly deep: there is neither a version with two evidence
rows NOR a source with a parseable schedule. ``min(fetched_at + window)`` and
``min(fetched_at) + window`` are identical *by construction* on that data.

What this module adds
---------------------
Synthetic, persisted corpora (``tests/support/boundary_corpus.py``) carrying the
structure the published one lacks: a version with TWO evidence rows fetched at
different moments, and sources declaring DIFFERENT parseable windows. Synthetic
data is the right instrument here because the question is the REACHABILITY of a
branch, not present-day incidence -- unlike a currency measurement, where seeding
a corpus would only measure this suite's own ingest clock.

Both killing assertions are checked against PRODUCTION behaviour rather than
against a restatement of the helper's own arithmetic:

* the M4 arm requires the derived expiry to be the exact moment
  ``queries.fetch_evidence_currency`` flips the version, which is decided by
  ``worst()`` in application code;
* the M2 arm requires the derivation to ACCEPT a corpus only when that corpus's
  one-second arm is measurably unambiguous in the database.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta

import pytest
from alembic import command
from app.ingest.reconcile import parse_schedule_window
from app.read_api import queries
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

# ``_alembic_config``/``_publish``/``_published_claim_expiries``/
# ``_derive_provider_boundary`` are private to their module, and reaching across
# for them is deliberate: a killing assertion that lives OUTSIDE the file it
# guards proves the corpus really reaches that file's code rather than a local
# copy of it. ``tests.unit.test_adviser_router._pool`` sets the precedent for a
# cross-module private import in this suite.
from tests.integration.test_catalogue_currency import (
    _alembic_config,
    _derive_provider_boundary,
    _publish,
    _published_claim_expiries,
)
from tests.support.boundary_corpus import (
    ALIGNED_CLAIMS,
    INVERTED_CLAIMS,
    PERMUTABLE_CLAIMS,
    Corpus,
    build_corpus,
)

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start Postgres (scripts/stack-up) and export it to enable.",
)

ONE_SECOND = timedelta(seconds=1)

Claims = Sequence[tuple[int, datetime, timedelta]]


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    from sqlalchemy import create_engine

    command.upgrade(_alembic_config(), "head")
    eng = create_engine(DATABASE_URL)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    conn = engine.connect()
    trans = conn.begin()
    sess = Session(bind=conn)
    try:
        yield sess
    finally:
        sess.close()
        trans.rollback()
        conn.close()


@pytest.fixture
def aligned(session: Session) -> Corpus:
    """Two claims; one carries two evidence rows. Oldest-fetched == earliest-expiring.

    Built so the SECOND precondition of the derivation is satisfied and only the
    expiry spread is out of range -- otherwise a refusal could not be attributed
    to the spread guard alone.
    """

    return build_corpus(session, suffix="aligned", claims=ALIGNED_CLAIMS)


@pytest.fixture
def inverted(session: Session) -> Corpus:
    """One claim whose oldest-FETCHED evidence row carries the LONGER window."""

    return build_corpus(session, suffix="inverted", claims=INVERTED_CLAIMS)


@pytest.fixture
def permutable(session: Session) -> Corpus:
    """FOUR claims the derivation accepts, so every ordering can be compared."""

    return build_corpus(session, suffix="permutable", claims=PERMUTABLE_CLAIMS)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _expiries(claims: Claims) -> list[datetime]:
    return [fetched + window for _, fetched, window in claims]


def _derivation_refusal(claims: Claims, slug: str) -> str | None:
    """``None`` when the derivation ACCEPTS the corpus, else its refusal message."""

    try:
        _derive_provider_boundary(claims, slug)
    except AssertionError as exc:
        return str(exc)
    return None


def _stale_flags_at(session: Session, claims: Claims, moment: datetime) -> list[bool]:
    """Production's own verdict for each claim at ``moment`` -- not a restatement."""

    verdicts = queries.fetch_evidence_currency(session, now=moment)
    return [verdicts[("offer_version", version_id)].stale for version_id, _, _ in claims]


def _one_second_arm_is_ambiguous(session: Session, claims: Claims) -> bool:
    """Is ``min(expiry) + 1s`` a moment at which the claims DISAGREE?

    This is the property the ``spread < 1s`` precondition exists to protect, and
    it is measured from the database rather than recomputed from the same
    arithmetic the guard uses.
    """

    flags = _stale_flags_at(session, claims, min(_expiries(claims)) + ONE_SECOND)
    return not all(flags)


# --------------------------------------------------------------------------- #
# The instrument's own floor: the corpus must really discriminate              #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_the_synthetic_corpus_carries_the_structure_the_published_one_lacks(
    session: Session, aligned: Corpus
) -> None:
    """Asserted, not announced -- a vacuous corpus kills nothing and looks identical.

    Read DIRECTLY from the ORM rows, never through ``_published_claim_expiries``.
    An earlier draft of this test enumerated windows through that helper, which
    made the floor move with the very mutation it is supposed to be a fixed floor
    for: mutating the helper made this test fail for a reason that was about the
    helper, not about the corpus. A floor must not depend on the thing it floors.

    A corpus later "simplified" back to one evidence row per version fails HERE,
    with a named reason, instead of silently turning the mutation kills into
    survivals again.
    """

    provider = queries.fetch_provider(session, aligned.provider_slug)
    assert provider is not None, "the synthetic provider must be visible to the read layer"

    versions = [queries.latest_version(offer) for svc in provider.services for offer in svc.offers]
    assert all(v is not None for v in versions)
    evidence_counts = sorted(len(v.evidence) for v in versions if v is not None)
    assert evidence_counts == [1, 2], (
        "the corpus must hold a version with TWO evidence rows -- that is the "
        f"population M4 needs and the published corpus lacks (got {evidence_counts})"
    )

    # Straight off the rows: every (fetched_at, schedule) the corpus contains.
    rows = [
        (e.snapshot.fetched_at, e.source.schedule)
        for v in versions
        if v is not None
        for e in v.evidence
        if e.snapshot is not None and e.snapshot.fetched_at is not None
    ]
    assert len(rows) == 3, f"expected three evidence rows across two versions, got {len(rows)}"

    # The two rows on the multi-evidence version must be fetched at DIFFERENT
    # moments, or oldest-vs-newest is still unobservable even with two of them.
    multi = aligned.evidence_by_name["two_rows"]
    assert len({fetched for fetched, _ in multi}) == 2, (
        "both evidence rows share a fetch time, so selecting the oldest is still "
        "indistinguishable from selecting the newest"
    )

    # And the sources must declare DIFFERENT windows that actually PARSE. In the
    # published corpus every schedule_ref is unparseable and every window is the
    # same 7-day fallback, which is the deeper reason these branches were unreachable.
    windows = {parse_schedule_window(schedule) for _, schedule in rows}
    assert windows == {timedelta(days=1), timedelta(days=7)}, (
        f"expected genuinely parsed daily and weekly windows, got {windows}; with "
        "a single window min(fetched_at + window) cannot be told from "
        "min(fetched_at) + window"
    )


# --------------------------------------------------------------------------- #
# M4 -- the OLDEST evidence row, cross-checked against production currency     #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_each_claims_expiry_is_the_moment_production_currency_actually_flips(
    session: Session, aligned: Corpus
) -> None:
    """The helper's expiry must agree with ``worst()``, not merely with itself.

    ``fetch_evidence_currency`` gives a version its LEAST current verdict, so a
    version resting on several sources goes stale when its STALEST support does.
    ``_published_claim_expiries`` therefore has to select the oldest evidence row
    and pair that row's own window. Asserting that by restating ``min(...)``
    would prove nothing; this instead pins the derived expiry to the exact
    instant at which application code flips the verdict, one second either side.

    This is the assertion that had no discriminating population before: with one
    evidence row per version, every selection rule agrees.
    """

    claims = _published_claim_expiries(session, aligned.provider_slug)
    assert len(claims) == 2, f"expected two published claims, got {len(claims)}"

    for version_id, fetched, window in claims:
        expiry = fetched + window
        at = queries.fetch_evidence_currency(session, now=expiry)[("offer_version", version_id)]
        after = queries.fetch_evidence_currency(session, now=expiry + ONE_SECOND)[
            ("offer_version", version_id)
        ]
        assert at.current is True, (
            f"version {version_id} is already stale at the expiry the helper "
            f"derived ({expiry.isoformat()}), so the helper is reading the wrong "
            "evidence row: production flips earlier than this"
        )
        assert at.stale is False
        assert after.stale is True, (
            f"version {version_id} is still current one second past the derived "
            f"expiry ({expiry.isoformat()}), so the helper is reading the wrong "
            "evidence row: production flips later than this"
        )


@skip_without_db
def test_the_two_evidence_rows_would_yield_different_expiries(
    session: Session, aligned: Corpus
) -> None:
    """The impossible-value floor for the test above.

    If oldest and newest happened to produce the same expiry, the cross-check
    would pass under either selection rule and prove nothing. State the gap.
    """

    rows = aligned.evidence_by_name["two_rows"]

    oldest = min(rows, key=lambda row: row[0])
    newest = max(rows, key=lambda row: row[0])
    oldest_expiry = oldest[0] + parse_schedule_window(oldest[1])
    newest_expiry = newest[0] + parse_schedule_window(newest[1])

    assert oldest_expiry != newest_expiry, (
        "selecting the oldest and the newest evidence row give the same expiry, "
        "so this corpus cannot discriminate the two selection rules after all"
    )
    # And the difference must be far larger than any clock resolution question.
    assert abs(newest_expiry - oldest_expiry) > timedelta(days=1)


# --------------------------------------------------------------------------- #
# M2 -- the spread precondition, stated as its PURPOSE rather than its text    #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_a_wide_expiry_spread_really_does_leave_the_one_second_arm_ambiguous(
    session: Session, aligned: Corpus
) -> None:
    """The hazard the precondition guards, demonstrated as a live measurement.

    Without this, a test that merely asserts "the guard raises" would be the
    tautology that an assert asserts. Here the database itself is asked whether
    the one-second arm resolves, and on this corpus it does NOT: the
    earliest-expiring claim is past its window while a sibling claim is still
    comfortably current.
    """

    claims = _published_claim_expiries(session, aligned.provider_slug)
    expiries = _expiries(claims)
    spread = max(expiries) - min(expiries)
    assert spread >= ONE_SECOND, (
        f"this corpus was meant to spread wider than a second (got {spread})"
    )

    flags = _stale_flags_at(session, claims, min(expiries) + ONE_SECOND)
    assert any(flags), "no claim is stale one second past the earliest expiry"
    assert not all(flags), (
        "every claim is stale one second past the earliest expiry, so the "
        "one-second arm is unambiguous here and this corpus does not exercise "
        "the hazard the spread precondition exists to catch"
    )


@skip_without_db
def test_the_derivation_accepts_a_corpus_only_when_its_one_second_arm_resolves(
    session: Session, aligned: Corpus
) -> None:
    """The precondition's PURPOSE, checked over an accepting and a refusing corpus.

    The guard's contract is not "there is an assert statement" but *the boundary
    this function hands back is one every claim agrees about*. So: for each
    corpus, ask the database whether the one-second arm is ambiguous, then ask
    the derivation whether it accepts. Acceptance must imply resolution.

    Deleting the precondition breaks exactly this: the wide corpus is accepted
    while remaining measurably ambiguous.
    """

    _publish(session)
    published = _published_claim_expiries(session, "cloudflare")
    synthetic = _published_claim_expiries(session, aligned.provider_slug)
    assert published and synthetic

    for slug, claims in (("cloudflare", published), (aligned.provider_slug, synthetic)):
        ambiguous = _one_second_arm_is_ambiguous(session, claims)
        refusal = _derivation_refusal(claims, slug)
        accepted = refusal is None
        assert accepted is not ambiguous, (
            f"{slug}: the derivation "
            f"{'ACCEPTED' if accepted else 'refused'} a corpus whose one-second "
            f"arm is {'ambiguous' if ambiguous else 'unambiguous'} -- acceptance "
            "must imply that every claim agrees at stale_at"
        )


@skip_without_db
def test_the_wide_corpus_is_refused_by_the_spread_guard_and_not_the_other_one(
    session: Session, aligned: Corpus
) -> None:
    """Attribution: a kill by a different guard is not evidence for this one.

    The derivation carries two preconditions. This corpus is built so the second
    (oldest-fetched == earliest-expiring) HOLDS, which is verified here from the
    claims directly rather than inferred from which message came back, so the
    refusal can only be the spread guard's.
    """

    claims = _published_claim_expiries(session, aligned.provider_slug)

    # Structural check of the OTHER precondition, computed independently here.
    earliest_fetched = min(claims, key=lambda claim: claim[1])
    earliest_expiry = min(claims, key=lambda claim: claim[1] + claim[2])
    assert earliest_fetched[0] == earliest_expiry[0], (
        "this corpus violates the alignment precondition too, so a refusal could "
        "not be attributed to the spread guard alone"
    )

    refusal = _derivation_refusal(claims, aligned.provider_slug)
    assert refusal is not None, (
        "the derivation accepted a corpus whose claims expire "
        f"{max(_expiries(claims)) - min(_expiries(claims))} apart"
    )
    assert "expire within one second of each other" in refusal, (
        f"refused, but by the wrong guard: {refusal!r}"
    )
    assert "earliest-expiring one" not in refusal


# --------------------------------------------------------------------------- #
# What a discriminating corpus reveals about the WITHIN-version rule           #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_the_within_version_rule_survives_an_inverted_window_ordering(
    session: Session, inverted: Corpus
) -> None:
    """The same cross-check, with the longer window on the OLDER evidence row.

    This orientation FAILED when it was first run, and that failure was the point
    of building the corpus. ``_published_claim_expiries`` used to select ``min``
    by ``fetched_at`` and pair THAT row's window; when the older row carries the
    longer window it is no longer the earliest-EXPIRING row, so the derived
    expiry ran roughly six days past the moment ``worst()`` actually flipped the
    version.

    "Earliest-expiring, not oldest-fetched" had been applied ACROSS a provider's
    claims but left as ``min(fetched_at)`` WITHIN each claim -- a half-applied
    principle, invisible while every version has one evidence row and every
    source falls back to one window. :func:`_earliest_expiring_evidence` now
    applies it at both levels, and this pins that.
    """

    claims = _published_claim_expiries(session, inverted.provider_slug)
    assert len(claims) == 1

    # The orientation must really be inverted, or this proves nothing: the
    # oldest-FETCHED row must NOT be the earliest-EXPIRING one.
    rows = inverted.evidence_by_name["inverted"]
    oldest_fetched = min(rows, key=lambda row: row[0])
    earliest_expiring = min(rows, key=lambda row: row[0] + parse_schedule_window(row[1]))
    assert oldest_fetched != earliest_expiring, (
        "this corpus is not actually inverted -- the oldest-fetched row is also "
        "the earliest-expiring one, so it cannot distinguish the two rules"
    )

    version_id, fetched, window = claims[0]
    expiry = fetched + window
    at = queries.fetch_evidence_currency(session, now=expiry)[("offer_version", version_id)]
    after = queries.fetch_evidence_currency(session, now=expiry + ONE_SECOND)[
        ("offer_version", version_id)
    ]
    assert at.current is True, (
        f"version {version_id} is already stale at the derived expiry "
        f"({expiry.isoformat()}): the helper paired the oldest-FETCHED row's "
        "window, but a newer row under a shorter window expires first"
    )
    assert after.stale is True


# --------------------------------------------------------------------------- #
# Order invariance, exhaustively, at a population larger than two              #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_the_boundary_is_identical_under_every_ordering_of_four_claims(
    session: Session, permutable: Corpus
) -> None:
    """Exhaustive at n=4 -- 24 orderings, which is the COMPLETE permutation space.

    PR #103 demonstrated anchor invariance by running the suite with a
    first-match and a last-match anchor, and was careful to say what that did and
    did not prove: at its population of **2**, first-versus-last IS the whole
    permutation space, so it was exhaustive for that corpus and a SAMPLE for any
    larger one. Orderings exist at n>2 that are neither first nor last.

    ``_published_claim_expiries`` walks ``provider.services`` and then
    ``svc.offers``, neither of which declares an ``order_by``, so the list it
    returns is in an unspecified order that is free to differ between runs.
    Permuting that list is therefore a faithful model of the hazard, and at four
    claims every one of the 4! = 24 orderings can be enumerated rather than
    sampled.

    What this establishes: the DERIVATION is order-invariant at n=4. What it does
    NOT establish: anything about ``_free_offer``'s anchor over the published
    cloudflare corpus, which still holds exactly two anchor-eligible offers and
    so cannot be permuted past n=2 at all.
    """

    claims = _published_claim_expiries(session, permutable.provider_slug)
    assert len(claims) == 4, f"the permutation corpus must publish four claims, got {len(claims)}"

    orderings = list(itertools.permutations(claims))
    assert len(orderings) == 24, "4! is 24; anything else means the population changed"

    reference = _derive_provider_boundary(list(orderings[0]), permutable.provider_slug)
    for ordering in orderings[1:]:
        assert _derive_provider_boundary(list(ordering), permutable.provider_slug) == reference

    # The invariance must not be the trivial kind. Reading at ONE claim's expiry
    # instead of the provider's would give a different answer depending on which
    # claim, which is exactly the defect #103 removed -- so the boundary really is
    # doing work here, and the 24 identical answers are not identical because
    # every candidate answer was the same.
    expiries = sorted(fetched + window for _, fetched, window in claims)
    assert len(set(expiries)) == 4, (
        "the four claims expire at the same instant, so any ordering would agree "
        "trivially and this proves nothing"
    )
    assert reference["current_at"] == expiries[0]
    assert reference["claim_count"] == 4


@skip_without_db
def test_the_permutation_corpus_is_one_the_derivation_actually_accepts(
    session: Session, permutable: Corpus
) -> None:
    """A refusal for all 24 orderings would also be 'invariant', and prove nothing.

    Both preconditions must hold, or the exhaustive comparison above is comparing
    24 identical exceptions rather than 24 identical boundaries.
    """

    claims = _published_claim_expiries(session, permutable.provider_slug)
    assert _derivation_refusal(claims, permutable.provider_slug) is None

    expiries = _expiries(claims)
    assert max(expiries) - min(expiries) < ONE_SECOND
    assert not _one_second_arm_is_ambiguous(session, claims)

    # ...and it is still a corpus with more than one window, so acceptance here
    # is not an artefact of every claim being identical.
    assert len({window for _, _, window in claims}) == 2


# --------------------------------------------------------------------------- #
# Scope, stated as an executable fact rather than a claim in a report          #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_a_provider_other_than_cloudflare_is_now_exercised(
    session: Session, aligned: Corpus
) -> None:
    """Every boundary assertion before this one read a single provider slug.

    This does NOT add real-provider coverage: the corpus is synthetic and never
    passes through the config or ingest path. What it establishes is that the
    boundary helpers are not accidentally coupled to ``cloudflare``.
    """

    assert aligned.provider_slug != "cloudflare"
    claims = _published_claim_expiries(session, aligned.provider_slug)
    assert claims, "the helper returned nothing for a provider it did not hard-code"

    _publish(session)
    cloudflare = _published_claim_expiries(session, "cloudflare")
    assert cloudflare, "the published corpus must still be readable alongside a synthetic one"
    assert {vid for vid, _, _ in claims}.isdisjoint({vid for vid, _, _ in cloudflare})
