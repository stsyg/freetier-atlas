"""Deterministic build-time export of the ten read-only catalogue routes.

F009-S2, slice 1. ADR 0007 (Accepted) fixes the public deployment as a *static
snapshot only*, so this module renders the ten ``GET`` routes under
``/catalogue`` (:mod:`app.read_api.router`) into versioned JSON artefacts a
static host can serve. Slice 2 builds the client-side search / filter / compare
/ adviser experience on top of these files.

Design decisions (the reviewer asked for these explicitly)
==========================================================

Enumerating parameterised routes
--------------------------------
``/providers/{slug}`` and its two children are enumerated over
:func:`queries.fetch_providers` (ordered by slug). ``/offers/{id}`` and its two
children are enumerated over every *published* offer
(:func:`queries.is_published`), sorted by integer id. Each concrete instance is
fetched through the *same* query helper the live handler uses
(:func:`queries.fetch_provider`, :func:`queries.fetch_offer`) so the exported
body is the byte-for-byte projection the route would return. The manifest lists
the enumerated slugs and ids with counts, which doubles as a positive control:
a broken enumerator returns an empty list rather than a plausible-looking wrong
one.

``/search`` and ``/compare`` are inherently *queries*, not resources
-------------------------------------------------------------------
A static host cannot execute an arbitrary keyword search or an arbitrary
id-tuple comparison. Rather than freeze an arbitrary subset (which would silently
answer "no result" for anything not pre-baked), each is exported as the full
**corpus** it operates over, in the exact item shape the live route emits:

* ``search.json`` -- every published offer as a search result item (the union of
  every page of an unfiltered search). Slice 2 runs keyword / filter / sort over
  this corpus client-side.
* ``compare.json`` -- every published offer's normalised compare cell, produced
  by the server-side :func:`service.serialize_compare` so the conservative quota
  normalisation is preserved and never re-implemented in the browser. Slice 2
  composes any comparison from these cells.

Both carry ``kind: "corpus"`` and a ``note`` saying so, and -- critically -- each
item still carries its own ``evidence_currency`` verdict frozen at ``as_of``, so
a corpus item cannot present an expired claim as current any more than a resource
artefact can.

Reusing the existing currency mechanism (NOT a tenth implementation)
--------------------------------------------------------------------
PRs #95 / #99 fixed a defect class where nine catalogue surfaces each repeated an
expired free claim. This export is a tenth surface, so it does **not** reimplement
staleness. It calls the identical primitives the nine live handlers call --
:func:`queries.currency_context` (which wraps :mod:`app.read_api.currency`) and
the ``app.read_api.service`` serializers -- so the exact same
``confidence_label`` collapse, ``freshness -> null`` withholding, and
``evidence_currency`` block are baked into the artefacts.

One subtlety earns a single, whole-catalogue :class:`CurrencyContext`: the live
handlers *scope* their context to the version ids a request touches, but that
scope is an optimisation only. A version's verdict is computed from its own
evidence alone (see :func:`queries.fetch_evidence_currency`'s contract: "a
narrowed call and a full call agree on every key they share"), so the unscoped
context yields byte-identical verdicts for every version while letting the whole
export share one clock. A route-parity test pins this equivalence.

Invariants (acceptance criteria)
--------------------------------
1. Every artefact carries an explicit ``as_of`` (see :func:`_envelope`, and the
   manifest). ``as_of`` is injected, never read from the wall clock inside the
   builder.
2. The data itself encodes staleness: a stale/unknown claim serialises with a
   collapsed label, ``freshness: null``, and ``evidence_currency.current=false``,
   so a renderer cannot present it as fresh without re-deriving currency (which
   it structurally need not, and must not, do).
3. Unknown stays unknown: evidence with no fetch time is ``checked=false``
   (unchecked), never ``current``. An absent field is absent, not free.
4. Per-source staleness windows: the manifest reports each source's window via
   the same :func:`app.ingest.reconcile.parse_schedule_window` the currency path
   uses, so windows genuinely differ per source (e.g. ``2d`` daily, ``2h``
   hourly) rather than collapsing to one global figure.
5. Deterministic and reproducible: :func:`build_catalogue_export` is a pure
   function of ``(database state, as_of)``; ordering is fixed and
   :func:`canonical_json` emits sorted-key, stable JSON, so the same state yields
   byte-identical output modulo the injected ``as_of``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.reconcile import parse_schedule_window, window_to_compact
from app.models.domain import Provider, Source
from app.read_api import queries, search, service

#: Bumped when the artefact envelope shape changes in a way a consumer must notice.
SNAPSHOT_VERSION = 1
#: Stamped into every artefact so a published snapshot is traceable to its producer.
GENERATOR = "freetier-atlas-catalogue-export/1"
#: The router prefix these artefacts mirror.
ROUTE_PREFIX = "/catalogue"

_NAIVE_AS_OF = "as_of must be timezone-aware; a snapshot's currency clock cannot be naive"

_SEARCH_NOTE = (
    "A static host cannot execute an arbitrary query. This is the full "
    "published-offer corpus in the exact item shape /catalogue/search returns; "
    "slice 2 runs keyword search, filtering and sorting over it client-side. "
    "Each item carries its own evidence_currency verdict frozen at as_of."
)
_COMPARE_NOTE = (
    "A static host cannot enumerate every id-tuple. This is every published "
    "offer's normalised compare cell (server-side quota normalisation "
    "preserved); slice 2 composes any comparison from these cells client-side. "
    "Each cell carries its own evidence_currency verdict frozen at as_of."
)


def _as_of_text(as_of: datetime) -> str:
    """Canonical UTC ISO-8601 text for the injected snapshot clock."""

    if as_of.tzinfo is None:
        raise ValueError(_NAIVE_AS_OF)
    return as_of.astimezone(UTC).isoformat()


def _dump(model: BaseModel) -> Any:
    """Render a serializer's Pydantic model exactly as the HTTP layer would."""

    return model.model_dump(mode="json")


