"""Reconciliation: change detection, staleness, and contradiction review.

This is the pass that runs *after* :func:`app.ingest.scan.run_scan` has persisted
the pre-publication :class:`~app.models.domain.Candidate` rows for a scan. It is
deliberately a **separate** step (never wired into ``run_scan``) so that scanning
itself keeps writing zero ``change_event`` rows; reconciliation is where change
history and human-review signals are produced.

It does three things, all operating on *candidate content only*
(docs/ARCHITECTURE.md -> reconciliation):

* **Change detection.** Diff each freshly-scanned candidate against the
  last-known candidate for the same source + identity and, when they differ,
  emit a DRAFT :class:`~app.models.domain.ChangeEvent` with a deterministic
  ``change_type`` (``added`` / ``modified`` / ``withdrawn`` / ``restored``) and a
  ``materiality`` classification. A candidate that disappeared from the source is
  ``withdrawn``; one that reappears after a withdrawal is ``restored``.
* **Staleness.** When the source's freshest snapshot is older than the configured
  schedule/policy window, the affected candidates are flagged
  ``verification_state='stale'``. Stale data is **never** treated as a fresh
  verification.
* **Contradiction.** When two *official* sources present different *known* values
  for the same material fact of the same identity, raise a
  :class:`~app.models.domain.ReviewItem` (``admin_disposition='pending'``) for a
  human. Nothing is auto-resolved.

Hard invariants (docs/DATA_MODEL.md, docs/SECURITY_PRIVACY_ABUSE.md):

* There is **no publication path**. Reconciliation never creates or mutates
  ``offer`` / ``offer_version``; every change event it writes is ``draft`` and
  the ``offer_version`` immutability trigger is untouched.
* Contradictions are never auto-resolved: they always produce a *pending*
  review item.
* Unknown (``None``) values never contradict -- an unknown fact is unknown, not a
  conflict (mirrors the Z0 engine's "unknown is not guessed" rule).

The pure functions (``assess_change``, ``classify_change_type``,
``classify_materiality``, ``assess_staleness``, ``find_contradictions``) take
plain values and do no I/O, so every rule is independently unit-testable. The
:func:`reconcile_scan` orchestrator sequences them over a live session; the
caller owns the surrounding transaction (it flushes, never commits).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models.domain import Candidate, ChangeEvent, ReviewItem, ScanRun, Snapshot, Source

# --- Deterministic materiality vocabulary ----------------------------------

#: Fact fields that materially affect an offer's zero-cost meaning. A change to
#: any of these is ``material``; a contradiction on any of these between official
#: sources raises a review item.
MATERIAL_FACT_FIELDS: tuple[str, ...] = (
    "offer_type",
    "requires_card",
    "has_paid_dependencies",
    "quotas",
)

#: Fact fields that are cosmetic / descriptive: a change to only these is
#: ``non_material``. A changed field in neither set is classified ``unknown``
#: (never guessed as non-material).
NON_MATERIAL_FACT_FIELDS: tuple[str, ...] = (
    "display_name",
    "service_description",
    "documentation_url",
    "notes",
)

# --- Staleness policy defaults ---------------------------------------------

#: Named schedule windows a source may declare in ``Source.schedule``.
NAMED_SCHEDULE_WINDOWS: dict[str, timedelta] = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}

#: Compact ``<n><unit>`` schedule form, e.g. ``6h`` / ``7d`` / ``2w``.
_COMPACT_SCHEDULE = re.compile(r"^(\d+)\s*([smhdw])$")
_COMPACT_UNITS: dict[str, timedelta] = {
    "s": timedelta(seconds=1),
    "m": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
}

#: Fallback freshness window when a source declares no (or an unparseable)
#: schedule.
DEFAULT_STALENESS_WINDOW: timedelta = timedelta(days=7)


# --- Canonicalisation helper -----------------------------------------------


def _canon(value: Any) -> str:
    """Order-stable canonical text for a value.

    Tuples and lists compare equal element-wise and mapping key order is
    ignored, so a fact parsed as a tuple (adapter) and re-read as a list (JSONB)
    compare identically.
    """

    def _norm(v: Any) -> Any:
        if isinstance(v, Mapping):
            return {str(k): _norm(v[k]) for k in v}
        if isinstance(v, (list, tuple)):
            return [_norm(x) for x in v]
        return v

    return json.dumps(_norm(value), sort_keys=True, separators=(",", ":"))


# --- Change classification (pure) ------------------------------------------


@dataclass(frozen=True)
class ChangeAssessment:
    """The result of diffing a prior candidate against a new one."""

    change_type: str | None  # None means "unchanged" (emit nothing).
    materiality: str  # material / non_material / unknown
    changed_fields: tuple[str, ...] = ()


def changed_fields(prior: Mapping[str, Any], new: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the sorted names of fields whose values differ between two facts."""

    keys = set(prior) | set(new)
    return tuple(sorted(k for k in keys if _canon(prior.get(k)) != _canon(new.get(k))))


