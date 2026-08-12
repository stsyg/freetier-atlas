"""Deterministic numeric re-validation of extracted quota facts (F005 slice 2).

Before an official candidate may be published, the material *numbers* it claims
are re-derived here from the extracted evidence text. This is the first gate
condition (docs/ARCHITECTURE.md "Publication gate" -> "deterministic parsing of
material numbers"): the same fact text always yields the same parsed quota, and
a value that cannot be parsed to a number yields ``None`` (UNKNOWN) rather than a
fabricated figure ("unknown is better than guessed").

The parser is pure and standard-library only. It reads the per-limit text fields
an HTML profile captured verbatim (e.g. ``"100,000/day"``, ``"10 ms"``,
``"1 build at a time"``) and re-derives:

* ``amount`` -- the numeric value with thousands separators stripped and an
  optional unambiguous uppercase decimal count suffix (``K``, ``M``, ``B``)
  applied, kept as an exact :class:`decimal.Decimal` (``None`` when no supported
  number is present);
* ``reset_period`` -- the divisor after a ``/`` (``"100,000/day"`` -> ``"day"``)
  or, failing that, a ``*_per_<period>`` field-name suffix
  (``builds_per_month`` -> ``"month"``);
* ``unit`` -- the trailing unit token when the value is not a rate
  (``"128 MB"`` -> ``"MB"``).

Nothing here touches the database or the network; it operates purely on the
already-persisted candidate facts, so re-running it against identical facts
reproduces an identical, order-stable :class:`RevalidationResult`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

#: Candidate-fact keys that are *not* quota metrics. Everything else on a
#: candidate is treated as a per-limit quota field to re-validate.
NON_QUOTA_FIELDS: frozenset[str] = frozenset(
    {
        "service",
        "offer_type",
        "eligibility",
        "commercial_use_allowed",
        "personal_use_allowed",
        "requires_card",
        "has_paid_dependencies",
        "exhaustion_behaviour",
        "quotas",
        "display_name",
        "service_description",
        "documentation_url",
        "notes",
        "provider",
        "error",
        "detail",
    }
)

_OPTIONAL_BOOLEAN_FIELDS: tuple[str, ...] = (
    "commercial_use_allowed",
    "personal_use_allowed",
)

# Numeric token: digits with optional thousands separators / decimal fraction
# and an optional directly-adjacent uppercase decimal SI count suffix.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?(?P<magnitude>[KMB])?")
# A separated single-letter count suffix is ambiguous with a unit and is not
# part of the numeric token, so fail closed rather than treating it as a unit.
# Unambiguous multi-letter units remain ordinary units; ambiguous magnitude
# continuations and IEC-looking binary forms are rejected below.
_SEPARATED_MAGNITUDE = re.compile(
    r"^[\W_]*[KMB](?=$|[\W_])",
    re.IGNORECASE,
)
_GROUPED_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_ALPHA_TOKEN = re.compile(r"[^\W\d_]+")
_BINARY_UNIT = re.compile(r"^[A-Za-z]iB$")
_MAGNITUDE_CONTINUATION_UNITS = frozenset({"Kbps", "Mbps", "MBps"})
_MAGNITUDE_MULTIPLIERS: Mapping[str, Decimal] = {
    "K": Decimal("1000"),
    "M": Decimal("1000000"),
    "B": Decimal("1000000000"),
}
# A "<field>_per_<period>" suffix used to recover a reset period from the metric
# name when the value text itself does not carry one.
_PER_SUFFIX = re.compile(r"_per_([a-z]+)$")


def _is_supported_compact_unit(token: str, *, consumed_magnitude: bool) -> bool:
    """Whether an adjacent alphabetic token is unambiguously an ordinary unit."""

    if consumed_magnitude:
        return token in _MAGNITUDE_CONTINUATION_UNITS
    if len(token) < 2:
        return False
    if _BINARY_UNIT.fullmatch(token):
        return False
    return not all(ch in "KMB" for ch in token)


def _is_sign_like(character: str) -> bool:
    """Whether one code point semantically represents a numeric sign."""

    normalized = unicodedata.normalize("NFKC", character)
    for candidate in character + normalized:
        category = unicodedata.category(candidate)
        name = unicodedata.name(candidate, "")
        if category == "Pd" or any(
            marker in name for marker in ("PLUS", "MINUS", "HYPHEN", "DASH")
        ):
            return True
    return False


def _trailing_prefix_has_sign(prefix: str) -> bool:
    """Scan the trailing non-word prefix token for a semantic sign."""

    for character in reversed(prefix):
        if _is_sign_like(character):
            return True
        if character.isalnum():
            return False
    return False


@dataclass(frozen=True)
class ParsedQuantity:
    """A single quota text value re-derived into structured numeric parts."""

    raw: str
    amount: Decimal | None
    unit: str | None
    reset_period: str | None

    @property
    def has_number(self) -> bool:
        return self.amount is not None


@dataclass(frozen=True)
class RevalidatedQuota:
    """A re-validated quota ready to be persisted as a :class:`Quota` row."""

    metric: str
    amount: Decimal | None
    unit: str | None
    reset_period: str | None
    exhaustion_behaviour: str
    raw: str

    def as_material_fact(self) -> dict[str, Any]:
        """Order-stable, JSON-safe representation for hashing / material_facts.

        The amount is rendered as a string so an exact ``Decimal`` round-trips
        through JSONB deterministically (floats would not).
        """

        return {
            "metric": self.metric,
            "amount": None if self.amount is None else format(self.amount, "f"),
            "unit": self.unit,
            "reset_period": self.reset_period,
            "exhaustion_behaviour": self.exhaustion_behaviour,
        }


@dataclass(frozen=True)
class RevalidationResult:
    """The outcome of re-validating one candidate's quota numbers."""

    quotas: tuple[RevalidatedQuota, ...]
    unparsed_fields: tuple[str, ...]

    @property
    def parsed_count(self) -> int:
        return sum(1 for q in self.quotas if q.amount is not None)

    @property
    def deterministic(self) -> bool:
        """True when every numeric-looking field parsed to a real number.

        A field that contains a digit but could not be reduced to a number is a
        determinism failure (it would otherwise be a guessed/ambiguous figure);
        a field with no digits at all is simply not a numeric claim and does not
        count against determinism. At least one real number must be present.
        """

        return not self.unparsed_fields and self.parsed_count > 0


