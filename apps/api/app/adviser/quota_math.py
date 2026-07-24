"""Exact-Decimal quota fit / headroom math for the adviser (F006 slice 3).

This module decides, for a single demand and a single offer's quotas, whether
the demand is *covered* -- i.e. whether the offer's free quota is at least the
demand at a **known, comparable unit** -- using exact :class:`decimal.Decimal`
arithmetic end to end. A $0 guarantee must never turn on a binary-float rounding
artefact, so a boundary case where headroom is exactly zero fits, and a case one
unit over does not.

Everything here **fails closed**. A demand is covered only when *all* of the
following hold, and any doubt yields ``covered = False`` with an explanatory
note (never a guessed conversion):

* a quota exists whose metric matches the demand's metric,
* the demand's period is compatible with that quota's reset period,
* both the demand amount+unit and the quota amount+unit normalize into the same
  dimension (via :mod:`app.read_api.normalize`'s exact-Decimal path), and
* ``headroom = quota_canonical - demand_canonical >= 0``.

The module is pure and import-light (stdlib + the normalize helper): no ORM, no
FastAPI, no LLM, no network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.read_api.normalize import comparable_decimal, normalize_amount_decimal


class _DemandLike(Protocol):
    metric: str
    amount: Decimal
    unit: str
    period: str | None


class _QuotaLike(Protocol):
    metric: str
    amount: object
    unit: str | None
    reset_period: str | None


#: Conservative period aliases -> a canonical spelling. Anything not listed is
#: compared by its literal (lowercased/trimmed) spelling; it is never guessed
#: into another period.
_PERIOD_ALIASES: dict[str, str] = {
    "month": "month",
    "monthly": "month",
    "day": "day",
    "daily": "day",
    "hour": "hour",
    "hourly": "hour",
    "minute": "minute",
    "second": "second",
    "week": "week",
    "weekly": "week",
    "year": "year",
    "yearly": "year",
    "annual": "year",
    "annually": "year",
}


def metric_key(raw: str | None) -> str:
    """Normalise a metric name for matching: lowercase, trim, collapse separators.

    Spaces, hyphens, and underscores are treated as equivalent word separators
    and collapsed to a single space so ``"egress bandwidth"``,
    ``"egress-bandwidth"``, and ``"egress_bandwidth"`` all match. Returns ``""``
    for a blank/absent metric (which never matches a real quota).
    """

    if not raw:
        return ""
    cleaned = raw.strip().lower()
    for sep in ("-", "_"):
        cleaned = cleaned.replace(sep, " ")
    return " ".join(cleaned.split())


def period_key(raw: str | None) -> str | None:
    """Normalise a period to a canonical spelling, or ``None`` when absent."""

    if raw is None:
        return None
    cleaned = raw.strip().lower()
    if not cleaned:
        return None
    return _PERIOD_ALIASES.get(cleaned, cleaned)


def periods_compatible(demand_period: str | None, quota_period: str | None) -> bool:
    """True when a demand's period is compatible with a quota's reset period.

    Compatible iff the demand does not constrain the period (``None``) or both
    periods normalise to the same canonical spelling. A demand that names a
    period the quota does not match (including a quota with no period) is **not**
    compatible -- fail closed rather than assume the rate lines up.
    """

    d = period_key(demand_period)
    if d is None:
        return True
    return d == period_key(quota_period)


@dataclass(frozen=True)
class DemandFit:
    """The exact-Decimal outcome of testing one demand against an offer's quotas."""

    metric: str
    covered: bool
    demand_amount: Decimal
    demand_unit: str
    demand_period: str | None
    matched_metric: str | None = None
    quota_original_amount: Decimal | None = None
    quota_original_unit: str | None = None
    canonical_unit: str | None = None
    demand_canonical: Decimal | None = None
    quota_canonical: Decimal | None = None
    headroom: Decimal | None = None
    headroom_ratio: Decimal | None = None
    reason: str = ""

    @property
    def is_boundary(self) -> bool:
        """True when the demand fits with exactly zero headroom."""

        return self.covered and self.headroom == Decimal(0)


