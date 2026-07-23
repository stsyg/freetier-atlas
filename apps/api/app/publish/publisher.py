"""The publication action: the first sanctioned config->offer path (F005 slice 2).

This is where an official, gated candidate finally becomes a *published* offer.
F004 deliberately withheld publication; F005 introduces it as a deterministic,
gated write. For each candidate this module:

1. re-validates the material numbers (:mod:`app.publish.revalidate`);
2. derives the deterministic confidence + completeness/freshness signals
   (:mod:`app.publish.confidence`);
3. evaluates the publication gate (:mod:`app.publish.gate`);
4. on ``publish`` -- upserts the ``service`` / ``offer``, appends an **immutable**
   ``offer_version`` (never updates or deletes one), writes its ``quota`` rows,
   links the candidate's official ``evidence`` to the new version, classifies the
   offer's zero-cost class through the existing :func:`classify_offer` bridge,
   and records a *published* ``change_event``;
5. on ``review`` -- raises a pending ``review_item`` (never a version);
6. on ``withhold`` -- does nothing.

Hard invariants (docs/DATA_MODEL.md, docs/SECURITY_PRIVACY_ABUSE.md):

* **``offer_version`` is append-only.** A new version is INSERTed with its final
  ``zero_cost_class`` and ``material_facts`` already set (classification happens
  in memory *before* the insert); an existing version is never UPDATEd or
  DELETEd, so ``trg_offer_version_immutable`` is untouched.
* **Idempotent.** Re-publishing identical material facts produces *no* new
  version -- the deterministic ``content_hash`` (computed over the stable material
  facts only, never over the time-varying confidence/freshness) matches the
  latest version and the publish is a no-op beyond re-verification.
* **Only official + evidenced data can publish.** The gate withholds anything
  unofficial or unevidenced, so community/quarantined data can never reach
  ``offer`` / ``offer_version`` / ``quota``.
* **No LLM, no network.** Publication is a pure deterministic function of the
  already-persisted candidate facts and evidence; the caller owns the
  transaction (this flushes, never commits).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classify.orm import classify_offer
from app.config.models import PublishingSection
from app.ingest.reconcile import (
    _canon,
    _freshest_fetched_at,
    _identity_of,
    _pending_conflict_exists,
    assess_staleness,
)
from app.ingest.scan import _content_hash
from app.models.domain import (
    Candidate,
    ChangeEvent,
    Evidence,
    Offer,
    OfferVersion,
    Quota,
    ReviewItem,
    ScanRun,
    Service,
    Source,
)

from . import gate as gate_mod
from .confidence import (
    ConfidenceSignals,
    completeness,
    compute_confidence,
    freshness_ratio,
    signals_as_material_fact,
)
from .gate import GateConditions, GateDecision, evaluate_gate
from .revalidate import RevalidatedQuota, RevalidationResult, revalidate_quotas

#: The default exhaustion behaviour attached to a quota when a candidate does not
#: carry a known one. ``"unknown"`` is a valid vocabulary value and, per the Z0
#: engine, blocks a Z0 verdict -- we never guess a safe behaviour.
_UNKNOWN_EXHAUSTION = "unknown"


@dataclass(frozen=True)
class PublishOutcome:
    """What publishing one candidate produced."""

    candidate_id: int
    decision: str
    confidence: float
    offer_id: int | None = None
    offer_version_id: int | None = None
    version_created: bool = False
    change_event_created: bool = False
    review_item_created: bool = False
    zero_cost_class: str | None = None
    failed_conditions: tuple[str, ...] = ()


@dataclass
class PublishResult:
    """A tally of one :func:`publish_scan` pass."""

    outcomes: list[PublishOutcome] = field(default_factory=list)

    @property
    def published(self) -> int:
        return sum(1 for o in self.outcomes if o.version_created)

    @property
    def unchanged(self) -> int:
        return sum(
            1 for o in self.outcomes if o.decision == gate_mod.PUBLISH and not o.version_created
        )

    @property
    def reviewed(self) -> int:
        return sum(1 for o in self.outcomes if o.decision == gate_mod.REVIEW)

    @property
    def withheld(self) -> int:
        return sum(1 for o in self.outcomes if o.decision == gate_mod.WITHHOLD)


# --- Signal derivation ------------------------------------------------------


def _exhaustion_of(facts: Mapping[str, Any]) -> str:
    value = facts.get("exhaustion_behaviour")
    if value is None:
        return _UNKNOWN_EXHAUSTION
    text = str(value).strip()
    return text or _UNKNOWN_EXHAUSTION


def _official_evidence(session: Session, candidate: Candidate) -> list[Evidence]:
    return list(
        session.execute(
            select(Evidence).where(
                Evidence.candidate_id == candidate.id,
                Evidence.official.is_(True),
            )
        ).scalars()
    )


def _build_conditions(
    session: Session,
    *,
    candidate: Candidate,
    source: Source,
    facts: Mapping[str, Any],
    revalidation: RevalidationResult,
    evidence_backed: bool,
    now: datetime,
) -> tuple[GateConditions, ConfidenceSignals]:
    """Derive the deterministic gate conditions + confidence signals."""

    official = bool(candidate.official and source.official)

    # Reproducible: re-hashing the persisted facts reproduces the candidate's
    # stored content hash (an identical re-scan would yield identical facts).
    reproducible = _content_hash(facts) == candidate.content_hash

    # No contradiction: no *pending* cross-source conflict for this identity.
    identity_key = _canon(_identity_of(candidate.provider, facts))
    no_contradiction = not _pending_conflict_exists(session, identity_key=identity_key)

    # Freshness: is the source's freshest evidence within its policy window?
    freshest = _freshest_fetched_at(session, source_id=source.id)
    if freshest is None:
        fresh = False
        fresh_ratio = 0.0
    else:
        staleness = assess_staleness(freshest, now, source.schedule)
        fresh = not staleness.stale
        fresh_ratio = freshness_ratio(staleness.age, staleness.window)

    complete_ratio, _missing = completeness(facts, revalidation)
    schema_complete = complete_ratio >= 1.0

    signals = ConfidenceSignals(
        official=official,
        evidence_backed=evidence_backed,
        deterministic=revalidation.deterministic,
        reproducible=reproducible,
        no_contradiction=no_contradiction,
        completeness=complete_ratio,
        freshness=fresh_ratio,
    )
    confidence = compute_confidence(signals)

    conditions = GateConditions(
        official=official,
        schema_complete=schema_complete,
        deterministic=revalidation.deterministic,
        reproducible=reproducible,
        evidence_backed=evidence_backed,
        no_contradiction=no_contradiction,
        fresh=fresh,
        confidence=confidence,
    )
    return conditions, signals


# --- Persistence helpers ----------------------------------------------------


def _resolve_service(session: Session, *, provider_id: int, canonical_name: str) -> Service:
    existing = session.execute(
        select(Service).where(
            Service.provider_id == provider_id,
            Service.canonical_name == canonical_name,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    service = Service(
        provider_id=provider_id,
        category_id=None,
        canonical_name=canonical_name,
        deployment_model="managed",
    )
    session.add(service)
    session.flush()
    return service


def _resolve_offer(
    session: Session, *, service_id: int, facts: Mapping[str, Any], now: datetime
) -> Offer:
    offer_type = str(facts["offer_type"])
    existing = session.execute(
        select(Offer).where(
            Offer.service_id == service_id,
            Offer.offer_type == offer_type,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.requires_card = facts.get("requires_card")
        existing.has_paid_dependencies = facts.get("has_paid_dependencies")
        return existing
    offer = Offer(
        service_id=service_id,
        offer_type=offer_type,
        zero_cost_class="UNKNOWN",
        requires_card=facts.get("requires_card"),
        has_paid_dependencies=facts.get("has_paid_dependencies"),
        first_seen_at=now,
    )
    session.add(offer)
    session.flush()
    return offer


def _stable_material_facts(
    facts: Mapping[str, Any], revalidation: RevalidationResult
) -> dict[str, Any]:
    """The deterministic, time-invariant material facts hashed into a version.

    Deliberately excludes confidence / freshness (which vary with time) so an
    identical re-publish reproduces an identical ``content_hash`` (idempotency).
    """

    return {
        "offer_type": facts.get("offer_type"),
        "requires_card": facts.get("requires_card"),
        "has_paid_dependencies": facts.get("has_paid_dependencies"),
        "exhaustion_behaviour": _exhaustion_of(facts),
        "quotas": [q.as_material_fact() for q in revalidation.quotas],
    }


def _latest_version(session: Session, *, offer_id: int) -> OfferVersion | None:
    return session.execute(
        select(OfferVersion)
        .where(OfferVersion.offer_id == offer_id)
        .order_by(OfferVersion.version_number.desc())
        .limit(1)
    ).scalar_one_or_none()


def _make_quota(revalidated: RevalidatedQuota) -> Quota:
    amount: Decimal | None = revalidated.amount
    return Quota(
        metric=revalidated.metric,
        amount=amount,
        unit=revalidated.unit,
        reset_period=revalidated.reset_period,
        behaviour="unknown",
        exhaustion_behaviour=revalidated.exhaustion_behaviour,
    )


def _do_publish(
    session: Session,
    *,
    candidate: Candidate,
    source: Source,
    facts: Mapping[str, Any],
    revalidation: RevalidationResult,
    signals: ConfidenceSignals,
    decision: GateDecision,
    evidence_rows: Sequence[Evidence],
    now: datetime,
) -> PublishOutcome:
    """Perform the gated publish: upsert offer, append version, classify, record."""

    service = _resolve_service(
        session,
        provider_id=source.provider_id,
        canonical_name=str(facts["service"]),
    )
    offer = _resolve_offer(session, service_id=service.id, facts=facts, now=now)

    stable = _stable_material_facts(facts, revalidation)
    content_hash = _content_hash(stable)

    prior = _latest_version(session, offer_id=offer.id)

    # Idempotency: identical material facts -> no new version (just re-verify).
    if prior is not None and prior.content_hash == content_hash:
        offer.last_verified_at = now
        return PublishOutcome(
            candidate_id=candidate.id,
            decision=gate_mod.PUBLISH,
            confidence=decision.confidence,
            offer_id=offer.id,
            offer_version_id=prior.id,
            version_created=False,
            zero_cost_class=prior.zero_cost_class,
        )

    version_number = (prior.version_number + 1) if prior is not None else 1

    # Build the new version + quotas in memory so classification can run against
    # them BEFORE the row is inserted -- the immutable version is written once,
    # with its final zero_cost_class and material_facts already set.
    version = OfferVersion(
        offer_id=offer.id,
        version_number=version_number,
        content_hash=content_hash,
        offer_type=str(facts["offer_type"]),
        zero_cost_class="UNKNOWN",
    )
    for revalidated in revalidation.quotas:
        version.quotas.append(_make_quota(revalidated))
    offer.versions.append(version)

    classification = classify_offer(offer, version)

    version.zero_cost_class = classification.zero_cost_class
    offer.zero_cost_class = classification.zero_cost_class
    version.material_facts = {
        **stable,
        "confidence": decision.confidence,
        "confidence_signals": signals_as_material_fact(signals),
        "classification": {
            "zero_cost_class": classification.zero_cost_class,
            "reasons": list(classification.reasons),
            "blocking_conditions": list(classification.blocking_conditions),
        },
        "gate": {
            "decision": decision.decision,
            "automatic_threshold": decision.automatic_threshold,
            "uncertain_threshold": decision.uncertain_threshold,
            "reasons": list(decision.reasons),
        },
    }

    session.add(version)
    session.flush()

    # Link the candidate's official evidence to the freshly-published version.
    # The evidence keeps its candidate_id (still official), so the 0006
    # candidate/evidence separation trigger re-fires and passes.
    for evidence in evidence_rows:
        evidence.offer_version_id = version.id

    change_type = "modified" if prior is not None else "added"
    session.add(
        ChangeEvent(
            offer_id=offer.id,
            previous_version_id=prior.id if prior is not None else None,
            new_version_id=version.id,
            change_type=change_type,
            materiality="material",
            publication_status="published",
        )
    )

    offer.last_verified_at = now
    session.flush()

    return PublishOutcome(
        candidate_id=candidate.id,
        decision=gate_mod.PUBLISH,
        confidence=decision.confidence,
        offer_id=offer.id,
        offer_version_id=version.id,
        version_created=True,
        change_event_created=True,
        zero_cost_class=classification.zero_cost_class,
    )


def _raise_review(
    session: Session,
    *,
    scan_run_id: int | None,
    candidate: Candidate,
    facts: Mapping[str, Any],
    decision: GateDecision,
) -> PublishOutcome:
    """Raise a pending review item (never a version). Deduped by identity."""

    identity_key = _canon(_identity_of(candidate.provider, facts))
    if _pending_conflict_exists(session, identity_key=identity_key):
        # A pending review for this identity already exists (e.g. a contradiction
        # raised during reconciliation); do not duplicate it.
        return PublishOutcome(
            candidate_id=candidate.id,
            decision=gate_mod.REVIEW,
            confidence=decision.confidence,
            review_item_created=False,
            failed_conditions=decision.failed_conditions,
        )

    session.add(
        ReviewItem(
            scan_run_id=scan_run_id,
            offer_id=None,
            reason="publication_gate: uncertain evidence held for review, not published",
            evidence_conflict={
                "identity_key": identity_key,
                "gate_decision": decision.decision,
                "confidence": decision.confidence,
                "automatic_threshold": decision.automatic_threshold,
                "uncertain_threshold": decision.uncertain_threshold,
                "failed_conditions": list(decision.failed_conditions),
                "reasons": list(decision.reasons),
            },
            candidate_facts=dict(facts),
            recommended_action="manual_review",
            admin_disposition="pending",
        )
    )
    session.flush()
    return PublishOutcome(
        candidate_id=candidate.id,
        decision=gate_mod.REVIEW,
        confidence=decision.confidence,
        review_item_created=True,
        failed_conditions=decision.failed_conditions,
    )


# --- Orchestrators ----------------------------------------------------------


def publish_candidate(
    session: Session,
    candidate: Candidate,
    source: Source,
    publishing: PublishingSection,
    *,
    scan_run_id: int | None = None,
    now: datetime | None = None,
) -> PublishOutcome:
    """Re-validate, gate, and (if permitted) publish a single candidate.

    Pure-deterministic decision; the caller owns the transaction (this flushes,
    never commits). Returns a :class:`PublishOutcome` describing the route taken.
    """

    now = now or datetime.now(UTC)
    facts: Mapping[str, Any] = candidate.candidate_facts or {}

    revalidation = revalidate_quotas(facts, exhaustion_behaviour=_exhaustion_of(facts))
    evidence_rows = _official_evidence(session, candidate)
    conditions, signals = _build_conditions(
        session,
        candidate=candidate,
        source=source,
        facts=facts,
        revalidation=revalidation,
        evidence_backed=bool(evidence_rows),
        now=now,
    )
    decision = evaluate_gate(
        conditions,
        automatic_threshold=publishing.automatic_threshold,
        uncertain_threshold=publishing.uncertain_threshold,
    )

    if decision.decision == gate_mod.PUBLISH:
        return _do_publish(
            session,
            candidate=candidate,
            source=source,
            facts=facts,
            revalidation=revalidation,
            signals=signals,
            decision=decision,
            evidence_rows=evidence_rows,
            now=now,
        )
    if decision.decision == gate_mod.REVIEW:
        return _raise_review(
            session,
            scan_run_id=scan_run_id,
            candidate=candidate,
            facts=facts,
            decision=decision,
        )
    return PublishOutcome(
        candidate_id=candidate.id,
        decision=gate_mod.WITHHOLD,
        confidence=decision.confidence,
        failed_conditions=decision.failed_conditions,
    )


def publish_scan(
    session: Session,
    scan_run: ScanRun,
    source: Source,
    publishing: PublishingSection,
    *,
    now: datetime | None = None,
) -> PublishResult:
    """Run the publication gate over every candidate produced by ``scan_run``.

    Iterates *all* of the scan's candidates (in id order for determinism); the
    gate itself withholds any unofficial / unevidenced candidate, so community
    data can never be published. The caller owns the transaction.
    """

    now = now or datetime.now(UTC)
    candidates = list(
        session.execute(
            select(Candidate).where(Candidate.scan_run_id == scan_run.id).order_by(Candidate.id)
        ).scalars()
    )
    result = PublishResult()
    for candidate in candidates:
        outcome = publish_candidate(
            session,
            candidate,
            source,
            publishing,
            scan_run_id=scan_run.id,
            now=now,
        )
        result.outcomes.append(outcome)
    return result


__all__: Sequence[str] = (
    "PublishOutcome",
    "PublishResult",
    "publish_candidate",
    "publish_scan",
)
