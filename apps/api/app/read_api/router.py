"""HTTP routes for the read-only catalogue API (F005 slice 3).

Seven ``GET`` endpoints under ``/catalogue`` expose the *published* catalogue.

Security posture (SECURITY.md):

* **Read-only.** Only ``GET`` methods are registered. There is no route that
  writes, mutates, or publishes anything, and the injected DB session
  (:func:`app.db.get_session`) never commits.
* **No user-controlled URLs / no SSRF surface.** Every path parameter is an
  internal identifier -- a provider ``slug`` (validated against a strict pattern)
  or an integer offer id. No endpoint accepts a URL/host/endpoint from the caller
  or fetches anything on the caller's behalf.
* **No LLM in the request path.** Responses are pure DB projections.
* **Published data only.** Queries never touch the ``candidate`` /
  ``discovery_candidate`` tables, so community/pre-publication data cannot leak.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from ..db import get_session
from . import queries, search, service
from .schemas import (
    CategoryMatrixResponse,
    CategoryStatesResponse,
    CompareResponse,
    OfferDetail,
    OfferEvidenceResponse,
    OfferHistoryResponse,
    OfferSummary,
    ProviderDetail,
    ProviderSummary,
    SearchResponse,
)

router = APIRouter(prefix="/catalogue", tags=["catalogue"])

#: Provider slugs are internal identifiers: lowercase alphanumerics + hyphens.
#: The pattern intentionally cannot express a scheme, host, or path, so a slug
#: can never be coerced into a fetchable URL.
_SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"

SessionDep = Annotated[Session, Depends(get_session)]
SlugParam = Annotated[str, Path(pattern=_SLUG_PATTERN, description="Internal provider slug")]
OfferIdParam = Annotated[int, Path(ge=1, description="Internal offer identifier")]

#: Query-parameter forms of the slug/enum filters used by ``/catalogue/search``.
#: ``provider`` / ``category`` reuse the strict slug pattern so a filter value can
#: never be a URL/host; enum membership is enforced in :mod:`.search`.
SearchQueryParam = Annotated[
    str | None, Query(max_length=search.MAX_Q_LENGTH, description="Keyword search text")
]
ProviderFilterParam = Annotated[str | None, Query(pattern=_SLUG_PATTERN)]
CategoryFilterParam = Annotated[str | None, Query(pattern=_SLUG_PATTERN)]
PageParam = Annotated[int, Query(ge=1, le=search.MAX_PAGE, description="1-based page number")]

#: Compare accepts a small, bounded id set; anything larger is rejected outright.
MAX_COMPARE_OFFERS = 6
_COMPARE_ID_PATTERN = re.compile(r"[0-9]{1,9}")


def _parse_compare_ids(raw: str) -> list[int]:
    """Parse ``offers=1,2,3`` into a bounded, de-duplicated list of positive ints.

    Rejects (HTTP 422) an empty/too-small/oversize set and any token that is not a
    small positive integer, so a hostile ``offers`` value (URL, path traversal,
    SQL-ish text, huge number) can never reach the query layer.
    """

    tokens = [t.strip() for t in raw.split(",") if t.strip() != ""]
    if len(tokens) < 2:
        raise HTTPException(status_code=422, detail="compare requires at least two offer ids.")
    if len(tokens) > MAX_COMPARE_OFFERS:
        raise HTTPException(
            status_code=422,
            detail=f"compare accepts at most {MAX_COMPARE_OFFERS} offer ids.",
        )
    ids: list[int] = []
    seen: set[int] = set()
    for token in tokens:
        if not _COMPARE_ID_PATTERN.fullmatch(token):
            raise HTTPException(status_code=422, detail="offer ids must be positive integers.")
        offer_id = int(token)
        if offer_id < 1:
            raise HTTPException(status_code=422, detail="offer ids must be positive integers.")
        if offer_id in seen:
            raise HTTPException(status_code=422, detail="duplicate offer id.")
        seen.add(offer_id)
        ids.append(offer_id)
    return ids


def _require_provider(session: Session, slug: str):
    # Defence in depth: even though FastAPI validates the pattern, re-check so a
    # slug can never be anything other than an internal identifier.
    if not re.fullmatch(_SLUG_PATTERN, slug):
        raise HTTPException(status_code=404, detail="Provider not found.")
    provider = queries.fetch_provider(session, slug)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found.")
    return provider


def _require_published_offer(session: Session, offer_id: int):
    offer = queries.fetch_offer(session, offer_id)
    if offer is None or not queries.is_published(offer):
        raise HTTPException(status_code=404, detail="Offer not found.")
    return offer


def _category_map_for(session: Session, provider) -> dict:
    category_ids = [s.category_id for s in provider.services if s.category_id is not None]
    return queries.category_map(session, category_ids)


@router.get("/providers", response_model=list[ProviderSummary])
def list_providers(session: SessionDep) -> list[ProviderSummary]:
    """List all providers with summary metadata + completeness/freshness."""

    providers = queries.fetch_providers(session)
    return [service.serialize_provider_summary(p) for p in providers]


@router.get("/providers/{provider_slug}", response_model=ProviderDetail)
def get_provider(provider_slug: SlugParam, session: SessionDep) -> ProviderDetail:
    """Get a single provider (e.g. Cloudflare) with its full metadata."""

    provider = _require_provider(session, provider_slug)
    return service.serialize_provider_detail(provider)


@router.get(
    "/providers/{provider_slug}/category-states",
    response_model=CategoryStatesResponse,
)
def get_category_states(provider_slug: SlugParam, session: SessionDep) -> CategoryStatesResponse:
    """Category/service states: published offers grouped with their Z0 state."""

    provider = _require_provider(session, provider_slug)
    cat_map = _category_map_for(session, provider)
    return service.serialize_category_states(provider, cat_map)


@router.get(
    "/providers/{provider_slug}/offers",
    response_model=list[OfferSummary],
)
def list_provider_offers(provider_slug: SlugParam, session: SessionDep) -> list[OfferSummary]:
    """List a provider's published offers (summary view)."""

    provider = _require_provider(session, provider_slug)
    cat_map = _category_map_for(session, provider)
    return service.serialize_offer_summaries(provider, cat_map)


