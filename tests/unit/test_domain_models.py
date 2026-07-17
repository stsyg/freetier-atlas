"""Offline unit tests for the F003 domain model (no database required).

These assert the *shape* of the SQLAlchemy metadata: that all 13 entities are
present, that the closed-vocabulary check constraints carry exactly the
membership documented in ``docs/DATA_MODEL.md``, that Evidence carries the three
provenance foreign keys, and that the ORM mappers configure cleanly. The live
migration behaviour (apply / rollback / immutability trigger / FK enforcement)
is covered by tests/integration/test_domain_migration.py.
"""

from __future__ import annotations

from app.models import Evidence, metadata
from app.models.vocab import (
    EXHAUSTION_BEHAVIOURS,
    OFFER_TYPES,
    ZERO_COST_CLASSES,
)
from sqlalchemy import CheckConstraint
from sqlalchemy.orm import configure_mappers

EXPECTED_TABLES = {
    "provider",
    "category",
    "service",
    "offer",
    "offer_version",
    "quota",
    "region_availability",
    "source",
    "snapshot",
    "evidence",
    "change_event",
    "scan_run",
    "review_item",
}


def _check_constraint_sql(table_name: str, column: str) -> str:
    table = metadata.tables[table_name]
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint) and column in str(constraint.sqltext):
            return str(constraint.sqltext)
    raise AssertionError(f"check constraint on {table_name}.{column} not found")


def test_all_thirteen_domain_tables_present() -> None:
    assert set(metadata.tables.keys()) == EXPECTED_TABLES
    assert len(EXPECTED_TABLES) == 13


def test_mappers_configure_cleanly() -> None:
    configure_mappers()


def test_zero_cost_class_vocabulary_matches_data_model() -> None:
    sql = _check_constraint_sql("offer", "zero_cost_class")
    for value in ZERO_COST_CLASSES:
        assert f"'{value}'" in sql
    assert len(ZERO_COST_CLASSES) == 5
    # OfferVersion carries the same closed vocabulary.
    sql_ov = _check_constraint_sql("offer_version", "zero_cost_class")
    for value in ZERO_COST_CLASSES:
        assert f"'{value}'" in sql_ov


def test_offer_type_vocabulary_matches_data_model() -> None:
    sql = _check_constraint_sql("offer", "offer_type")
    for value in OFFER_TYPES:
        assert f"'{value}'" in sql
    assert len(OFFER_TYPES) == 11


def test_exhaustion_behaviour_vocabulary_matches_data_model() -> None:
    sql = _check_constraint_sql("quota", "exhaustion_behaviour")
    for value in EXHAUSTION_BEHAVIOURS:
        assert f"'{value}'" in sql
    assert len(EXHAUSTION_BEHAVIOURS) == 12


def test_evidence_has_three_provenance_foreign_keys() -> None:
    evidence = metadata.tables["evidence"]
    referenced = {fk.parent.name: fk.column.table.name for fk in evidence.foreign_keys}
    assert referenced["source_id"] == "source"
    assert referenced["offer_version_id"] == "offer_version"
    assert referenced["snapshot_id"] == "snapshot"
    # All three provenance links are mandatory (NOT NULL).
    for column_name in ("source_id", "offer_version_id", "snapshot_id"):
        assert evidence.columns[column_name].nullable is False


def test_evidence_orm_maps_to_provenance_table() -> None:
    assert Evidence.__tablename__ == "evidence"
