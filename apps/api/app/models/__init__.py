"""FreeTier Atlas domain model package (F003 task 004).

Exposes the declarative :class:`Base` (and its metadata) plus the 13 catalogue /
evidence entities. Importing this package has no side effects and requires no
database connection, so it is safe to import from Alembic's ``env.py`` and from
offline unit tests.
"""

from __future__ import annotations

from .base import Base, alembic_include_object, domain_table_names
from .domain import (
    Category,
    ChangeEvent,
    Evidence,
    Offer,
    OfferVersion,
    Provider,
    Quota,
    RegionAvailability,
    ReviewItem,
    ScanRun,
    Service,
    Snapshot,
    Source,
)

metadata = Base.metadata

__all__ = [
    "Base",
    "metadata",
    "alembic_include_object",
    "domain_table_names",
    "Category",
    "ChangeEvent",
    "Evidence",
    "Offer",
    "OfferVersion",
    "Provider",
    "Quota",
    "RegionAvailability",
    "ReviewItem",
    "ScanRun",
    "Service",
    "Snapshot",
    "Source",
]
