"""The Z0 classification engine package.

Public surface:

* :func:`classify` -- pure, deterministic classification of :class:`OfferFacts`.
* :func:`classify_offer` -- read-only convenience over persisted ORM ``Offer`` rows.
* :class:`ClassificationResult`, :class:`OfferFacts` -- the input/output types.
* The five zero-cost class label constants.
"""

from __future__ import annotations

from .engine import (
    UNKNOWN,
    Z0_TRUE_FREE,
    Z1_BILLING_EXPOSURE,
    Z2_TEMPORARY_OR_CONDITIONAL,
    Z3_SELF_HOSTED_BUILDING_BLOCK,
    ClassificationResult,
    OfferFacts,
    classify,
    known_zero_cost_classes,
    summarise,
)
from .orm import classify_offer, offer_facts_from_orm

__all__ = (
    "OfferFacts",
    "ClassificationResult",
    "classify",
    "classify_offer",
    "offer_facts_from_orm",
    "known_zero_cost_classes",
    "summarise",
    "Z0_TRUE_FREE",
    "Z1_BILLING_EXPOSURE",
    "Z2_TEMPORARY_OR_CONDITIONAL",
    "Z3_SELF_HOSTED_BUILDING_BLOCK",
    "UNKNOWN",
)
