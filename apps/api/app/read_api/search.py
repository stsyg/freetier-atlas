"""Deterministic in-DB catalogue search (F006 slice 1).

Owner decision Q3=A: search is a **deterministic in-database ILIKE / equality
match** -- no Postgres extension, no full-text index, no new dependency (an FTS
index is deferred to F008). This module owns the search-specific input validation
and the parameterized ``SELECT`` builder + executor.

Security posture:

* **Published-only.** The result set is restricted to offers that have at least
  one :class:`~app.models.domain.OfferVersion` (an ``EXISTS`` correlated
  sub-select). The ``candidate`` / ``discovery_candidate`` tables are never
  referenced, so community/pre-publication data can never surface.
* **No injection, no SSRF.** ``q`` is length-bounded and only ever used as a bound
  parameter to ``ILIKE`` (its ``LIKE`` wildcards are escaped so it matches
  literally); it is never fetched, and never string-formatted into SQL. Slug and
  enum filters are validated against closed sets before the query is built.
* **Deterministic.** Results are ordered by ``(provider slug, service canonical
  name, offer id)`` and paged with a fixed page size, so pagination is stable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.domain import Category, Offer, OfferVersion, Provider, Service
from app.models.vocab import OFFER_STATUSES, OFFER_TYPES, ZERO_COST_CLASSES

from .currency import ANCHOR_OFFER_VERSION, currency_for

#: Provider/category slugs are internal identifiers only (lowercase alphanumerics
#: + hyphens). The pattern cannot express a scheme/host/path, so a slug can never
#: be coerced into a fetchable URL. Kept identical to the read_api router's path
#: pattern.
SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"

#: ``q`` is bounded to keep the query cheap and to deny oversize hostile input.
MAX_Q_LENGTH = 128

#: Fixed page size (deterministic pagination). Not caller-tunable in this slice.
PAGE_SIZE = 20

#: Upper bound on the requested page number (defence against absurd offsets).
MAX_PAGE = 10_000

_ZERO_COST_CLASSES = frozenset(ZERO_COST_CLASSES)
_OFFER_TYPES = frozenset(OFFER_TYPES)
_OFFER_STATUSES = frozenset(OFFER_STATUSES)


class SearchValidationError(ValueError):
    """Raised when a search parameter is outside its allowed set (-> HTTP 422)."""


@dataclass(frozen=True)
class SearchParams:
    """The validated, normalized inputs to a catalogue search."""

    q: str | None = None
    provider: str | None = None
    category: str | None = None
    zero_cost_class: str | None = None
    offer_type: str | None = None
    commercial_use: bool | None = None
    status: str | None = None
    #: Evidence currency, an axis DISTINCT from ``zero_cost_class``. ``None``
    #: (the default) means "any", so an offer whose evidence has expired is
    #: still RETURNED -- labelled by its own currency verdict -- rather than
    #: silently omitted. A wrongly-omitted free offer is its own defect, and
    #: omission is invisible to the user in a way that a label is not.
    evidence_current: bool | None = None
    page: int = 1


@dataclass(frozen=True)
class SearchPage:
    """The executed search: the page's offers plus the total match count."""

    offers: list[Offer]
    total: int
    page: int
    page_size: int


def _escape_like(text: str) -> str:
    """Escape ``LIKE`` wildcards so ``q`` matches literally (no pattern injection)."""

    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _validate_enum(value: str | None, allowed: frozenset[str], field: str) -> str | None:
    if value is None:
        return None
    if value not in allowed:
        raise SearchValidationError(f"Invalid {field}.")
    return value