def invalid_optional_offer_fields(facts: Mapping[str, Any]) -> tuple[str, ...]:
    """Return explicit optional offer fields whose runtime types are invalid.

    Exact runtime-type checks are intentional: values such as ``0``, ``"false"``,
    or objects implementing truthiness/equality/string protocols must not be
    coerced into structured publication facts.
    """

    invalid: list[str] = []
    if "eligibility" in facts:
        eligibility = facts["eligibility"]
        if eligibility is not None and (type(eligibility) is not str or not eligibility.strip()):
            invalid.append("eligibility")

    for field_name in _OPTIONAL_BOOLEAN_FIELDS:
        if field_name in facts:
            value = facts[field_name]
            if value is not None and type(value) is not bool:
                invalid.append(field_name)
    return tuple(invalid)


def parse_quantity(raw: str | None, *, metric: str | None = None) -> ParsedQuantity:
    """Re-derive one quota text value into a :class:`ParsedQuantity`.

    Deterministic and side-effect free. ``metric`` is used only to recover a
    reset period from a ``*_per_<period>`` field name when the value text does
    not carry one; it never invents a number.
    """

    text = "" if raw is None else str(raw).strip()
    if not text:
        return ParsedQuantity(raw=text, amount=None, unit=None, reset_period=None)

    matches = list(_NUMBER.finditer(text))
    if len(matches) != 1:
        return ParsedQuantity(raw=text, amount=None, unit=None, reset_period=None)

    match = matches[0]
    amount: Decimal | None = None
    rest = text
    prefix = text[: match.start()]
    before = text[match.start() - 1] if match.start() else ""
    after_token = text[match.end() :]
    magnitude = match.group("magnitude")
    alpha_match = _ALPHA_TOKEN.match(after_token)
    compact_unit: str | None = None
    if alpha_match is not None:
        consumed_magnitude = magnitude is not None
        compact_unit = (magnitude or "") + alpha_match.group(0)
        if not _is_supported_compact_unit(
            compact_unit,
            consumed_magnitude=consumed_magnitude,
        ):
            return ParsedQuantity(raw=text, amount=None, unit=None, reset_period=None)
        after_token = after_token[alpha_match.end() :]
        magnitude = None

    # Searching rather than anchoring deliberately preserves one-token qualifiers
    # ("First 10K", "Up to 1M"). A symbol or punctuation directly before the
    # number, adjacent letters, invalid suffix continuations, and separated K/M/B
    # are outside the supported grammar and must not degrade to leading digits.
    if (
        (before and unicodedata.category(before)[0] in {"P", "S"})
        or _trailing_prefix_has_sign(prefix)
        or (before and before.isalnum())
        or (
            magnitude is not None
            and after_token
            and not (after_token[0].isspace() or after_token[0] == "/")
        )
        or _SEPARATED_MAGNITUDE.match(after_token)
    ):
        return ParsedQuantity(raw=text, amount=None, unit=None, reset_period=None)
    try:
        numeric_text = match.group(0) if match.group("magnitude") is None else match.group(0)[:-1]
        if "," in numeric_text and _GROUPED_NUMBER.fullmatch(numeric_text) is None:
            return ParsedQuantity(raw=text, amount=None, unit=None, reset_period=None)
        amount = Decimal(numeric_text.replace(",", ""))
        if magnitude is not None:
            amount *= _MAGNITUDE_MULTIPLIERS[magnitude]
    except InvalidOperation:  # pragma: no cover - regex guarantees a valid number
        amount = None
    rest = ((compact_unit or "") + after_token).strip()

    unit: str | None = None
    reset_period: str | None = None
    if "/" in text:
        after = text.split("/", 1)[1].strip()
        reset_period = after or None
    elif rest.lower().startswith("per "):
        reset_period = rest[4:].strip() or None
    elif rest:
        unit = rest

    if reset_period is None and metric:
        suffix = _PER_SUFFIX.search(metric)
        if suffix is not None:
            reset_period = suffix.group(1)

    return ParsedQuantity(raw=text, amount=amount, unit=unit, reset_period=reset_period)