def _envelope(
    as_of: datetime,
    route: str,
    data: Any,
    *,
    kind: str = "route",
    note: str | None = None,
) -> dict[str, Any]:
    """Wrap a route body with the snapshot metadata every artefact must carry.

    ``as_of`` on every envelope satisfies invariant 1: no artefact can be read
    without knowing when its evidence currency was frozen.
    """

    envelope: dict[str, Any] = {
        "as_of": _as_of_text(as_of),
        "snapshot_version": SNAPSHOT_VERSION,
        "generator": GENERATOR,
        "route": route,
        "kind": kind,
        "data": data,
    }
    if note is not None:
        envelope["note"] = note
    return envelope


def _category_map_for(session: Session, provider: Provider) -> dict:
    """Category lookup for one provider's services (mirrors the router helper)."""

    category_ids = [s.category_id for s in provider.services if s.category_id is not None]
    return queries.category_map(session, category_ids)


def _search_corpus(
    session: Session, as_of: datetime, currency: queries.CurrencyContext
) -> dict[str, Any]:
    """Export every published offer as a search item (the whole corpus)."""

    first = search.search_published_offers(session, search.build_params(), now=as_of)
    total = first.total
    page_size = first.page_size
    total_pages = ceil(total / page_size) if page_size else 0

    pages = [first]
    for page_no in range(2, total_pages + 1):
        pages.append(
            search.search_published_offers(session, search.build_params(page=page_no), now=as_of)
        )

    all_offers = [offer for page in pages for offer in page.offers]
    cat_map = queries.category_map(session, [o.service.category_id for o in all_offers])

    results: list[Any] = []
    for page_no, page in enumerate(pages, start=1):
        params = search.build_params(page=page_no)
        dumped = _dump(service.serialize_search_response(page, params, cat_map, currency))
        results.extend(dumped["results"])

    data = {"page_size": page_size, "total_results": total, "results": results}
    return _envelope(as_of, f"{ROUTE_PREFIX}/search", data, kind="corpus", note=_SEARCH_NOTE)