def classify_materiality(fields: Sequence[str]) -> str:
    """Classify the materiality of a set of changed field names.

    * any material field changed -> ``material``;
    * only known cosmetic fields changed -> ``non_material``;
    * an unrecognised field changed -> ``unknown`` (never guessed non-material).
    """

    fset = set(fields)
    if fset & set(MATERIAL_FACT_FIELDS):
        return "material"
    if fset and fset <= set(NON_MATERIAL_FACT_FIELDS):
        return "non_material"
    if not fset:
        return "non_material"
    return "unknown"


def classify_change_type(
    *, seen_before: bool, present_now: bool, last_withdrawn: bool, facts_changed: bool
) -> str | None:
    """Deterministically classify a candidate's change type.

    Returns ``None`` when there is nothing to record (never seen and still
    absent, already-withdrawn and still absent, or seen and unchanged).
    """

    if not present_now:
        if seen_before and not last_withdrawn:
            return "withdrawn"
        return None
    if not seen_before:
        return "added"
    if last_withdrawn:
        return "restored"
    if facts_changed:
        return "modified"
    return None


def assess_change(
    prior: Mapping[str, Any] | None,
    new: Mapping[str, Any] | None,
    *,
    seen_before: bool | None = None,
    last_withdrawn: bool = False,
) -> ChangeAssessment:
    """Assess the change between a prior and a new candidate's facts.

    ``seen_before`` defaults to "there is a prior facts mapping". A whole
    candidate appearing (``added`` / ``restored``) or disappearing
    (``withdrawn``) is ``material``; a modification's materiality is derived from
    the fields that changed.
    """

    if seen_before is None:
        seen_before = prior is not None
    present_now = new is not None
    fields: tuple[str, ...] = ()
    facts_changed = False
    if present_now and prior is not None:
        fields = changed_fields(prior, new or {})
        facts_changed = bool(fields)

    change_type = classify_change_type(
        seen_before=seen_before,
        present_now=present_now,
        last_withdrawn=last_withdrawn,
        facts_changed=facts_changed,
    )
    if change_type is None:
        return ChangeAssessment(None, "non_material", ())
    if change_type == "modified":
        return ChangeAssessment("modified", classify_materiality(fields), fields)
    # added / restored / withdrawn: a whole offer appears or disappears.
    subject = new if present_now else prior
    return ChangeAssessment(change_type, "material", tuple(sorted(subject or {})))


# --- Staleness (pure) -------------------------------------------------------


@dataclass(frozen=True)
class StalenessAssessment:
    """Whether a source's freshest data is within its policy window."""

    stale: bool
    age: timedelta
    window: timedelta


def parse_schedule_window(
    schedule: str | None, *, default: timedelta = DEFAULT_STALENESS_WINDOW
) -> timedelta:
    """Parse a ``Source.schedule`` string into a freshness window.

    Accepts named windows (``hourly`` / ``daily`` / ``weekly`` / ``monthly``) and
    a compact ``<n><unit>`` form (``6h`` / ``7d`` / ``2w``); anything else falls
    back to ``default`` so an unparseable schedule never crashes reconciliation.
    """

    if not schedule:
        return default
    text = schedule.strip().lower()
    if text in NAMED_SCHEDULE_WINDOWS:
        return NAMED_SCHEDULE_WINDOWS[text]
    match = _COMPACT_SCHEDULE.match(text)
    if match:
        count = int(match.group(1))
        unit = _COMPACT_UNITS[match.group(2)]
        if count > 0:
            return unit * count
    return default