def revalidate_quotas(facts: Mapping[str, Any], *, exhaustion_behaviour: str) -> RevalidationResult:
    """Re-validate every quota field on ``facts`` into structured quotas.

    Iterates the candidate's non-identity fields in sorted (deterministic)
    order, re-deriving each numeric quota. A field carrying a digit that cannot
    be parsed is reported in ``unparsed_fields`` (never silently guessed). The
    supplied ``exhaustion_behaviour`` is attached to every derived quota (it is
    the offer's evidence-backed exhaustion behaviour).
    """

    quotas: list[RevalidatedQuota] = []
    unparsed: list[str] = []
    for metric in sorted(facts):
        if metric in NON_QUOTA_FIELDS:
            continue
        value = facts[metric]
        if value is None:
            continue
        raw = str(value).strip()
        if not raw:
            continue
        parsed = parse_quantity(raw, metric=metric)
        if parsed.amount is None and any(ch.isdigit() for ch in raw):
            unparsed.append(metric)
        quotas.append(
            RevalidatedQuota(
                metric=metric,
                amount=parsed.amount,
                unit=parsed.unit,
                reset_period=parsed.reset_period,
                exhaustion_behaviour=exhaustion_behaviour,
                raw=raw,
            )
        )
    return RevalidationResult(quotas=tuple(quotas), unparsed_fields=tuple(sorted(unparsed)))


__all__: Sequence[str] = (
    "NON_QUOTA_FIELDS",
    "ParsedQuantity",
    "RevalidatedQuota",
    "RevalidationResult",
    "invalid_optional_offer_fields",
    "parse_quantity",
    "revalidate_quotas",
)
