"""Declared-vs-derived coverage reconciliation (F008 slice S2).

A provider's ``coverage:`` block is a human declaration. The published catalogue
is evidence. When the two materially disagree -- most importantly when a provider
declares ``unknown`` or ``not_offered`` over a category in which it demonstrably
has a published offer -- that is a durable data-quality problem, so it is
recorded as a pending ``review_item`` and surfaces in the existing F007 admin
review queue (``GET /api/admin/review-queue``). No new admin surface is added and
no admin machinery is changed: this module only writes rows the queue already
projects.

Q11: the derived state is **not** written back anywhere. It is recomputed by the
pure :mod:`app.read_api.coverage` every time it is needed; the review item is the
only durable artefact of a contradiction, which is why the write side lives here
in ``ingest`` rather than in the read-only API.

Idempotency: at most one pending coverage review item exists per (provider,
category). Re-running against an unchanged database creates nothing, and a
mismatch whose declared/derived pair has since changed is left to the admin to
dispose of rather than being silently rewritten.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Provider, ReviewItem
from app.read_api import coverage as coverage_rules
from app.read_api import queries

#: Prefix for the ``review_item.reason`` this module writes. Deliberately
#: distinct from ``evidence_conflict`` so the derivation's contradiction signal
#: cannot feed on this module's own output.
COVERAGE_MISMATCH_REASON = "coverage_mismatch"

#: Marks a review item as produced by coverage reconciliation.
COVERAGE_MISMATCH_KIND = "coverage_declaration_mismatch"


@dataclass(frozen=True)
class CoverageMismatch:
    """One (provider, category) pair whose declaration the catalogue contradicts."""

    provider_slug: str
    category_slug: str
    declared_state: str
    derived_state: str
    detail: dict[str, object]

    @property
    def identity_key(self) -> str:
        return f"{self.provider_slug}/{self.category_slug}"

    def describe(self) -> str:
        return coverage_rules.describe_mismatch(self.detail)


@dataclass
class CoverageReconcileResult:
    """A summary of one :func:`reconcile_coverage` run."""

    mismatches: list[CoverageMismatch] = field(default_factory=list)
    created: int = 0
    existing: int = 0


def find_coverage_mismatches(session: Session, *, now: datetime) -> list[CoverageMismatch]:
    """Compute every material declared-vs-derived contradiction. Read-only.

    ``now`` is REQUIRED. It was previously optional and forwarded ``None``
    straight into :func:`~app.read_api.queries.coverage_signal_context`, whose
    ``now`` PR #99 had just made required precisely to remove an invented clock.

    That is the subtle part, and it is worth stating plainly: making a
    keyword-only parameter required constrains **arity, not nullability**. This
    function satisfied "the argument must be supplied" while supplying ``None``,
    so the remedy one level down was defeated by its own caller. There is no
    static type checker on the Python side of this repository (see
    ``requirements-dev.txt``), so the ``now: datetime`` annotation there caught
    nothing; the only real enforcement is at runtime.

    Measured, not assumed: ``None`` does not fail open here, it raises
    ``TypeError: unsupported operand type(s) for -: 'NoneType' and
    'datetime.datetime'`` from ``reconcile.py`` the moment any evidence row with
    a real ``fetched_at`` is reached. It stayed invisible only because
    :func:`assert_no_coverage_contradictions` -- the reusable helper the Wave-3
    provider slices call from their own tests -- exercises data that never
    reaches a fetched snapshot. It was a latent crash waiting for the first
    provider slice with real evidence, not a defect the suite had ruled out.
    """

    providers = queries.fetch_providers(session)
    if not providers:
        return []
    cat_map = queries.category_map_for_providers(session, providers)
    context = queries.coverage_signal_context(session, providers, now=now)

    # Reuse the serialiser so the review queue and the public matrix can never
    # disagree about what a mismatch is.
    from app.read_api import service as read_service

    matrix = read_service.serialize_category_matrix(providers, cat_map, context)

    mismatches: list[CoverageMismatch] = []
    for row in matrix.categories:
        for entry in row.providers:
            if not entry.mismatch:
                continue
            detail = {
                "provider": entry.provider_slug,
                "category": row.slug,
                "declared_state": entry.declared_state or coverage_rules.UNKNOWN,
                "derived_state": entry.derived_state,
                "published_offer_count": entry.published_offer_count,
                "free_offer_count": entry.free_offer_count,
            }
            mismatches.append(
                CoverageMismatch(
                    provider_slug=entry.provider_slug,
                    category_slug=row.slug,
                    declared_state=entry.declared_state or coverage_rules.UNKNOWN,
                    derived_state=entry.derived_state,
                    detail=detail,
                )
            )
    return mismatches


def _pending_coverage_item_exists(session: Session, *, identity_key: str) -> bool:
    stmt = (
        select(ReviewItem.id)
        .where(
            ReviewItem.admin_disposition == "pending",
            ReviewItem.evidence_conflict["kind"].astext == COVERAGE_MISMATCH_KIND,
            ReviewItem.evidence_conflict["identity_key"].astext == identity_key,
        )
        .limit(1)
    )
    return session.execute(stmt).scalars().first() is not None


def reconcile_coverage(
    session: Session, *, scan_run_id: int | None = None, now: datetime
) -> CoverageReconcileResult:
    """Raise a pending review item for every material coverage contradiction.

    Returns a summary; the caller owns the transaction (this flushes but never
    commits). Nothing is published, changed or auto-corrected -- a contradiction
    is a question for a human, not something to resolve by picking a side.

    ``now`` is REQUIRED for the same reason as on :func:`find_coverage_mismatches`.
    It previously read ``now or datetime.now(UTC)``, which left this file holding
    both halves of the defect at once: a callee that demanded a clock and a
    caller that would manufacture one. Whether a snapshot is stale decides
    whether a coverage declaration is a contradiction worth a human's attention,
    so the moment that question is asked at is part of the answer.
    """

    result = CoverageReconcileResult()
    for mismatch in find_coverage_mismatches(session, now=now):
        result.mismatches.append(mismatch)
        if _pending_coverage_item_exists(session, identity_key=mismatch.identity_key):
            result.existing += 1
            continue
        session.add(
            ReviewItem(
                scan_run_id=scan_run_id,
                reason=f"{COVERAGE_MISMATCH_REASON}: {mismatch.describe()}",
                evidence_conflict={
                    "kind": COVERAGE_MISMATCH_KIND,
                    "identity_key": mismatch.identity_key,
                    "provider_slug": mismatch.provider_slug,
                    "category_slug": mismatch.category_slug,
                    "declared_state": mismatch.declared_state,
                    "derived_state": mismatch.derived_state,
                    # Written out in full so a reviewer reading the queue never
                    # has to re-run the derivation to understand the question.
                    "explanation": mismatch.describe(),
                },
                candidate_facts=dict(mismatch.detail),
                recommended_action="manual_review",
                admin_disposition="pending",
            )
        )
        result.created += 1
    session.flush()
    return result


def assert_no_coverage_contradictions(session: Session, *, provider_slug: str) -> None:
    """Fail loudly when ``provider_slug`` declares coverage its catalogue refutes.

    The reusable assertion the six Wave-3 provider slices call from their own
    tests, so a provider that quietly declares ``unknown`` over a real published
    offer fails *its own* suite rather than only showing up in an admin queue
    nobody is watching during development.
    """

    provider = session.execute(
        select(Provider).where(Provider.slug == provider_slug)
    ).scalar_one_or_none()
    if provider is None:
        raise AssertionError(f"provider {provider_slug!r} is not in the database")

    # BOUNDARY: this helper is invoked directly by a provider slice's own test as
    # a complete operation -- there is no caller holding a moment for it to
    # inherit, and no sibling clock-consumer inside the same operation for it to
    # disagree with. So it legitimately sources the clock, and does so on an
    # explicit line rather than through an optional parameter that defaults.
    #
    # It must NOT gain a `now: datetime | None = None`. That is exactly the shape
    # being removed: until this change the omitted clock travelled as `None` into
    # `find_coverage_mismatches` and on into `coverage_signal_context`, whose
    # `now` is required -- satisfying arity while violating the type, with no
    # static checker in this repo to notice. It raised nothing only because the
    # data these tests build never reaches a snapshot with a `fetched_at`.
    now = datetime.now(UTC)
    offending = [
        m for m in find_coverage_mismatches(session, now=now) if m.provider_slug == provider_slug
    ]
    if offending:
        lines = "\n  ".join(m.describe() for m in offending)
        raise AssertionError(
            f"{provider_slug}: {len(offending)} coverage declaration(s) contradict the "
            f"published catalogue:\n  {lines}\n"
            "Update the provider's coverage: block to match the evidence (or fix the "
            "evidence). An unknown/not_offered declaration over a real published offer, "
            "a verified_free claim whose backing snapshot has expired, and a "
            "verified_free claim the catalogue has never corroborated are all forms of "
            "the dishonesty F008 exists to prevent."
        )


__all__: Sequence[str] = (
    "COVERAGE_MISMATCH_KIND",
    "COVERAGE_MISMATCH_REASON",
    "CoverageMismatch",
    "CoverageReconcileResult",
    "assert_no_coverage_contradictions",
    "find_coverage_mismatches",
    "reconcile_coverage",
)