def _compare_corpus(
    session: Session,
    as_of: datetime,
    currency: queries.CurrencyContext,
    offer_ids: list[int],
) -> dict[str, Any]:
    """Export every published offer's normalised compare cell (the whole corpus)."""

    offer_map = queries.fetch_offers_by_ids(session, offer_ids)
    resolved = [offer_map[offer_id] for offer_id in offer_ids if offer_id in offer_map]
    cat_map = queries.category_map(session, [o.service.category_id for o in resolved])
    compare = service.serialize_compare(offer_ids, resolved, cat_map, currency)
    return _envelope(
        as_of, f"{ROUTE_PREFIX}/compare", _dump(compare), kind="corpus", note=_COMPARE_NOTE
    )


def _manifest(
    session: Session,
    as_of: datetime,
    provider_slugs: list[str],
    offer_ids: list[int],
    artefact_paths: list[str],
) -> dict[str, Any]:
    """The snapshot index: as_of, counts (positive control), and per-source windows.

    ``staleness_windows`` derives each source's refresh window with the *same*
    :func:`parse_schedule_window` the currency path applies to ``Source.schedule``
    (see :func:`queries.fetch_evidence_currency`), so this is a report of the
    live mechanism's windows, not a second computation. Windows differ per source
    (invariant 4); a single global figure would be wrong.
    """

    sources = session.execute(select(Source)).scalars().all()
    # Total, deterministic order: slug when present, then id as the unique
    # tiebreaker so slug-less sources (and equal slugs) cannot depend on the
    # database's unordered row delivery. This is what makes the manifest
    # reproducible by construction rather than by luck (invariant 5).
    ordered = sorted(sources, key=lambda s: (s.slug is None, s.slug or "", s.id))
    staleness_windows = [
        {
            "source": s.slug,
            "schedule": s.schedule,
            "window": window_to_compact(parse_schedule_window(s.schedule)),
        }
        for s in ordered
    ]
    artifacts = sorted([*artefact_paths, "manifest.json"])
    return {
        "as_of": _as_of_text(as_of),
        "snapshot_version": SNAPSHOT_VERSION,
        "generator": GENERATOR,
        "route_prefix": ROUTE_PREFIX,
        "counts": {
            "providers": len(provider_slugs),
            "published_offers": len(offer_ids),
            "sources": len(ordered),
            "artifacts": len(artifacts),
        },
        "providers": list(provider_slugs),
        "offers": list(offer_ids),
        "staleness_windows": staleness_windows,
        "artifacts": artifacts,
    }