def build_params(
    *,
    q: str | None = None,
    provider: str | None = None,
    category: str | None = None,
    zero_cost_class: str | None = None,
    offer_type: str | None = None,
    commercial_use: bool | None = None,
    status: str | None = None,
    evidence_current: bool | None = None,
    page: int = 1,
) -> SearchParams:
    """Validate + normalize raw query inputs into :class:`SearchParams`.

    Enum filters are checked against their closed vocabularies and ``q`` is
    trimmed/bounded. Slug and page bounds are expected to be enforced by the route
    signature (FastAPI ``Query``), but enum membership is enforced here. Raises
    :class:`SearchValidationError` on any invalid value.
    """

    text = q.strip() if q is not None else None
    if text == "":
        text = None
    if text is not None and len(text) > MAX_Q_LENGTH:
        raise SearchValidationError("Search text too long.")

    if page < 1 or page > MAX_PAGE:
        raise SearchValidationError("Invalid page.")

    return SearchParams(
        q=text,
        provider=provider,
        category=category,
        zero_cost_class=_validate_enum(zero_cost_class, _ZERO_COST_CLASSES, "zero_cost_class"),
        offer_type=_validate_enum(offer_type, _OFFER_TYPES, "offer_type"),
        commercial_use=commercial_use,
        status=_validate_enum(status, _OFFER_STATUSES, "status"),
        evidence_current=evidence_current,
        page=page,
    )


def _conditions(params: SearchParams) -> list:
    """Build the parameterized WHERE conditions for a search (published-only)."""

    conditions = [Offer.versions.any()]  # published: EXISTS an immutable version

    if params.provider is not None:
        conditions.append(Provider.slug == params.provider)
    if params.category is not None:
        # Correlated sub-select keeps this a safe, parameterized equality even when
        # a service has no category (NULL category_id simply never matches).
        conditions.append(
            Service.category_id.in_(select(Category.id).where(Category.slug == params.category))
        )
    if params.zero_cost_class is not None:
        conditions.append(Offer.zero_cost_class == params.zero_cost_class)
    if params.offer_type is not None:
        conditions.append(Offer.offer_type == params.offer_type)
    if params.commercial_use is not None:
        conditions.append(Offer.commercial_use_allowed == params.commercial_use)
    if params.status is not None:
        conditions.append(Offer.status == params.status)

    if params.q is not None:
        pattern = f"%{_escape_like(params.q)}%"
        conditions.append(
            or_(
                Provider.name.ilike(pattern, escape="\\"),
                Provider.slug.ilike(pattern, escape="\\"),
                Service.canonical_name.ilike(pattern, escape="\\"),
                Offer.offer_type.ilike(pattern, escape="\\"),
                Offer.zero_cost_class.ilike(pattern, escape="\\"),
            )
        )
    return conditions


def _ordering() -> tuple:
    """The deterministic result order, named once so every phase shares it."""

    return (Provider.slug, Service.canonical_name, Offer.id)


def _latest_version_ids(session: Session, offer_ids: Sequence[int]) -> dict[int, int]:
    """``{offer_id: latest offer_version id}`` for the given offers.

    "Latest" is the highest ``version_number``, matching
    :func:`app.read_api.queries.latest_version` exactly. One query regardless of
    how many offers are involved.
    """

    if not offer_ids:
        return {}
    stmt = select(OfferVersion.offer_id, OfferVersion.id, OfferVersion.version_number).where(
        OfferVersion.offer_id.in_(list(offer_ids))
    )
    best: dict[int, tuple[int, int]] = {}
    for offer_id, version_id, number in session.execute(stmt).all():
        current = best.get(int(offer_id))
        if current is None or int(number) > current[1]:
            best[int(offer_id)] = (int(version_id), int(number))
    return {offer_id: version_id for offer_id, (version_id, _) in best.items()}


