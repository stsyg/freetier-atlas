"""Closed vocabularies for the source-ingestion pipeline.

The single source of truth for the verification-state lifecycle that a candidate
fact moves through. It mirrors ``docs/ARCHITECTURE.md`` ("Verification states")
exactly so the ingestion contract and any future persistence agree.

It is also the single source of truth for the **registered assertion-field
vocabulary** (:data:`ASSERTION_FIELD_RULES`) -- which fact fields a trusted
profile may pin an ``HtmlTextAssertion`` to, and what kind of value each may
carry. That lives here rather than in the HTML adapter because it is a property
of the candidate-fact contract, not of one document format, and here it can be
shared without ``app.ingest`` importing ``app.publish`` (which imports
``app.ingest`` and would form a cycle).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.models.vocab import EXHAUSTION_BEHAVIOURS, OFFER_TYPES

# docs/ARCHITECTURE.md -> Verification states.
#
# A source document is ``detected`` when first discovered, ``extracting`` while
# its material facts are parsed, ``candidate`` once parsed but not yet verified,
# then one of the terminal/steady states below. Adapters in this epic only ever
# produce candidate facts; nothing here can mark a fact ``verified``.
VERIFICATION_STATES: tuple[str, ...] = (
    "detected",
    "extracting",
    "candidate",
    "verified",
    "verified_with_caveats",
    "conflict",
    "stale",
    "withdrawn",
    "rejected",
)

# The states an adapter is permitted to assign to freshly extracted facts. A
# fact can never be born ``verified``; verification is a later, separate step.
ADAPTER_ASSIGNABLE_STATES: frozenset[str] = frozenset(
    {"detected", "extracting", "candidate", "rejected"}
)


def is_verification_state(value: str) -> bool:
    """True if ``value`` is a known verification state."""

    return value in VERIFICATION_STATES


# --------------------------------------------------------------------------- #
# THE REGISTERED ASSERTION-FIELD VOCABULARY                                    #
# --------------------------------------------------------------------------- #
# Until this existed, ``HtmlTextAssertion`` accepted ANY field name and the
# closed-vocabulary check was a dict lookup that fell through silently for a name
# it did not know. A mistyped material condition (``exhaustion_behavior``,
# ``requires_cards``) therefore escaped validation entirely, and then -- because
# ``app.publish.revalidate`` treats every fact key it does not reserve as a quota
# metric -- was PUBLISHED as a quota row while the condition its name resembles
# stayed absent. Two failures at once, neither of them loud.
#
# A candidate fact field is one of exactly two things, and the split is what
# makes the vocabulary enforceable without inventing a closed list of every
# quota name a provider might publish:
#
# * a RESERVED field -- read BY NAME by the publication, classification and
#   reconciliation layers. Bounded, enumerated here, and each one declares what
#   kind of value it may carry.
# * a QUOTA METRIC -- open by construction, because the metric names come from
#   the provider's own document (measured: 89 distinct ones across the six
#   merged providers). Admitted by SHAPE and value type, not by enumeration.
#
# Anything that is neither is unregistered and is refused outright.


#: A canonical value DERIVED from the pinned block rather than quoted out of it
#: (a canonical service name, an eligibility label, a documentation URL). The
#: block is still the evidence; the value is a mapping, exactly as ``offer_type``
#: is a mapping onto a closed vocabulary.
MAPPED_TEXT = "mapped_text"

#: Free text that REACHES THE UI and must therefore reproduce the asserted source
#: wording verbatim rather than paraphrase it (docs/PROVIDER_ADAPTERS.md).
QUOTED_TEXT = "quoted_text"

#: A value drawn from a closed vocabulary declared alongside the field.
CLOSED_VALUE = "closed_value"

#: A strict boolean gate: ``0``/``1``/``"true"`` are not booleans.
BOOLEAN_GATE = "boolean_gate"


@dataclass(frozen=True)
class AssertionFieldRule:
    """How one registered reserved fact field may be asserted."""

    value_kind: str
    allowed: tuple[Any, ...] = ()

    def describe(self) -> str:
        if self.value_kind == CLOSED_VALUE:
            return f"one of {list(self.allowed)}"
        if self.value_kind == BOOLEAN_GATE:
            # Worded as "one of [False, True]" deliberately: a boolean gate is a
            # closed vocabulary of two values, and the pre-existing profiles and
            # tests already speak of it that way.
            return "one of [False, True] (a strict bool)"
        if self.value_kind == QUOTED_TEXT:
            return "free text quoted verbatim from the pinned block"
        return "a non-empty string mapped from the pinned block"


#: Reserved fact fields a profile MAY pin, and the value rule for each. The keys
#: plus :data:`NON_ASSERTABLE_FACT_FIELDS` must reproduce
#: ``app.publish.revalidate.NON_QUOTA_FIELDS`` exactly; a drift-guard test
#: asserts it, so the two cannot diverge silently.
ASSERTION_FIELD_RULES: Mapping[str, AssertionFieldRule] = {
    "service": AssertionFieldRule(MAPPED_TEXT),
    "offer_type": AssertionFieldRule(CLOSED_VALUE, OFFER_TYPES),
    "eligibility": AssertionFieldRule(MAPPED_TEXT),
    "commercial_use_allowed": AssertionFieldRule(BOOLEAN_GATE),
    "personal_use_allowed": AssertionFieldRule(BOOLEAN_GATE),
    "requires_card": AssertionFieldRule(BOOLEAN_GATE),
    "has_paid_dependencies": AssertionFieldRule(BOOLEAN_GATE),
    "exhaustion_behaviour": AssertionFieldRule(CLOSED_VALUE, EXHAUSTION_BEHAVIOURS),
    "display_name": AssertionFieldRule(QUOTED_TEXT),
    "service_description": AssertionFieldRule(QUOTED_TEXT),
    "documentation_url": AssertionFieldRule(MAPPED_TEXT),
    "notes": AssertionFieldRule(QUOTED_TEXT),
}

#: Reserved names that are the adapter's and publisher's own CONTROL PLANE, not
#: extractable facts. ``error``/``detail`` are how ``HtmlDocAdapter._rejected``
#: marks a rejected candidate and how ``validate()`` recognises one, so a profile
#: able to pin them could forge or mask a rejection. ``provider`` is identity and
#: ``quotas`` is the structured list the publisher builds itself.
NON_ASSERTABLE_FACT_FIELDS: frozenset[str] = frozenset({"error", "detail", "provider", "quotas"})

#: Every fact field the publication layer reads by name.
RESERVED_FACT_FIELDS: frozenset[str] = frozenset(ASSERTION_FIELD_RULES) | NON_ASSERTABLE_FACT_FIELDS

#: The shape a quota-metric field name must take. Quota metrics are open by
#: design, but they are re-read by ``revalidate_quotas`` (which recovers a reset
#: period from a ``*_per_<period>`` suffix) and surfaced as metric names, so a
#: name carrying spaces, capitals or punctuation is a mistake rather than a
#: metric.
#:
#: MEASURED across every registry in this repository (HTML, structured/JSON and
#: MCP), with the test-support profiles imported as a pytest run imports them:
#: 206 distinct non-reserved field names, all matching. Scoped to the six merged
#: providers alone that is 89 quota-metric assertion fields and 102 matrix
#: fields and **zero** column fields -- every one of the 23 column fields belongs
#: to Cloudflare and the two generic profiles, not to the six. The distinction
#: matters: a count that is true repository-wide and false when attributed to a
#: subset is exactly the defect class this module exists to make impossible.
#: ``test_no_registered_field_name_in_this_repository_is_confusable`` re-derives
#: the property rather than the counts, so it cannot rot as providers are added.
QUOTA_METRIC_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: How close a quota-metric name may come to a RESERVED name before it is
#: refused as a probable typo.
#:
#: This is the second half of rule 1, and it exists because shape alone is not
#: enough: ``exhaustion_behavior`` (US spelling), ``offer_typ`` and
#: ``requires_cards`` are all perfectly well-formed metric names, so a
#: shape-only check would accept them in silence -- and then the publisher would
#: turn the mistyped material condition into a quota row while the condition it
#: resembles stayed absent. That is precisely the unsupported-$0-claim failure
#: this product cannot absorb.
#:
#: MEASURED before choosing the threshold, over all 206 distinct non-reserved
#: field names registered across every profile (assertions, matrix rows, columns
#: and structured-JSON fields): ZERO are within edit distance 2 of any reserved
#: name, and zero are within distance 1. The six obvious typos are all at
#: distance 1. So distance 1 catches every one of them with a measured margin of
#: 2 against real work, rather than being a guess about what a typo looks like.
_MAX_RESERVED_CONFUSION_DISTANCE = 1


def _edit_distance(left: str, right: str, *, limit: int) -> int:
    """Levenshtein distance, short-circuited once it cannot fall within ``limit``."""

    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def confusable_reserved_field(field: str) -> str | None:
    """Return the reserved field ``field`` is a probable typo of, if any."""

    if field in RESERVED_FACT_FIELDS:
        return None
    for reserved in sorted(RESERVED_FACT_FIELDS):
        if _edit_distance(field, reserved, limit=_MAX_RESERVED_CONFUSION_DISTANCE) <= (
            _MAX_RESERVED_CONFUSION_DISTANCE
        ):
            return reserved
    return None


def assertion_vocabulary_summary() -> str:
    """One-line description of the registered vocabulary, for error messages."""

    reserved = ", ".join(sorted(ASSERTION_FIELD_RULES))
    forbidden = ", ".join(sorted(NON_ASSERTABLE_FACT_FIELDS))
    return (
        f"registered reserved fields: {reserved}; "
        f"never assertable (adapter/publisher control plane): {forbidden}; "
        f"any other field must be a quota metric matching {QUOTA_METRIC_NAME.pattern} "
        "with a non-empty string value, and must not be a near-miss of a reserved name"
    )


def assertion_field_problem(field: str, value: Any) -> str | None:
    """Return why ``field``/``value`` is not a registered assertion, else ``None``.

    Fail-closed and total: every field name lands in exactly one branch, so there
    is no silent fall-through for a name the vocabulary does not know.
    """

    if field in NON_ASSERTABLE_FACT_FIELDS:
        return (
            f"Assertion field {field!r} is part of the adapter/publisher control plane "
            "and can never be asserted by a profile"
        )

    rule = ASSERTION_FIELD_RULES.get(field)
    if rule is None:
        if not QUOTA_METRIC_NAME.fullmatch(field):
            return (
                f"Assertion field {field!r} is not a registered reserved field and is not a "
                f"valid quota-metric name (expected {QUOTA_METRIC_NAME.pattern})"
            )
        confusable = confusable_reserved_field(field)
        if confusable is not None:
            return (
                f"Assertion field {field!r} is not registered and is one character away from "
                f"the reserved field {confusable!r}. A near-miss is refused rather than "
                "accepted as a quota metric, because a mistyped material condition is "
                "republished as a quota row while the condition it resembles stays absent. "
                f"Use {confusable!r} if that is what was meant, or choose a metric name that "
                "is not confusable with a reserved one"
            )
        if type(value) is not str or not value.strip():
            return (
                f"Assertion field {field!r} is a quota metric, so its value must be a "
                f"non-empty string; got {value!r}"
            )
        return None

    if rule.value_kind == BOOLEAN_GATE:
        if type(value) is not bool:
            return f"Assertion field {field!r} requires {rule.describe()}; got {value!r}"
        return None

    if rule.value_kind == CLOSED_VALUE:
        if not any(type(value) is type(allowed) and value == allowed for allowed in rule.allowed):
            return f"Assertion field {field!r} requires {rule.describe()}; got {value!r}"
        return None

    if type(value) is not str or not value.strip():
        return f"Assertion field {field!r} requires {rule.describe()}; got {value!r}"
    return None


__all__ = (
    "VERIFICATION_STATES",
    "ADAPTER_ASSIGNABLE_STATES",
    "is_verification_state",
    "MAPPED_TEXT",
    "QUOTED_TEXT",
    "CLOSED_VALUE",
    "BOOLEAN_GATE",
    "AssertionFieldRule",
    "ASSERTION_FIELD_RULES",
    "NON_ASSERTABLE_FACT_FIELDS",
    "RESERVED_FACT_FIELDS",
    "QUOTA_METRIC_NAME",
    "confusable_reserved_field",
    "assertion_vocabulary_summary",
    "assertion_field_problem",
)
