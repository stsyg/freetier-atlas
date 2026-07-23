"""Deterministic, gated publication pipeline (F005 slice 2).

The first sanctioned config->offer publication path. F004 produced only
pre-publication candidates + evidence; this package turns an *official*, gated
candidate into a published, Z0-classified offer:

    revalidate (numbers) -> confidence (signals) -> gate (publish/review/withhold)
    -> publisher (append immutable OfferVersion + Quota + link Evidence +
       classify + published ChangeEvent)  |  review -> pending ReviewItem

Everything here is deterministic and standard-library only; there is no LLM and
no network path to publication. ``OfferVersion`` is append-only (never updated or
deleted) and only official, evidence-backed data can ever be published.
"""

from __future__ import annotations

from collections.abc import Sequence

from .confidence import (
    ConfidenceSignals,
    completeness,
    compute_confidence,
    freshness_ratio,
    signals_as_material_fact,
)
from .gate import (
    PUBLISH,
    REVIEW,
    WITHHOLD,
    GateConditions,
    GateDecision,
    evaluate_gate,
)
from .publisher import (
    PublishOutcome,
    PublishResult,
    publish_candidate,
    publish_scan,
)
from .revalidate import (
    ParsedQuantity,
    RevalidatedQuota,
    RevalidationResult,
    parse_quantity,
    revalidate_quotas,
)

__all__: Sequence[str] = (
    # revalidate
    "ParsedQuantity",
    "RevalidatedQuota",
    "RevalidationResult",
    "parse_quantity",
    "revalidate_quotas",
    # confidence
    "ConfidenceSignals",
    "completeness",
    "compute_confidence",
    "freshness_ratio",
    "signals_as_material_fact",
    # gate
    "PUBLISH",
    "REVIEW",
    "WITHHOLD",
    "GateConditions",
    "GateDecision",
    "evaluate_gate",
    # publisher
    "PublishOutcome",
    "PublishResult",
    "publish_candidate",
    "publish_scan",
)
