"""Test support: PERSISTED synthetic corpora for evidence-currency boundary work.

Distinct from :mod:`tests.support.synthetic`, which builds *transient* (never
added to a session) ORM graphs for the adviser. These rows are really INSERTed,
because the code under test here reaches the database through raw SQL
(:func:`app.read_api.queries.fetch_evidence_currency` joins ``evidence`` ->
``snapshot`` -> ``source``) and a transient graph is invisible to it.

Why synthetic data is the right instrument here
-----------------------------------------------
The question these corpora exist to answer is about the REACHABILITY of a code
path, not about present-day incidence. Two behaviours of
``tests/integration/test_catalogue_currency.py`` -- selecting the OLDEST evidence
row for a version, and refusing a corpus whose claims expire more than a second
apart -- cannot be told apart from their wrong alternatives by the only corpus
the suite has, because in it:

* every published version carries exactly **one** evidence row, so "oldest" has
  an empty discriminating population; and
* every source declares an **unparseable** ``schedule_ref`` (``official_pages``,
  ``rss``, ``mcp_documentation``), so ``parse_schedule_window`` returns the same
  7-day fallback for all of them and there is only ever ONE window in play.

Constructing the input is therefore the correct instrument. This is NOT the same
as seeding a corpus to take a *measurement* from -- a currency or coverage figure
read off self-seeded data would just be measuring this module's own clock.

Nothing here is ever published or served: every row is created inside a test's
own transaction, which the ``session`` fixture rolls back.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.models.domain import (
    Evidence,
    Offer,
    OfferVersion,
    Provider,
    Service,
    Snapshot,
    Source,
)
from sqlalchemy.orm import Session

#: A fixed base moment, so a corpus is reproducible run to run. The production
#: functions all take their clock as an argument, so nothing here reads a wall
#: clock and no assertion depends on when the test happens to run.
BASE_MOMENT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

#: Marked unmistakably synthetic. These rows describe no real provider and make
#: no claim that anything is free; they exist to give an unreachable branch a
#: population.
SYNTHETIC_PREFIX = "synthetic-boundary-probe"


@dataclass(frozen=True)
class EvidenceSpec:
    """One evidence row: when its snapshot was fetched, and its source's schedule.

    ``schedule`` is written into ``Source.schedule`` and read back through the
    real :func:`app.ingest.reconcile.parse_schedule_window`, so the window is
    derived exactly as production derives it -- ``"daily"`` and ``"weekly"`` are
    genuinely 1 and 7 days, not numbers this module invented.
    """

    offset: timedelta
    schedule: str


@dataclass(frozen=True)
class ClaimSpec:
    """One published offer, and the evidence rows its current version rests on."""

    name: str
    evidence: Sequence[EvidenceSpec]


@dataclass
class Corpus:
    """What was built, so a test can assert against it without re-deriving it."""

    provider_slug: str
    base: datetime
    version_id_by_name: dict[str, int] = field(default_factory=dict)
    offer_id_by_name: dict[str, int] = field(default_factory=dict)
    #: ``name -> [(fetched_at, schedule)]`` exactly as inserted.
    evidence_by_name: dict[str, list[tuple[datetime, str]]] = field(default_factory=dict)

    def fetched_at(self, name: str, index: int) -> datetime:
        return self.evidence_by_name[name][index][0]


def build_corpus(
    session: Session,
    *,
    suffix: str,
    claims: Sequence[ClaimSpec],
    base: datetime = BASE_MOMENT,
) -> Corpus:
    """Persist one synthetic provider whose published claims match ``claims``.

    ``suffix`` distinguishes corpora built within one transaction; both
    ``Provider.slug`` and ``Source.slug`` are UNIQUE, so two corpora in the same
    test must not collide.
    """

    slug = f"{SYNTHETIC_PREFIX}-{suffix}"
    provider = Provider(
        slug=slug,
        name=f"Synthetic boundary probe ({suffix})",
        type="synthetic",
        official_domains=[],
    )
    session.add(provider)
    session.flush()

    service = Service(
        provider_id=provider.id,
        canonical_name=f"Synthetic service ({suffix})",
        deployment_model="managed",
        portability_traits=[],
    )
    session.add(service)
    session.flush()

    # One Source per DISTINCT declared schedule. Sharing a source between claims
    # is what makes "two sources declaring different windows" a property of the
    # corpus rather than an accident of how many rows were written.
    sources: dict[str, Source] = {}
    for claim in claims:
        for spec in claim.evidence:
            if spec.schedule in sources:
                continue
            source = Source(
                provider_id=provider.id,
                slug=f"{slug}-{spec.schedule}",
                adapter_type="html",
                trust_level="official",
                official=True,
                # Deliberately not an http(s) URL: these rows are synthetic and
                # must never be mistaken for a fetchable official location.
                endpoint=f"synthetic://{slug}/{spec.schedule}",
                schedule=spec.schedule,
                enabled=True,
            )
            session.add(source)
            sources[spec.schedule] = source
    session.flush()

    corpus = Corpus(provider_slug=slug, base=base)
    for claim in claims:
        offer = Offer(
            service_id=service.id,
            offer_type="always_free",
            zero_cost_class="Z0_TRUE_FREE",
            status="active",
            visibility="public",
        )
        session.add(offer)
        session.flush()

        version = OfferVersion(
            offer_id=offer.id,
            version_number=1,
            content_hash=f"{slug}-{claim.name}-v1",
            offer_type="always_free",
            zero_cost_class="Z0_TRUE_FREE",
            material_facts={},
        )
        session.add(version)
        session.flush()

        rows: list[tuple[datetime, str]] = []
        for index, spec in enumerate(claim.evidence):
            source = sources[spec.schedule]
            fetched_at = base + spec.offset
            snapshot = Snapshot(
                source_id=source.id,
                content_location=f"synthetic://{slug}/{claim.name}/{index}",
                mime_type="text/html",
                content_hash=f"{slug}-{claim.name}-{index}",
                fetched_at=fetched_at,
            )
            session.add(snapshot)
            session.flush()
            session.add(
                Evidence(
                    source_id=source.id,
                    snapshot_id=snapshot.id,
                    offer_version_id=version.id,
                    official=True,
                    content_hash=snapshot.content_hash,
                )
            )
            rows.append((fetched_at, spec.schedule))

        corpus.offer_id_by_name[claim.name] = offer.id
        corpus.version_id_by_name[claim.name] = version.id
        corpus.evidence_by_name[claim.name] = rows

    session.flush()
    return corpus


# --------------------------------------------------------------------------- #
# The two corpora this work needs, described where they are defined            #
# --------------------------------------------------------------------------- #

#: ALIGNED: within every version the oldest-FETCHED evidence row is also the
#: earliest-EXPIRING one, and across the provider the oldest-fetched claim is the
#: earliest-expiring claim. So the second precondition of ``provider_boundary``
#: holds and only the expiry SPREAD is out of range.
#:
#:   two_rows : evidence at +0h under a 1-day window  -> expires base + 1d
#:              evidence at +2h under a 7-day window  -> expires base + 2h + 7d
#:              => oldest row is also the earliest-expiring row
#:   one_row  : evidence at +1h under a 7-day window  -> expires base + 1h + 7d
#:
#: expiry spread = (base + 7d1h) - (base + 1d) = 6 days 1 hour, so a ONE-SECOND
#: differential cannot be unambiguous for both claims.
ALIGNED_CLAIMS: tuple[ClaimSpec, ...] = (
    ClaimSpec(
        name="two_rows",
        evidence=(
            EvidenceSpec(offset=timedelta(0), schedule="daily"),
            EvidenceSpec(offset=timedelta(hours=2), schedule="weekly"),
        ),
    ),
    ClaimSpec(
        name="one_row",
        evidence=(EvidenceSpec(offset=timedelta(hours=1), schedule="weekly"),),
    ),
)

#: INVERTED: within the version the oldest-FETCHED evidence row carries the
#: LONGER window, so it is NOT the earliest-expiring row.
#:
#:   inverted : evidence at +0h under a 7-day window -> expires base + 7d
#:              evidence at +1h under a 1-day window -> expires base + 1h + 1d
#:
#: A version is only as current as its stalest support, so production currency
#: flips at ``base + 1h + 1d``. Any helper that takes the oldest-FETCHED row and
#: pairs THAT row's window reports ``base + 7d`` instead.
INVERTED_CLAIMS: tuple[ClaimSpec, ...] = (
    ClaimSpec(
        name="inverted",
        evidence=(
            EvidenceSpec(offset=timedelta(0), schedule="weekly"),
            EvidenceSpec(offset=timedelta(hours=1), schedule="daily"),
        ),
    ),
)

#: PERMUTABLE: FOUR claims that the boundary derivation ACCEPTS, so its output
#: can be compared across every ordering of them.
#:
#: PR #103 demonstrated anchor invariance by running the suite with a first-match
#: and a last-match anchor. At its population of 2 that is the complete
#: permutation space; at 3 or more it is a sample, because orderings exist that
#: are neither first nor last. Four claims give 24 orderings, which is small
#: enough to enumerate exhaustively.
#:
#: Satisfying both preconditions while still declaring two distinct windows is
#: tightly constrained, and the arrangement below is the reason why. The
#: oldest-FETCHED claim must also be the earliest-EXPIRING one, so the claim with
#: the LONGER window has to be the oldest; the shorter-window claims are
#: therefore fetched much later, landing their expiries just after it:
#:
#:   anchor : +0        under 1 day  -> expires base + 1d          (oldest, earliest)
#:   near_a : +23h100ms under 1 hour -> expires base + 1d + 100ms
#:   near_b : +23h200ms under 1 hour -> expires base + 1d + 200ms
#:   near_c : +23h300ms under 1 hour -> expires base + 1d + 300ms
#:
#: expiry spread = 300 ms, inside the one-second guard, so the derivation
#: accepts; two distinct parsed windows are in play; and the claim count is 4.
PERMUTABLE_CLAIMS: tuple[ClaimSpec, ...] = (
    ClaimSpec(name="anchor", evidence=(EvidenceSpec(timedelta(0), "daily"),)),
    ClaimSpec(
        name="near_a",
        evidence=(EvidenceSpec(timedelta(hours=23, milliseconds=100), "hourly"),),
    ),
    ClaimSpec(
        name="near_b",
        evidence=(EvidenceSpec(timedelta(hours=23, milliseconds=200), "hourly"),),
    ),
    ClaimSpec(
        name="near_c",
        evidence=(EvidenceSpec(timedelta(hours=23, milliseconds=300), "hourly"),),
    ),
)