@dataclass(frozen=True)
class OfferFit:
    """The aggregate fit of an offer against every demand of one requirement."""

    fits: bool
    demand_fits: tuple[DemandFit, ...]
    min_headroom_ratio: Decimal | None
    notes: tuple[str, ...]

    @property
    def blocking(self) -> tuple[DemandFit, ...]:
        """The demands this offer failed to cover (empty when it fits)."""

        return tuple(df for df in self.demand_fits if not df.covered)


def _candidate_quotas(demand_metric: str, quotas: Sequence[_QuotaLike]) -> list[_QuotaLike]:
    key = metric_key(demand_metric)
    if not key:
        return []
    return [q for q in quotas if metric_key(q.metric) == key]


def evaluate_demand(demand: _DemandLike, quotas: Sequence[_QuotaLike]) -> DemandFit:
    """Test one ``demand`` against an offer's ``quotas`` with exact Decimal math.

    Returns a :class:`DemandFit`. ``covered`` is ``True`` only when a
    metric-matched, period-compatible quota normalises into the same dimension
    as the demand and has ``headroom >= 0`` at that dimension. Every failing
    path sets ``covered = False`` with a human-readable ``reason`` -- never a
    guessed conversion.
    """

    demand_amount = demand.amount
    base = {
        "metric": demand.metric,
        "demand_amount": demand_amount,
        "demand_unit": demand.unit,
        "demand_period": demand.period,
    }

    matches = _candidate_quotas(demand.metric, quotas)
    if not matches:
        return DemandFit(
            covered=False,
            reason=(f"No quota measures metric '{demand.metric}'; cannot guarantee coverage."),
            **base,
        )

    demand_norm = normalize_amount_decimal(demand_amount, demand.unit)

    best: DemandFit | None = None
    fail_reason = ""
    for quota in matches:
        if not periods_compatible(demand.period, quota.reset_period):
            fail_reason = (
                f"Quota for '{quota.metric}' resets per "
                f"{quota.reset_period or 'an unspecified period'}, which is not "
                f"compatible with the demanded period '{demand.period}'."
            )
            continue

        quota_norm = normalize_amount_decimal(quota.amount, quota.unit)
        if not demand_norm.normalized:
            fail_reason = (
                f"Demanded unit '{demand.unit}' cannot be confidently normalized; "
                "cannot guarantee coverage."
            )
            continue
        if not quota_norm.normalized:
            fail_reason = (
                f"Quota unit '{quota.unit}' cannot be confidently normalized; "
                "cannot guarantee coverage."
            )
            continue
        if not comparable_decimal(demand_norm, quota_norm):
            fail_reason = (
                f"Demanded unit '{demand.unit}' and quota unit '{quota.unit}' are "
                "not the same kind of measurement; cannot compare."
            )
            continue

        assert demand_norm.canonical_amount is not None
        assert quota_norm.canonical_amount is not None
        headroom = quota_norm.canonical_amount - demand_norm.canonical_amount
        covered = headroom >= Decimal(0)
        ratio: Decimal | None = None
        if demand_norm.canonical_amount > Decimal(0):
            ratio = headroom / demand_norm.canonical_amount
        candidate = DemandFit(
            covered=covered,
            matched_metric=quota.metric,
            quota_original_amount=quota_norm.original_amount,
            quota_original_unit=quota.unit,
            canonical_unit=demand_norm.canonical_unit,
            demand_canonical=demand_norm.canonical_amount,
            quota_canonical=quota_norm.canonical_amount,
            headroom=headroom,
            headroom_ratio=ratio,
            reason=(
                (
                    f"Quota provides {quota_norm.original_amount} {quota.unit} "
                    f"(headroom {headroom} {demand_norm.canonical_unit}) for demand of "
                    f"{demand_amount} {demand.unit}."
                )
                if covered
                else (
                    f"Quota provides only {quota_norm.original_amount} {quota.unit}; "
                    f"short by {-headroom} {demand_norm.canonical_unit} for the demanded "
                    f"{demand_amount} {demand.unit}."
                )
            ),
            **base,
        )
        # Prefer a covering quota; among covering quotas keep the largest
        # headroom for a stable, safest choice.
        if best is None:
            best = candidate
        elif candidate.covered and not best.covered:
            best = candidate
        elif (
            candidate.covered
            and best.covered
            and candidate.headroom is not None
            and best.headroom is not None
            and candidate.headroom > best.headroom
        ):
            best = candidate

    if best is not None:
        return best

    return DemandFit(
        covered=False,
        reason=fail_reason or "Metric matched but no quota could be compared; cannot guarantee.",
        **base,
    )