def _currency_filtered_ids(
    session: Session,
    conditions: Sequence,
    *,
    want_current: bool,
    now: datetime,
) -> list[int]:
    """Offer ids matching ``conditions`` AND the requested currency, in order.

    Deliberately id-first. Filtering on a value that only exists after a
    read-time computation cannot be pushed into the ``WHERE`` clause, so the
    naive fixes are either to hydrate every match as an ORM graph (expensive) or
    to filter the already-paged rows (which makes ``total_results`` a lie: the
    count is computed before the filter, so a page can render fewer rows than it
    claims -- or none at all while later pages still have results).

    This does neither. It pulls the matching ids *only*, resolves currency for
    them in one query, filters, and lets the caller slice the page from the
    surviving ids. The cost is N integers rather than N object graphs, and the
    count is taken AFTER the filter, so pagination stays honest.
    """

    id_stmt = (
        select(Offer.id)
        .join(Service, Offer.service_id == Service.id)
        .join(Provider, Service.provider_id == Provider.id)
        .where(*conditions)
        .order_by(*_ordering())
    )
    ordered_ids = [int(row) for row in session.execute(id_stmt).scalars().all()]
    latest = _latest_version_ids(session, ordered_ids)

    # Imported lazily: queries imports search-free modules, but keeping the
    # dependency one-directional here avoids an import cycle at module load.
    from .queries import fetch_evidence_currency

    index = fetch_evidence_currency(session, now=now, offer_version_ids=list(latest.values()))
    kept: list[int] = []
    for offer_id in ordered_ids:
        version_id = latest.get(offer_id)
        verdict = currency_for(ANCHOR_OFFER_VERSION, version_id, index)
        # `current` is the single predicate: an unchecked claim is NOT current,
        # so `evidence_current=false` correctly includes "we could not check"
        # alongside "expired" -- both are ways of not being current.
        if verdict.current is want_current:
            kept.append(offer_id)
    return kept


def search_published_offers(
    session: Session, params: SearchParams, *, now: datetime | None = None
) -> SearchPage:
    """Execute a deterministic, paged, published-only catalogue search.

    When ``params.evidence_current`` is ``None`` (the default) this is exactly
    the original two-statement query: a count and a page, both in SQL. The
    currency machinery below is only paid for when a caller explicitly asks to
    filter on it.
    """

    conditions = _conditions(params)

    if params.evidence_current is not None:
        kept = _currency_filtered_ids(
            session,
            conditions,
            want_current=params.evidence_current,
            now=now or datetime.now(UTC),
        )
        total = len(kept)
        offset = (params.page - 1) * PAGE_SIZE
        page_ids = kept[offset : offset + PAGE_SIZE]
        if not page_ids:
            return SearchPage(offers=[], total=total, page=params.page, page_size=PAGE_SIZE)
        hydrate = (
            select(Offer)
            .where(Offer.id.in_(page_ids))
            .options(
                selectinload(Offer.service).selectinload(Service.provider),
                selectinload(Offer.versions),
            )
        )
        by_id = {o.id: o for o in session.execute(hydrate).scalars().unique()}
        # Re-apply the id order rather than trusting the IN clause, which has no
        # defined ordering.
        offers = [by_id[oid] for oid in page_ids if oid in by_id]
        return SearchPage(offers=offers, total=total, page=params.page, page_size=PAGE_SIZE)

    count_stmt = (
        select(func.count(func.distinct(Offer.id)))
        .select_from(Offer)
        .join(Service, Offer.service_id == Service.id)
        .join(Provider, Service.provider_id == Provider.id)
        .where(*conditions)
    )
    total = int(session.execute(count_stmt).scalar_one())

    offset = (params.page - 1) * PAGE_SIZE
    page_stmt = (
        select(Offer)
        .join(Service, Offer.service_id == Service.id)
        .join(Provider, Service.provider_id == Provider.id)
        .where(*conditions)
        .order_by(*_ordering())
        .offset(offset)
        .limit(PAGE_SIZE)
        .options(
            selectinload(Offer.service).selectinload(Service.provider),
            selectinload(Offer.versions),
        )
    )
    offers = list(session.execute(page_stmt).scalars().unique())
    return SearchPage(offers=offers, total=total, page=params.page, page_size=PAGE_SIZE)


__all__: Sequence[str] = (
    "SLUG_PATTERN",
    "MAX_Q_LENGTH",
    "PAGE_SIZE",
    "MAX_PAGE",
    "SearchValidationError",
    "SearchParams",
    "SearchPage",
    "build_params",
    "search_published_offers",
)
