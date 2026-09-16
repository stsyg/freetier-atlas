"""Build-time static export of the read-only catalogue (F009-S2, slice 1).

ADR 0007 (Accepted) fixes the public deployment as a *static snapshot only*: no
running server, no database, no POST. This package turns the ten read-only
``/catalogue`` GET routes into deterministic, versioned JSON artefacts a static
host can serve, so slice 2 can build the client-side search / filter / compare /
adviser experience over them.

The export is a **tenth catalogue surface**. The defect class fixed in PR #95 /
PR #99 -- nine surfaces independently repeating an expired free claim -- applies
to it by construction, so the export reuses the *exact* currency mechanism the
nine live surfaces use (:mod:`app.read_api.currency` via
:func:`app.read_api.queries.currency_context` and the ``app.read_api.service``
serializers) rather than reimplementing staleness. See
:mod:`app.export.catalogue` for the design and the reasoning.
"""

from __future__ import annotations

from .catalogue import (
    GENERATOR,
    ROUTE_PREFIX,
    SNAPSHOT_VERSION,
    build_catalogue_export,
    canonical_json,
    render_export,
    write_export,
)

__all__ = [
    "GENERATOR",
    "ROUTE_PREFIX",
    "SNAPSHOT_VERSION",
    "build_catalogue_export",
    "canonical_json",
    "render_export",
    "write_export",
]