def evaluate_offer(demands: Sequence[_DemandLike], quotas: Sequence[_QuotaLike]) -> OfferFit:
    """Aggregate :func:`evaluate_demand` over every demand of a requirement.

    The offer *fits* only when every demand is covered. ``min_headroom_ratio`` is
    the tightest per-demand headroom ratio across the covered demands (used for a
    deterministic "most comfortable margin" ordering); it is ``None`` whenever
    the offer does not fit, so a non-fitting offer never sorts ahead of a fit.
    """

    fits_list = [evaluate_demand(demand, quotas) for demand in demands]
    all_covered = all(df.covered for df in fits_list)
    notes = tuple(df.reason for df in fits_list if not df.covered)

    min_ratio: Decimal | None = None
    if all_covered:
        ratios = [df.headroom_ratio for df in fits_list if df.headroom_ratio is not None]
        min_ratio = min(ratios) if ratios else Decimal(0)

    return OfferFit(
        fits=all_covered,
        demand_fits=tuple(fits_list),
        min_headroom_ratio=min_ratio,
        notes=notes,
    )


def reduced_demand_amount(demand: _DemandLike, quotas: Sequence[_QuotaLike]) -> Decimal | None:
    """The largest demand amount (in the demand's own unit) that WOULD fit.

    Used by the impossible-workload reduction step: given the best metric-matched
    quota, returns the exact Decimal amount, expressed in the demand's original
    unit, that exactly exhausts the quota (headroom 0). Returns ``None`` when the
    demand cannot be normalised/compared at all (fail closed -- no reduction is
    proposed rather than a guessed one).
    """

    demand_norm = normalize_amount_decimal(demand.amount, demand.unit)
    if not demand_norm.normalized or demand_norm.canonical_amount is None:
        return None

    best_quota_canonical: Decimal | None = None
    for quota in _candidate_quotas(demand.metric, quotas):
        if not periods_compatible(demand.period, quota.reset_period):
            continue
        quota_norm = normalize_amount_decimal(quota.amount, quota.unit)
        if not quota_norm.normalized or not comparable_decimal(demand_norm, quota_norm):
            continue
        assert quota_norm.canonical_amount is not None
        if best_quota_canonical is None or quota_norm.canonical_amount > best_quota_canonical:
            best_quota_canonical = quota_norm.canonical_amount

    if best_quota_canonical is None:
        return None
    if demand_norm.canonical_amount == Decimal(0):
        return None
    # Convert the canonical quota ceiling back into the demand's own unit exactly:
    # demand_original / demand_canonical is the exact unit-per-canonical factor.
    factor = demand.amount / demand_norm.canonical_amount
    return best_quota_canonical * factor


__all__: Sequence[str] = (
    "DemandFit",
    "OfferFit",
    "metric_key",
    "period_key",
    "periods_compatible",
    "evaluate_demand",
    "evaluate_offer",
    "reduced_demand_amount",
)
