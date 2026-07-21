"""Offline unit tests for the domain model (no database required).

These assert the *shape* of the SQLAlchemy metadata: that all 15 entities are
present, that the closed-vocabulary check constraints carry exactly the
membership documented in ``docs/DATA_MODEL.md``, that Evidence carries its
provenance foreign keys, and that the ORM mappers configure cleanly. The live
migration behaviour (apply / rollback / immutability trigger / FK enforcement)
is covered by tests/integration/test_domain_migration.py.
"""

from __future__ import annotations

from app.ingest.vocab import VERIFICATION_STATES as INGEST_VERIFICATION_STATES
from app.models import Candidate, DiscoveryCandidate, Evidence, metadata
from app.models.vocab import (
    DISCOVERY_VERIFICATION_STATUSES,
    EXHAUSTION_BEHAVIOURS,
    IMPORT_METHODS,
    OFFER_TYPES,
    VERIFICATION_STATES,
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
    "candidate",
    "discovery_candidate",
}


def _check_constraint_sql(table_name: str, column: str) -> str:
    table = metadata.tables[table_name]
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint) and column in str(constraint.sqltext):
            return str(constraint.sqltext)
    raise AssertionError(f"check constraint on {table_name}.{column} not found")


def test_all_fifteen_domain_tables_present() -> None:
    assert set(metadata.tables.keys()) == EXPECTED_TABLES
    assert len(EXPECTED_TABLES) == 15


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


def test_evidence_provenance_foreign_keys_and_link_target() -> None:
    evidence = metadata.tables["evidence"]
    referenced = {fk.parent.name: fk.column.table.name for fk in evidence.foreign_keys}
    # Provenance (where a claim came from) is always mandatory.
    assert referenced["source_id"] == "source"
    assert referenced["snapshot_id"] == "snapshot"
    for column_name in ("source_id", "snapshot_id"):
        assert evidence.columns[column_name].nullable is False
    # The subject an evidence row backs is either an offer_version (published) or
    # a candidate (pre-publication); both linkage columns are individually
    # optional but a CHECK requires at least one.
    assert referenced["offer_version_id"] == "offer_version"
    assert referenced["candidate_id"] == "candidate"
    assert evidence.columns["offer_version_id"].nullable is True
    assert evidence.columns["candidate_id"].nullable is True
    link_check = next(
        c
        for c in evidence.constraints
        if isinstance(c, CheckConstraint) and c.name.endswith("evidence_link_target")
    )
    sqltext = str(link_check.sqltext)
    assert "offer_version_id IS NOT NULL" in sqltext
    assert "candidate_id IS NOT NULL" in sqltext


def test_evidence_orm_maps_to_provenance_table() -> None:
    assert Evidence.__tablename__ == "evidence"


def test_candidate_verification_state_vocabulary_matches_ingest() -> None:
    # The candidate table's closed vocabulary must equal the ingestion pipeline's
    # verification-state lifecycle (single source of truth in app.ingest.vocab).
    assert VERIFICATION_STATES == INGEST_VERIFICATION_STATES
    sql = _check_constraint_sql("candidate", "verification_state")
    for value in VERIFICATION_STATES:
        assert f"'{value}'" in sql
    assert len(VERIFICATION_STATES) == 9


def test_discovery_candidate_vocabularies_match_data_model() -> None:
    import_sql = _check_constraint_sql("discovery_candidate", "import_method")
    for value in IMPORT_METHODS:
        assert f"'{value}'" in import_sql
    status_sql = _check_constraint_sql("discovery_candidate", "verification_status")
    for value in DISCOVERY_VERIFICATION_STATUSES:
        assert f"'{value}'" in status_sql


def test_discovery_candidate_has_no_evidence_or_offer_version_link() -> None:
    # Community-derived candidates are quarantined and must never be linked to
    # evidence or an offer_version (docs/SOURCE_REUSE_AND_PROVENANCE.md).
    discovery = metadata.tables["discovery_candidate"]
    referenced = {fk.column.table.name for fk in discovery.foreign_keys}
    assert "evidence" not in referenced
    assert "offer_version" not in referenced
    assert DiscoveryCandidate.__tablename__ == "discovery_candidate"


def test_candidate_never_links_to_offer_version() -> None:
    # A candidate may resolve to a service/offer but never to an offer_version
    # (no publication path in the ingestion layer).
    candidate = metadata.tables["candidate"]
    referenced = {fk.column.table.name for fk in candidate.foreign_keys}
    assert "offer_version" not in referenced
    assert Candidate.__tablename__ == "candidate"