@router.get("/offers/{offer_id}", response_model=OfferDetail)
def get_offer(offer_id: OfferIdParam, session: SessionDep) -> OfferDetail:
    """Offer detail: current version, Z0 class + reasons, quotas, confidence label.

    The primary confidence field is a plain-language label; the raw numeric score
    and signals appear only inside the ``advanced`` block (D039).
    """

    offer = _require_published_offer(session, offer_id)
    cat_map = queries.category_map(session, [offer.service.category_id])
    return service.serialize_offer_detail(offer, cat_map)


@router.get("/offers/{offer_id}/evidence", response_model=OfferEvidenceResponse)
def get_offer_evidence(offer_id: OfferIdParam, session: SessionDep) -> OfferEvidenceResponse:
    """Official evidence + provenance backing an offer's current version."""

    offer = _require_published_offer(session, offer_id)
    version = queries.latest_version(offer)
    evidence_rows = (
        queries.fetch_offer_evidence(session, offer_version_id=version.id)
        if version is not None
        else []
    )
    return service.serialize_offer_evidence(offer, evidence_rows)


@router.get("/offers/{offer_id}/history", response_model=OfferHistoryResponse)
def get_offer_history(offer_id: OfferIdParam, session: SessionDep) -> OfferHistoryResponse:
    """Append-only offer version history + published change events."""

    offer = _require_published_offer(session, offer_id)
    versions = queries.fetch_offer_versions(session, offer_id=offer.id)
    change_events = queries.fetch_offer_change_events(session, offer_id=offer.id)
    return service.serialize_offer_history(offer.id, versions, change_events)


@router.get("/search", response_model=SearchResponse)
def search_catalogue(
    session: SessionDep,
    q: SearchQueryParam = None,
    provider: ProviderFilterParam = None,
    category: CategoryFilterParam = None,
    zero_cost_class: Annotated[str | None, Query()] = None,
    offer_type: Annotated[str | None, Query()] = None,
    commercial_use: Annotated[bool | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    page: PageParam = 1,
) -> SearchResponse:
    """Keyword search + composable filters over published offers (paged).

    ``q`` is matched literally (its ``LIKE`` wildcards are escaped) against
    provider/service/offer text; every filter is validated against a closed set
    and composed with ``AND``. Results are deterministically ordered and paged.
    """

    try:
        params = search.build_params(
            q=q,
            provider=provider,
            category=category,
            zero_cost_class=zero_cost_class,
            offer_type=offer_type,
            commercial_use=commercial_use,
            status=status,
            page=page,
        )
    except search.SearchValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = search.search_published_offers(session, params)
    cat_map = queries.category_map(session, [o.service.category_id for o in result.offers])
    return service.serialize_search_response(result, params, cat_map)


@router.get("/categories", response_model=CategoryMatrixResponse)
def get_category_matrix(session: SessionDep) -> CategoryMatrixResponse:
    """The canonical 14-category taxonomy crossed with per-provider coverage.

    Every canonical category is always present; coverage states are derived
    strictly from published offers, and published offers with no canonical
    category are surfaced honestly in a per-provider uncategorized rollup.
    """

    providers = queries.fetch_providers(session)
    cat_map = queries.category_map_for_providers(session, providers)
    return service.serialize_category_matrix(providers, cat_map)


@router.get("/compare", response_model=CompareResponse)
def compare_offers(
    session: SessionDep,
    offers: Annotated[str, Query(description="Comma-separated offer ids, e.g. 1,2,3")],
) -> CompareResponse:
    """Normalized side-by-side comparison of a bounded set of published offers.

    Quota amounts are conservatively normalized (:mod:`.normalize`); a unit that
    cannot be confidently normalized fails closed rather than being guessed. An
    unknown or unpublished id yields 404; an empty/oversize id set yields 422.
    """

    offer_ids = _parse_compare_ids(offers)
    offer_map = queries.fetch_offers_by_ids(session, offer_ids)

    resolved: list = []
    for offer_id in offer_ids:
        offer = offer_map.get(offer_id)
        if offer is None or not queries.is_published(offer):
            raise HTTPException(status_code=404, detail="Offer not found.")
        resolved.append(offer)

    cat_map = queries.category_map(session, [o.service.category_id for o in resolved])
    return service.serialize_compare(offer_ids, resolved, cat_map)


__all__ = ["router"]