def build_catalogue_export(session: Session, *, as_of: datetime) -> dict[str, Any]:
    """Build the full catalogue snapshot as a ``{relative_path: json_object}`` map.

    Pure with respect to ``(session's committed database state, as_of)``: it reads
    only, never writes, and never consults the wall clock. Same state + same
    ``as_of`` -> identical mapping, so :func:`render_export` yields byte-identical
    bytes (invariant 5). ``as_of`` MUST be timezone-aware.
    """

    if as_of.tzinfo is None:
        raise ValueError(_NAIVE_AS_OF)

    # ONE whole-catalogue currency context, shared by every artefact. Unscoped is
    # byte-equivalent to each handler's scoped context (a version's verdict is
    # computed from its own evidence alone) and gives the whole export one clock.
    currency = queries.currency_context(session, now=as_of)
    providers = queries.fetch_providers(session)

    artefacts: dict[str, Any] = {}

    # /catalogue/providers
    artefacts["catalogue/providers.json"] = _envelope(
        as_of,
        f"{ROUTE_PREFIX}/providers",
        [_dump(service.serialize_provider_summary(p, currency)) for p in providers],
    )

    # /catalogue/providers/{slug}, .../category-states, .../offers
    provider_slugs: list[str] = []
    for summary_provider in providers:
        slug = summary_provider.slug
        provider_slugs.append(slug)
        provider = queries.fetch_provider(session, slug)
        cat_map = _category_map_for(session, provider)
        base = f"{ROUTE_PREFIX}/providers/{slug}"
        artefacts[f"catalogue/providers/{slug}.json"] = _envelope(
            as_of, base, _dump(service.serialize_provider_detail(provider, currency))
        )
        artefacts[f"catalogue/providers/{slug}/category-states.json"] = _envelope(
            as_of,
            f"{base}/category-states",
            _dump(service.serialize_category_states(provider, cat_map, currency)),
        )
        artefacts[f"catalogue/providers/{slug}/offers.json"] = _envelope(
            as_of,
            f"{base}/offers",
            [_dump(o) for o in service.serialize_offer_summaries(provider, cat_map, currency)],
        )

    # Published offers, enumerated deterministically by id.
    published_offers = sorted(
        (
            offer
            for p in providers
            for svc in p.services
            for offer in svc.offers
            if queries.is_published(offer)
        ),
        key=lambda o: o.id,
    )
    offer_ids = [offer.id for offer in published_offers]

    # /catalogue/offers/{id}, .../evidence, .../history
    for offer_id in offer_ids:
        offer = queries.fetch_offer(session, offer_id)
        cat_map = queries.category_map(session, [offer.service.category_id])
        base = f"{ROUTE_PREFIX}/offers/{offer_id}"
        artefacts[f"catalogue/offers/{offer_id}.json"] = _envelope(
            as_of, base, _dump(service.serialize_offer_detail(offer, cat_map, currency))
        )
        version = queries.latest_version(offer)
        evidence_rows = (
            queries.fetch_offer_evidence(session, offer_version_id=version.id)
            if version is not None
            else []
        )
        artefacts[f"catalogue/offers/{offer_id}/evidence.json"] = _envelope(
            as_of,
            f"{base}/evidence",
            _dump(service.serialize_offer_evidence(offer, evidence_rows, currency)),
        )
        versions = queries.fetch_offer_versions(session, offer_id=offer_id)
        change_events = queries.fetch_offer_change_events(session, offer_id=offer_id)
        artefacts[f"catalogue/offers/{offer_id}/history.json"] = _envelope(
            as_of,
            f"{base}/history",
            _dump(service.serialize_offer_history(offer_id, versions, change_events, currency)),
        )

    # /catalogue/categories
    cat_map_all = queries.category_map_for_providers(session, providers)
    context = queries.coverage_signal_context(session, providers, now=as_of)
    artefacts["catalogue/categories.json"] = _envelope(
        as_of,
        f"{ROUTE_PREFIX}/categories",
        _dump(service.serialize_category_matrix(providers, cat_map_all, context, currency)),
    )

    # /catalogue/search and /catalogue/compare -> corpora (see module docstring).
    artefacts["catalogue/search.json"] = _search_corpus(session, as_of, currency)
    artefacts["catalogue/compare.json"] = _compare_corpus(session, as_of, currency, offer_ids)

    # Snapshot index (as_of + counts + per-source windows).
    artefacts["manifest.json"] = _manifest(
        session, as_of, provider_slugs, offer_ids, list(artefacts.keys())
    )

    return artefacts


def canonical_json(obj: Any) -> str:
    """Serialise ``obj`` to stable, diffable JSON (sorted keys, trailing newline)."""

    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def render_export(artefacts: dict[str, Any]) -> dict[str, str]:
    """Render the artefact map to ``{relative_path: canonical_json_text}``."""

    return {path: canonical_json(obj) for path, obj in sorted(artefacts.items())}


def write_export(artefacts: dict[str, Any], out_dir: str | Path) -> list[str]:
    """Write every rendered artefact under ``out_dir``; return the paths written.

    Files are written with LF newlines regardless of platform so the published
    bytes are identical whether the export runs on Linux CI or a Windows box.
    """

    out = Path(out_dir)
    written: list[str] = []
    for path, text in render_export(artefacts).items():
        destination = out / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written


__all__ = [
    "GENERATOR",
    "ROUTE_PREFIX",
    "SNAPSHOT_VERSION",
    "build_catalogue_export",
    "canonical_json",
    "render_export",
    "write_export",
]
