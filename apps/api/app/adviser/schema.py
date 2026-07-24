"""Strict, structured workload schema for the deterministic adviser (F006 s3).

This is the *only* input the adviser accepts. It is deliberately strict so the
recommendation stays a pure function of well-formed structured data:

* **No natural language, no URLs.** There is no free-text project description to
  parse (NL parsing is F007) and every string field rejects URL/host-like input,
  so the endpoint presents no fetchable-URL / SSRF surface.
* **Bounded.** Every collection and string has an explicit maximum, and
  ``extra="forbid"`` rejects unknown keys, so a request cannot grow unboundedly
  or smuggle unexpected fields.
* **Exact amounts.** A demand amount is an exact :class:`~decimal.Decimal`
  greater than zero. A JSON float is re-parsed through ``str`` so
  ``0.1`` becomes ``Decimal("0.1")`` (never the binary-float artefact); the
  corpus expresses amounts as strings to make this unambiguous.
* **Fixed priorities.** The product's recommendation priorities are a code
  constant here, never caller input: exactly $0, then portability, then low
  lock-in.

The category of each requirement must be one of the fourteen canonical slugs
(:mod:`app.read_api.taxonomy`); an offer is only ever matched to a requirement
whose canonical category it actually declares.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from app.read_api.taxonomy import canonical_slugs

#: Product-fixed recommendation priorities (not caller input). The adviser always
#: optimises for an exact $0 guarantee first, then portability, then low lock-in.
FIXED_PRIORITIES: tuple[str, ...] = ("exactly_zero_cost", "portability", "low_lock_in")

# --- Bounds (all requests are explicitly bounded in size) ------------------- #
MAX_REQUIREMENTS = 20
MAX_DEMANDS_PER_REQUIREMENT = 20
MAX_CAPABILITIES = 20
MAX_TOKEN_LENGTH = 80
MAX_NAME_LENGTH = 120
MAX_REGION_LENGTH = 40

#: Substrings that make a value look like a URL/host/path. Any of these in a
#: free-text field is rejected outright -- the adviser accepts identifiers and
#: quantities only, never anything fetchable.
_URL_MARKERS: tuple[str, ...] = ("://", "http:", "https:", "www.", "/", "\\", "@", "..")


def _reject_url_like(value: str) -> str:
    """Raise if ``value`` contains a URL/host/path marker; else return it.

    Fails closed on anything that could be coerced into a fetchable location so
    the endpoint never accepts a user-controlled URL (SECURITY.md, no SSRF).
    """

    lowered = value.lower()
    for marker in _URL_MARKERS:
        if marker in lowered:
            raise ValueError("URLs, hosts, and paths are not accepted; provide plain identifiers")
    return value


def _to_exact_decimal(value: object) -> object:
    """Coerce a demand amount to an exact :class:`Decimal` before validation.

    A ``float`` is stringified first so the Decimal captures the intended
    decimal value rather than the binary artefact. Strings and ints pass through
    :class:`Decimal` directly. ``bool`` and unparseable values are handed back
    unchanged so pydantic raises the usual, informative validation error.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation:
            return value
    return value


#: A short identifier-like token (metric names, units, capabilities) with the
#: URL rejection applied. Stripped, bounded, and non-empty.
_Token = Annotated[
    str,
    Field(min_length=1, max_length=MAX_TOKEN_LENGTH),
    BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v),
    BeforeValidator(_reject_url_like),
]

#: An exact positive Decimal amount, coerced from number-or-string exactly.
_ExactAmount = Annotated[Decimal, BeforeValidator(_to_exact_decimal), Field(gt=0)]


class Demand(BaseModel):
    """One quantified demand within a requirement.

    ``metric`` names what is consumed (e.g. ``"storage"``, ``"requests"``),
    ``amount`` + ``unit`` quantify it exactly, and the optional ``period`` scopes
    a rate (e.g. ``"month"``). A demand fits an offer only when a quota covers
    this metric with exact, known-unit headroom (:mod:`app.adviser.quota_math`).
    """

    model_config = ConfigDict(extra="forbid")

    metric: _Token
    amount: _ExactAmount
    unit: _Token
    period: _Token | None = None


class Constraints(BaseModel):
    """Non-quantitative constraints a matching offer must satisfy.

    Defaults are conservative: ``commercial_use`` defaults to ``False`` (the
    workload is assumed personal unless it declares a commercial need) and
    ``personal_use_ok`` defaults to ``True``. ``region`` / ``residency`` are
    optional; when present, an offer must have free availability in that region
    (fail closed if that cannot be confirmed).
    """

    model_config = ConfigDict(extra="forbid")

    commercial_use: bool = False
    personal_use_ok: bool = True
    region: Annotated[str, Field(max_length=MAX_REGION_LENGTH)] | None = None
    residency: Annotated[str, Field(max_length=MAX_REGION_LENGTH)] | None = None

    @field_validator("region", "residency")
    @classmethod
    def _no_urls(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _reject_url_like(value.strip())


class Requirement(BaseModel):
    """A single component the workload needs, in one canonical category.

    ``category`` must be one of the fourteen canonical slugs. ``capabilities`` is
    an optional set of extra descriptors (currently advisory only -- recorded and
    echoed, never used to guess a match). ``demands`` are the quantified needs
    that must all be covered for an offer to fit.
    """

    model_config = ConfigDict(extra="forbid")

    category: str
    capabilities: Annotated[list[_Token], Field(max_length=MAX_CAPABILITIES)] = []
    demands: Annotated[list[Demand], Field(min_length=1, max_length=MAX_DEMANDS_PER_REQUIREMENT)]
    constraints: Constraints = Constraints()
    label: Annotated[str, Field(max_length=MAX_NAME_LENGTH)] | None = None

    @field_validator("category")
    @classmethod
    def _category_is_canonical(cls, value: str) -> str:
        slug = value.strip()
        if slug not in canonical_slugs():
            raise ValueError(
                "category must be one of the fourteen canonical slugs: "
                + ", ".join(canonical_slugs())
            )
        return slug

    @field_validator("label")
    @classmethod
    def _label_no_urls(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _reject_url_like(value.strip())


class RecommendationRequest(BaseModel):
    """The full workload: a bounded, named list of requirements.

    This is the request body of ``POST /adviser/recommend``. It carries no URL,
    no free-text description, and no LLM/provider selection -- the recommendation
    depends only on these structured requirements and the published catalogue.
    """

    model_config = ConfigDict(extra="forbid")

    workload_name: Annotated[str, Field(max_length=MAX_NAME_LENGTH)] | None = None
    requirements: Annotated[list[Requirement], Field(min_length=1, max_length=MAX_REQUIREMENTS)]

    @field_validator("workload_name")
    @classmethod
    def _name_no_urls(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _reject_url_like(value.strip())


__all__: Sequence[str] = (
    "FIXED_PRIORITIES",
    "MAX_REQUIREMENTS",
    "MAX_DEMANDS_PER_REQUIREMENT",
    "MAX_CAPABILITIES",
    "Demand",
    "Constraints",
    "Requirement",
    "RecommendationRequest",
)
