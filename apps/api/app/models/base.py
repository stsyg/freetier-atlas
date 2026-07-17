"""Declarative base and shared metadata for the FreeTier Atlas domain model.

A deterministic naming convention is attached to the metadata so that primary
keys, foreign keys, unique/check constraints, and indexes get stable,
predictable names in both the ORM models and the Alembic migration. This makes
``alembic`` autogenerate and ``compare_metadata`` drift checks reliable.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Stable constraint/index naming so migrations and ORM metadata agree.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base carrying the shared, naming-convention-bound metadata."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def domain_table_names() -> set[str]:
    """Return the set of table names owned by the domain model."""

    return set(Base.metadata.tables.keys())


def alembic_include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Restrict Alembic autogenerate/compare to domain-owned tables.

    Non-domain tables (and their columns/constraints/indexes) are excluded so
    the partial domain metadata does not cause Alembic to propose dropping the
    infrastructure tables owned by earlier migrations (app_meta, job_queue,
    service_heartbeat).
    """

    if type_ == "table":
        return name in domain_table_names()
    return True
