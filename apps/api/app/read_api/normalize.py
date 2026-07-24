"""Conservative, stdlib-only quota-unit normalization (F006 slice 1).

Owner decision Q7: quota-unit normalization for *compare* is conservative and
uses only the standard library. **Anything it cannot confidently normalize fails
closed** -- it surfaces an explicit "cannot normalize" result and never a guessed
conversion. This keeps the product's "unknown is better than guessed" rule intact
when two offers quote the same metric in different units.

Scope of what is normalized (deliberately small):

* **Data size** -> a canonical base of ``byte``. Decimal SI units
  (``B``/``KB``/``MB``/``GB``/``TB``/``PB``, powers of 1000) and binary IEC units
  (``KiB``/``MiB``/``GiB``/``TiB``/``PiB``, powers of 1024) are recognised. The
  decimal-vs-binary distinction follows the SI/IEC convention exactly as written
  (``GB`` = 1000^3, ``GiB`` = 1024^3); an ambiguous vendor unit is normalised by
  its literal spelling only, never re-interpreted.
* **Counts** -> a canonical base of ``count`` with a multiplier of exactly 1 for a
  small set of recognised countable units (requests, operations, invocations,
  builds, ...). This is a pass-through (no arithmetic conversion) so two offers
  quoting the same countable metric compare directly.

Everything else -- durations, currencies, bandwidth-per-time, blank/unknown units,
or any unit not in the tables below -- returns :data:`~NormalizedAmount` with
``normalized=False`` and a human-readable ``note``. The Slice 3 adviser is
expected to reuse this helper, so the contract is intentionally strict and pure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

#: Canonical base unit for the data-size dimension.
BYTE_UNIT = "byte"
#: Canonical base unit for the count dimension.
COUNT_UNIT = "count"

_DIMENSION_DATA = "data_size"
_DIMENSION_COUNT = "count"

#: Recognised data-size units -> their size in bytes. Decimal (1000^n) and binary
#: (1024^n) families are both listed explicitly; the lookup is case-insensitive
#: on the exact spelling, so ``GB`` and ``GiB`` never collapse into each other.
_DATA_SIZE_TO_BYTES: Mapping[str, int] = {
    "b": 1,
    "byte": 1,
    "bytes": 1,
    # decimal (SI) family, powers of 1000
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "pb": 1000**5,
    # binary (IEC) family, powers of 1024
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
    "pib": 1024**5,
}

#: Recognised countable units. These are pass-through (multiplier 1): they are
#: never scaled, only accepted so that same-metric offers compare directly.
_COUNT_UNITS: frozenset[str] = frozenset(
    {
        "count",
        "request",
        "requests",
        "operation",
        "operations",
        "op",
        "ops",
        "invocation",
        "invocations",
        "build",
        "builds",
        "call",
        "calls",
        "item",
        "items",
        "message",
        "messages",
        "record",
        "records",
        "row",
        "rows",
        "seat",
        "seats",
        "user",
        "users",
        "project",
        "projects",
        "domain",
        "domains",
    }
)


@dataclass(frozen=True)
class NormalizedAmount:
    """The result of normalizing a single ``(amount, unit)`` quota measurement.

    ``normalized`` is the fail-closed flag: when it is ``False`` the canonical
    fields are ``None`` and ``note`` explains why (an unknown amount, a blank
    unit, or a unit outside the recognised tables). Callers must treat a
    non-normalized value as *unknown*, never as zero or as directly comparable.
    """

    original_amount: float | None
    original_unit: str | None
    normalized: bool
    canonical_amount: float | None = None
    canonical_unit: str | None = None
    dimension: str | None = None
    note: str | None = None


def _clean_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    cleaned = unit.strip()
    return cleaned or None


def _coerce_amount(amount: object) -> float | None:
    if amount is None or isinstance(amount, bool):
        return None
    try:
        value = float(amount)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    return value


def normalize_amount(amount: object, unit: str | None) -> NormalizedAmount:
    """Normalize one ``(amount, unit)`` measurement, failing closed on the unknown.

    Returns a :class:`NormalizedAmount`. On success ``normalized`` is ``True`` and
    ``canonical_amount`` / ``canonical_unit`` / ``dimension`` are populated; on any
    doubt (missing amount, blank unit, unrecognised unit) ``normalized`` is
    ``False`` with a ``note`` and no canonical value -- never a guessed conversion.
    """

    original_amount = _coerce_amount(amount)
    original_unit = _clean_unit(unit)

    if original_amount is None:
        return NormalizedAmount(
            original_amount=None,
            original_unit=original_unit,
            normalized=False,
            note="unknown amount",
        )
    if original_unit is None:
        return NormalizedAmount(
            original_amount=original_amount,
            original_unit=None,
            normalized=False,
            note="missing unit; cannot normalize",
        )

    key = original_unit.lower()

    factor = _DATA_SIZE_TO_BYTES.get(key)
    if factor is not None:
        return NormalizedAmount(
            original_amount=original_amount,
            original_unit=original_unit,
            normalized=True,
            canonical_amount=original_amount * factor,
            canonical_unit=BYTE_UNIT,
            dimension=_DIMENSION_DATA,
        )

    if key in _COUNT_UNITS:
        return NormalizedAmount(
            original_amount=original_amount,
            original_unit=original_unit,
            normalized=True,
            canonical_amount=original_amount,
            canonical_unit=COUNT_UNIT,
            dimension=_DIMENSION_COUNT,
        )

    return NormalizedAmount(
        original_amount=original_amount,
        original_unit=original_unit,
        normalized=False,
        note=f"cannot normalize unit '{original_unit}'",
    )


def comparable(a: NormalizedAmount, b: NormalizedAmount) -> bool:
    """True only when both values normalized into the *same* dimension.

    Fails closed: if either value did not normalize, or they landed in different
    dimensions, they are not safely comparable.
    """

    return a.normalized and b.normalized and a.dimension is not None and a.dimension == b.dimension


# --------------------------------------------------------------------------- #
# Exact-Decimal path (F006 slice 3 adviser).                                  #
#                                                                             #
# The Slice 1 float path above stays byte-for-byte the same so the S1 compare #
# API/tests are untouched. The adviser, however, makes *$0-fit* decisions     #
# (headroom = quota_amount - demand), and a $0 guarantee must never turn on a #
# binary-float rounding artefact. This additive path reuses the very same     #
# unit tables but does all arithmetic in :class:`decimal.Decimal`, so an      #
# exact-boundary fit (headroom == 0) is decided exactly. It fails closed on   #
# the unknown in exactly the same way as the float path.                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NormalizedDecimal:
    """Exact-Decimal counterpart of :class:`NormalizedAmount`.

    ``normalized`` is the fail-closed flag: when it is ``False`` the canonical
    fields are ``None`` and ``note`` explains why. Callers must treat a
    non-normalized value as *unknown* -- never as zero or as comparable. All
    populated numeric fields are :class:`~decimal.Decimal` so no float ever
    enters a fit/headroom decision.
    """

    original_amount: Decimal | None
    original_unit: str | None
    normalized: bool
    canonical_amount: Decimal | None = None
    canonical_unit: str | None = None
    dimension: str | None = None
    note: str | None = None


def _coerce_amount_decimal(amount: object) -> Decimal | None:
    """Coerce a value to an exact :class:`Decimal`, or ``None`` on any doubt.

    ``bool`` is rejected (it is not a quantity). A ``float`` is stringified
    first so the Decimal captures the intended decimal value rather than the
    binary-float artefact (``Decimal(str(0.1)) == Decimal('0.1')``). Non-finite
    values (NaN / Infinity) fail closed.
    """

    if amount is None or isinstance(amount, bool):
        return None
    try:
        if isinstance(amount, Decimal):
            value = amount
        elif isinstance(amount, int):
            value = Decimal(amount)
        elif isinstance(amount, float):
            value = Decimal(str(amount))
        elif isinstance(amount, str):
            value = Decimal(amount.strip())
        else:
            return None
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite():
        return None
    return value


def normalize_amount_decimal(amount: object, unit: str | None) -> NormalizedDecimal:
    """Exact-Decimal normalization of one ``(amount, unit)`` measurement.

    Mirrors :func:`normalize_amount` (same recognised units, same fail-closed
    rules) but keeps every value as an exact :class:`~decimal.Decimal`. On
    success ``normalized`` is ``True`` with ``canonical_amount`` /
    ``canonical_unit`` / ``dimension`` populated; on any doubt (missing amount,
    blank unit, unrecognised unit) ``normalized`` is ``False`` with a ``note``
    and no canonical value -- never a guessed conversion.
    """

    original_amount = _coerce_amount_decimal(amount)
    original_unit = _clean_unit(unit)

    if original_amount is None:
        return NormalizedDecimal(
            original_amount=None,
            original_unit=original_unit,
            normalized=False,
            note="unknown amount",
        )
    if original_unit is None:
        return NormalizedDecimal(
            original_amount=original_amount,
            original_unit=None,
            normalized=False,
            note="missing unit; cannot normalize",
        )

    key = original_unit.lower()

    factor = _DATA_SIZE_TO_BYTES.get(key)
    if factor is not None:
        return NormalizedDecimal(
            original_amount=original_amount,
            original_unit=original_unit,
            normalized=True,
            canonical_amount=original_amount * Decimal(factor),
            canonical_unit=BYTE_UNIT,
            dimension=_DIMENSION_DATA,
        )

    if key in _COUNT_UNITS:
        return NormalizedDecimal(
            original_amount=original_amount,
            original_unit=original_unit,
            normalized=True,
            canonical_amount=original_amount,
            canonical_unit=COUNT_UNIT,
            dimension=_DIMENSION_COUNT,
        )

    return NormalizedDecimal(
        original_amount=original_amount,
        original_unit=original_unit,
        normalized=False,
        note=f"cannot normalize unit '{original_unit}'",
    )


def comparable_decimal(a: NormalizedDecimal, b: NormalizedDecimal) -> bool:
    """True only when both Decimal values normalized into the *same* dimension.

    Fails closed exactly like :func:`comparable`: if either value did not
    normalize, or they landed in different dimensions, they are not comparable.
    """

    return a.normalized and b.normalized and a.dimension is not None and a.dimension == b.dimension


__all__: Sequence[str] = (
    "BYTE_UNIT",
    "COUNT_UNIT",
    "NormalizedAmount",
    "normalize_amount",
    "comparable",
    "NormalizedDecimal",
    "normalize_amount_decimal",
    "comparable_decimal",
)