def assess_staleness(
    fetched_at: datetime,
    now: datetime,
    schedule: str | None,
    *,
    default_window: timedelta = DEFAULT_STALENESS_WINDOW,
) -> StalenessAssessment:
    """Assess whether data fetched at ``fetched_at`` is stale as of ``now``."""

    window = parse_schedule_window(schedule, default=default_window)
    age = now - fetched_at
    return StalenessAssessment(stale=age > window, age=age, window=window)


def counts_as_fresh_verification(staleness: StalenessAssessment) -> bool:
    """Stale data must never count as a fresh verification."""

    return not staleness.stale


# --- Contradiction detection (pure) ----------------------------------------


@dataclass(frozen=True)
class ReconcileCandidate:
    """A candidate view used for pure contradiction detection."""

    ref: Any
    source_id: int | None
    identity: tuple[Any, ...]
    facts: Mapping[str, Any]
    official: bool = True


@dataclass(frozen=True)
class FieldConflict:
    """Two or more official sources disagree on one material field."""

    field: str
    values: tuple[str, ...]
    sources: tuple[int, ...]


@dataclass(frozen=True)
class Contradiction:
    """A set of material-field conflicts for one identity."""

    identity: tuple[Any, ...]
    conflicts: tuple[FieldConflict, ...]
    refs: tuple[Any, ...] = field(default_factory=tuple)


def find_contradictions(candidates: Sequence[ReconcileCandidate]) -> list[Contradiction]:
    """Find material-fact contradictions among *official* candidates.

    Only official candidates are considered, only *different-source* pairs can
    contradict (same-source-over-time differences are changes, not conflicts),
    and only *known* (non-``None``) differing values count -- an unknown value is
    never a contradiction.
    """

    groups: dict[tuple[Any, ...], list[ReconcileCandidate]] = defaultdict(list)
    for cand in candidates:
        if cand.official:
            groups[tuple(cand.identity)].append(cand)

    results: list[Contradiction] = []
    for identity in sorted(groups, key=_canon):
        group = sorted(groups[identity], key=lambda c: (c.source_id or 0, _canon(c.ref)))
        per_field_values: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
        conflicting_refs: set[Any] = set()
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.source_id == b.source_id:
                    continue
                for fld in MATERIAL_FACT_FIELDS:
                    av, bv = a.facts.get(fld), b.facts.get(fld)
                    if av is None or bv is None:
                        continue
                    if _canon(av) != _canon(bv):
                        per_field_values[fld][_canon(av)].add(a.source_id or 0)
                        per_field_values[fld][_canon(bv)].add(b.source_id or 0)
                        conflicting_refs.update((a.ref, b.ref))
        if not per_field_values:
            continue
        conflicts = tuple(
            FieldConflict(
                field=fld,
                values=tuple(sorted(values_map)),
                sources=tuple(sorted({s for srcs in values_map.values() for s in srcs})),
            )
            for fld, values_map in sorted(per_field_values.items())
        )
        results.append(
            Contradiction(
                identity=identity,
                conflicts=conflicts,
                refs=tuple(sorted(conflicting_refs, key=_canon)),
            )
        )
    return results


# --- Persistence orchestrator ----------------------------------------------


@dataclass(frozen=True)
class ReconcileResult:
    """A tally of what one reconciliation pass produced."""

    change_events: int = 0
    added: int = 0
    modified: int = 0
    withdrawn: int = 0
    restored: int = 0
    stale_candidates: int = 0
    review_items: int = 0


def _identity_of(provider: str | None, facts: Mapping[str, Any]) -> tuple[Any, ...]:
    """Source-independent content identity used to group cross-source candidates."""

    return (provider, facts.get("service"), facts.get("offer_type"))


