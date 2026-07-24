"""The canonical category taxonomy for the catalogue query API (F006 slice 1).

Decision D025 (docs/DECISIONS.md): *all fourteen categories for every MVP
provider*. The fourteen categories are the product's fixed evaluation axis
(docs/PRODUCT_REQUIREMENTS.md -> "Category taxonomy"), so the category-coverage
matrix always presents all fourteen regardless of what a given provider has
published yet.

This module is the single source of truth for that taxonomy as a pure, ordered
code constant. It is intentionally **not** a database seed: this slice adds no
migration and writes nothing. The matrix endpoint maps a persisted
``category.slug`` onto one of these canonical entries; a published service with
no (or a non-canonical) category is reported honestly in a per-provider
*uncategorized* rollup rather than being guessed into a category.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class CategoryTaxon:
    """One canonical category: a stable slug, a display name, and its ordinal."""

    ordinal: int
    slug: str
    name: str


#: The fourteen canonical categories, in their product-defined order. The slugs
#: are stable identifiers (lowercase, hyphenated) that a persisted
#: ``category.slug`` is matched against; the names mirror
#: docs/PRODUCT_REQUIREMENTS.md exactly.
CATEGORY_TAXONOMY: tuple[CategoryTaxon, ...] = (
    CategoryTaxon(1, "compute-vms", "Compute and virtual machines"),
    CategoryTaxon(2, "containers-app-hosting", "Containers and application hosting"),
    CategoryTaxon(3, "serverless-functions", "Serverless functions"),
    CategoryTaxon(4, "relational-databases", "Relational databases"),
    CategoryTaxon(5, "nosql-key-value", "NoSQL and key-value databases"),
    CategoryTaxon(6, "object-file-storage", "Object and file storage"),
    CategoryTaxon(7, "networking-cdn-dns", "Networking, CDN, and DNS"),
    CategoryTaxon(8, "queues-messaging-jobs", "Queues, messaging, and scheduled jobs"),
    CategoryTaxon(9, "auth-identity", "Authentication and identity"),
    CategoryTaxon(10, "cicd-source-control", "CI/CD and source control"),
    CategoryTaxon(11, "monitoring-logs-tracing", "Monitoring, logs, and tracing"),
    CategoryTaxon(12, "ai-inference-embeddings", "AI models, inference, and embeddings"),
    CategoryTaxon(13, "email-notifications-comms", "Email, notifications, and communications"),
    CategoryTaxon(14, "secrets-config-devtools", "Secrets, configuration, and developer tools"),
)

#: Fast membership / lookup by slug.
_BY_SLUG: dict[str, CategoryTaxon] = {taxon.slug: taxon for taxon in CATEGORY_TAXONOMY}


def canonical_slugs() -> tuple[str, ...]:
    """Return the fourteen canonical slugs in taxonomy order."""

    return tuple(taxon.slug for taxon in CATEGORY_TAXONOMY)


def is_canonical_slug(slug: str | None) -> bool:
    """True when ``slug`` names one of the fourteen canonical categories."""

    return slug is not None and slug in _BY_SLUG


def taxon_for_slug(slug: str | None) -> CategoryTaxon | None:
    """Return the canonical taxon for ``slug`` (or ``None`` if not canonical)."""

    if slug is None:
        return None
    return _BY_SLUG.get(slug)


__all__: Sequence[str] = (
    "CategoryTaxon",
    "CATEGORY_TAXONOMY",
    "canonical_slugs",
    "is_canonical_slug",
    "taxon_for_slug",
)