def _latest_prior_candidate(
    session: Session, *, source_id: int, candidate_key: str, exclude_scan_run_id: int
) -> Candidate | None:
    stmt = (
        select(Candidate)
        .where(
            Candidate.source_id == source_id,
            Candidate.candidate_key == candidate_key,
            Candidate.scan_run_id != exclude_scan_run_id,
        )
        .order_by(Candidate.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def _latest_change_type(session: Session, *, source_id: int, candidate_key: str) -> str | None:
    linked = aliased(Candidate)
    stmt = (
        select(ChangeEvent.change_type)
        .join(
            linked,
            or_(
                linked.id == ChangeEvent.new_candidate_id,
                linked.id == ChangeEvent.previous_candidate_id,
            ),
        )
        .where(linked.source_id == source_id, linked.candidate_key == candidate_key)
        .order_by(ChangeEvent.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def _prior_scan_id(session: Session, *, source_id: int, current_scan_run_id: int) -> int | None:
    stmt = (
        select(ScanRun.id)
        .where(ScanRun.source_id == source_id, ScanRun.id < current_scan_run_id)
        .order_by(ScanRun.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def _freshest_fetched_at(session: Session, *, source_id: int) -> datetime | None:
    return session.execute(
        select(func.max(Snapshot.fetched_at)).where(Snapshot.source_id == source_id)
    ).scalar_one_or_none()


def _pending_conflict_exists(session: Session, *, identity_key: str) -> bool:
    stmt = (
        select(ReviewItem.id)
        .where(
            ReviewItem.admin_disposition == "pending",
            ReviewItem.evidence_conflict["identity_key"].astext == identity_key,
        )
        .limit(1)
    )
    return session.execute(stmt).scalars().first() is not None


def reconcile_scan(
    scan_run: ScanRun,
    source: Source,
    session: Session,
    *,
    now: datetime | None = None,
) -> ReconcileResult:
    """Reconcile the candidates produced by ``scan_run`` and persist the results.

    Emits DRAFT change events, flags stale candidates, and raises pending review
    items for cross-source official contradictions. Never creates or mutates
    ``offer`` / ``offer_version``. The caller owns the transaction; this flushes
    but does not commit.
    """

    now = now or datetime.now(UTC)

    current = list(
        session.execute(select(Candidate).where(Candidate.scan_run_id == scan_run.id)).scalars()
    )

    added = modified = withdrawn = restored = stale_count = review_count = 0

    # --- Staleness: is the source's freshest data within its policy window? ---
    freshest = _freshest_fetched_at(session, source_id=source.id)
    staleness = assess_staleness(freshest, now, source.schedule) if freshest is not None else None
    if staleness is not None and staleness.stale:
        for candidate in current:
            candidate.verification_state = "stale"
            stale_count += 1

    # --- Change detection: diff each candidate against the last-known one. ---
    current_keys = {c.candidate_key for c in current}
    for candidate in current:
        prior = _latest_prior_candidate(
            session,
            source_id=source.id,
            candidate_key=candidate.candidate_key,
            exclude_scan_run_id=scan_run.id,
        )
        last_change = _latest_change_type(
            session, source_id=source.id, candidate_key=candidate.candidate_key
        )
        assessment = assess_change(
            prior.candidate_facts if prior is not None else None,
            candidate.candidate_facts,
            seen_before=prior is not None,
            last_withdrawn=last_change == "withdrawn",
        )
        if assessment.change_type is None:
            continue
        session.add(
            ChangeEvent(
                offer_id=None,
                previous_candidate_id=prior.id if prior is not None else None,
                new_candidate_id=candidate.id,
                change_type=assessment.change_type,
                materiality=assessment.materiality,
                publication_status="draft",
            )
        )
        added += assessment.change_type == "added"
        modified += assessment.change_type == "modified"
        restored += assessment.change_type == "restored"

    # --- Withdrawals: keys seen in the immediately-prior scan, absent now. ---
    prior_scan_id = _prior_scan_id(session, source_id=source.id, current_scan_run_id=scan_run.id)
    if prior_scan_id is not None:
        prior_rows = list(
            session.execute(
                select(Candidate).where(Candidate.scan_run_id == prior_scan_id)
            ).scalars()
        )
        seen_keys: set[str] = set()
        for prior_candidate in prior_rows:
            key = prior_candidate.candidate_key
            if key in current_keys or key in seen_keys:
                continue
            seen_keys.add(key)
            if _latest_change_type(session, source_id=source.id, candidate_key=key) == "withdrawn":
                continue
            session.add(
                ChangeEvent(
                    offer_id=None,
                    previous_candidate_id=prior_candidate.id,
                    new_candidate_id=None,
                    change_type="withdrawn",
                    materiality="material",
                    publication_status="draft",
                )
            )
            withdrawn += 1

    # --- Contradictions: official cross-source disagreement on a known fact. ---
    review_count += _raise_contradiction_reviews(session, source, scan_run, current)

    session.flush()

    return ReconcileResult(
        change_events=added + modified + withdrawn + restored,
        added=added,
        modified=modified,
        withdrawn=withdrawn,
        restored=restored,
        stale_candidates=stale_count,
        review_items=review_count,
    )


def _raise_contradiction_reviews(
    session: Session,
    source: Source,
    scan_run: ScanRun,
    current: Sequence[Candidate],
) -> int:
    """Compare this scan's official candidates against other official sources."""

    current_official = [c for c in current if c.official]
    if not current_official:
        return 0

    providers = {c.provider for c in current_official}
    views: list[ReconcileCandidate] = [
        ReconcileCandidate(
            ref=c.id,
            source_id=c.source_id,
            identity=_identity_of(c.provider, c.candidate_facts),
            facts=c.candidate_facts,
            official=True,
        )
        for c in current_official
    ]

    # Latest official candidate per (source, key) from *other* sources.
    other_rows = list(
        session.execute(
            select(Candidate)
            .where(
                Candidate.official.is_(True),
                Candidate.source_id != source.id,
                Candidate.provider.in_(providers),
            )
            .order_by(Candidate.id.desc())
        ).scalars()
    )
    seen_other: set[tuple[int, str]] = set()
    for row in other_rows:
        marker = (row.source_id, row.candidate_key)
        if marker in seen_other:
            continue
        seen_other.add(marker)
        views.append(
            ReconcileCandidate(
                ref=row.id,
                source_id=row.source_id,
                identity=_identity_of(row.provider, row.candidate_facts),
                facts=row.candidate_facts,
                official=True,
            )
        )

    created = 0
    for contradiction in find_contradictions(views):
        identity_key = _canon(contradiction.identity)
        if _pending_conflict_exists(session, identity_key=identity_key):
            continue
        conflicts_payload = [
            {"field": fc.field, "values": list(fc.values), "sources": list(fc.sources)}
            for fc in contradiction.conflicts
        ]
        representative = next(
            (c.candidate_facts for c in current_official if c.id in set(contradiction.refs)),
            current_official[0].candidate_facts,
        )
        session.add(
            ReviewItem(
                scan_run_id=scan_run.id,
                reason="evidence_conflict: official sources disagree on material offer facts",
                evidence_conflict={
                    "identity_key": identity_key,
                    "identity": {
                        "provider": contradiction.identity[0],
                        "service": contradiction.identity[1],
                        "offer_type": contradiction.identity[2],
                    },
                    "conflicts": conflicts_payload,
                    "candidate_refs": list(contradiction.refs),
                },
                candidate_facts=dict(representative),
                recommended_action="manual_review",
                admin_disposition="pending",
            )
        )
        created += 1
    return created


__all__ = (
    "MATERIAL_FACT_FIELDS",
    "NON_MATERIAL_FACT_FIELDS",
    "DEFAULT_STALENESS_WINDOW",
    "ChangeAssessment",
    "StalenessAssessment",
    "ReconcileCandidate",
    "FieldConflict",
    "Contradiction",
    "ReconcileResult",
    "changed_fields",
    "classify_materiality",
    "classify_change_type",
    "assess_change",
    "parse_schedule_window",
    "assess_staleness",
    "counts_as_fresh_verification",
    "find_contradictions",
    "reconcile_scan",
)
