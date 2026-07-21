"""FreeTier Atlas catalogue / evidence domain model (F003 task 004).

Thirteen entities model the catalogue (Provider, Category, Service, Offer),
the immutable material history (OfferVersion, Quota, RegionAvailability), the
evidence provenance chain (Source, Snapshot, Evidence), and the operational
records (ChangeEvent, ScanRun, ReviewItem). The schema mirrors
``docs/DATA_MODEL.md``.

Immutability of ``offer_version`` (an OfferVersion holds *immutable material
offer facts*) is enforced at the database level by a trigger installed in the
0003 migration; these ORM models describe the table shape only.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .vocab import (
    CHANGE_TYPES,
    DEPLOYMENT_MODELS,
    DISCOVERY_VERIFICATION_STATUSES,
    EXHAUSTION_BEHAVIOURS,
    IMPORT_METHODS,
    MATERIALITIES,
    OFFER_STATUSES,
    OFFER_TYPES,
    OFFER_VISIBILITIES,
    PUBLICATION_STATUSES,
    QUOTA_BEHAVIOURS,
    REVIEW_DISPOSITIONS,
    SCAN_STATUSES,
    VERIFICATION_STATES,
    ZERO_COST_CLASSES,
    sql_in,
)


def _pk() -> Mapped[int]:
    return mapped_column(BigInteger, primary_key=True, autoincrement=True)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Provider(Base):
    __tablename__ = "provider"

    id: Mapped[int] = _pk()
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    official_domains: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    source_health: Mapped[str | None] = mapped_column(Text, nullable=True)
    completeness_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    freshness_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    services: Mapped[list[Service]] = relationship(back_populates="provider")

    __table_args__ = (UniqueConstraint("slug", name="uq_provider_slug"),)


class Category(Base):
    __tablename__ = "category"

    id: Mapped[int] = _pk()
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (UniqueConstraint("slug", name="uq_category_slug"),)


class Service(Base):
    __tablename__ = "service"

    id: Mapped[int] = _pk()
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("provider.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id", ondelete="SET NULL"), nullable=True
    )
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    deployment_model: Mapped[str] = mapped_column(Text, nullable=False)
    portability_traits: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()

    provider: Mapped[Provider] = relationship(back_populates="services")
    offers: Mapped[list[Offer]] = relationship(back_populates="service")

    __table_args__ = (
        UniqueConstraint("provider_id", "canonical_name", name="uq_service_provider_id"),
        CheckConstraint(
            f"deployment_model IN {sql_in(DEPLOYMENT_MODELS)}",
            name="deployment_model_valid",
        ),
    )


class Offer(Base):
    __tablename__ = "offer"

    id: Mapped[int] = _pk()
    service_id: Mapped[int] = mapped_column(
        ForeignKey("service.id", ondelete="CASCADE"), nullable=False
    )
    offer_type: Mapped[str] = mapped_column(Text, nullable=False)
    zero_cost_class: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    eligibility: Mapped[str | None] = mapped_column(Text, nullable=True)
    commercial_use_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    personal_use_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    requires_card: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_paid_dependencies: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    available_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    available_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'public'"))
    first_seen_at: Mapped[datetime] = _created_at()
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()

    service: Mapped[Service] = relationship(back_populates="offers")
    versions: Mapped[list[OfferVersion]] = relationship(back_populates="offer")

    __table_args__ = (
        CheckConstraint(f"offer_type IN {sql_in(OFFER_TYPES)}", name="offer_type_valid"),
        CheckConstraint(
            f"zero_cost_class IN {sql_in(ZERO_COST_CLASSES)}",
            name="zero_cost_class_valid",
        ),
        CheckConstraint(f"status IN {sql_in(OFFER_STATUSES)}", name="status_valid"),
        CheckConstraint(f"visibility IN {sql_in(OFFER_VISIBILITIES)}", name="visibility_valid"),
    )


class OfferVersion(Base):
    """Immutable material offer facts (append-only; enforced by DB trigger)."""

    __tablename__ = "offer_version"

    id: Mapped[int] = _pk()
    offer_id: Mapped[int] = mapped_column(
        ForeignKey("offer.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    offer_type: Mapped[str] = mapped_column(Text, nullable=False)
    zero_cost_class: Mapped[str] = mapped_column(Text, nullable=False)
    material_facts: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()

    offer: Mapped[Offer] = relationship(back_populates="versions")
    quotas: Mapped[list[Quota]] = relationship(back_populates="offer_version")
    evidence: Mapped[list[Evidence]] = relationship(back_populates="offer_version")

    __table_args__ = (
        UniqueConstraint("offer_id", "version_number", name="uq_offer_version_offer_id"),
        CheckConstraint(f"offer_type IN {sql_in(OFFER_TYPES)}", name="offer_type_valid"),
        CheckConstraint(
            f"zero_cost_class IN {sql_in(ZERO_COST_CLASSES)}",
            name="zero_cost_class_valid",
        ),
    )


class Quota(Base):
    __tablename__ = "quota"

    id: Mapped[int] = _pk()
    offer_version_id: Mapped[int] = mapped_column(
        ForeignKey("offer_version.id", ondelete="CASCADE"), nullable=False
    )
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    reset_period: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    region_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    behaviour: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'unknown'"))
    exhaustion_behaviour: Mapped[str] = mapped_column(Text, nullable=False)
    retention_policy: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    offer_version: Mapped[OfferVersion] = relationship(back_populates="quotas")

    __table_args__ = (
        CheckConstraint(f"behaviour IN {sql_in(QUOTA_BEHAVIOURS)}", name="behaviour_valid"),
        CheckConstraint(
            f"exhaustion_behaviour IN {sql_in(EXHAUSTION_BEHAVIOURS)}",
            name="exhaustion_behaviour_valid",
        ),
    )


class RegionAvailability(Base):
    __tablename__ = "region_availability"

    id: Mapped[int] = _pk()
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("provider.id", ondelete="CASCADE"), nullable=False
    )
    offer_id: Mapped[int | None] = mapped_column(
        ForeignKey("offer.id", ondelete="CASCADE"), nullable=True
    )
    region_code: Mapped[str] = mapped_column(Text, nullable=False)
    free_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    residency: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_plane_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    control_plane_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class Source(Base):
    __tablename__ = "source"

    id: Mapped[int] = _pk()
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider.id", ondelete="SET NULL"), nullable=True
    )
    adapter_type: Mapped[str] = mapped_column(Text, nullable=False)
    trust_level: Mapped[str] = mapped_column(Text, nullable=False)
    official: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    schedule: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_profile: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    health: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    snapshots: Mapped[list[Snapshot]] = relationship(back_populates="source")


class Snapshot(Base):
    __tablename__ = "snapshot"

    id: Mapped[int] = _pk()
    source_id: Mapped[int] = mapped_column(
        ForeignKey("source.id", ondelete="CASCADE"), nullable=False
    )
    content_location: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source: Mapped[Source] = relationship(back_populates="snapshots")


class Evidence(Base):
    """Provenance: links a Source and a Snapshot to an OfferVersion or a Candidate.

    Every Evidence row records *where a material claim came from* (Source +
    Snapshot are always mandatory). The *subject* it backs is either a published
    ``offer_version`` (post-publication provenance, F003) or a pre-publication
    ``candidate`` (F004 ingestion). Exactly the linkage columns differ; a CHECK
    constraint requires at least one target so evidence is never orphaned. The
    ``offer_version`` immutability trigger is unrelated to and untouched by this.
    """

    __tablename__ = "evidence"

    id: Mapped[int] = _pk()
    source_id: Mapped[int] = mapped_column(
        ForeignKey("source.id", ondelete="RESTRICT"), nullable=False
    )
    offer_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("offer_version.id", ondelete="CASCADE"), nullable=True
    )
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), nullable=True
    )
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshot.id", ondelete="RESTRICT"), nullable=False
    )
    official: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = _created_at()
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    offer_version: Mapped[OfferVersion | None] = relationship(back_populates="evidence")
    candidate: Mapped[Candidate | None] = relationship(back_populates="evidence")
    source: Mapped[Source] = relationship()
    snapshot: Mapped[Snapshot] = relationship()

    __table_args__ = (
        CheckConstraint(
            "offer_version_id IS NOT NULL OR candidate_id IS NOT NULL",
            name="evidence_link_target",
        ),
    )


class ChangeEvent(Base):
    __tablename__ = "change_event"

    id: Mapped[int] = _pk()
    offer_id: Mapped[int] = mapped_column(
        ForeignKey("offer.id", ondelete="CASCADE"), nullable=False
    )
    previous_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("offer_version.id", ondelete="SET NULL"), nullable=True
    )
    new_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("offer_version.id", ondelete="SET NULL"), nullable=True
    )
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    materiality: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'unknown'"))
    occurred_at: Mapped[datetime] = _created_at()
    publication_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'draft'")
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(f"change_type IN {sql_in(CHANGE_TYPES)}", name="change_type_valid"),
        CheckConstraint(f"materiality IN {sql_in(MATERIALITIES)}", name="materiality_valid"),
        CheckConstraint(
            f"publication_status IN {sql_in(PUBLICATION_STATUSES)}",
            name="publication_status_valid",
        ),
    )


class ScanRun(Base):
    __tablename__ = "scan_run"

    id: Mapped[int] = _pk()
    source_id: Mapped[int] = mapped_column(
        ForeignKey("source.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'running'"))
    documents_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    candidates_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    changes_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    errors_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (CheckConstraint(f"status IN {sql_in(SCAN_STATUSES)}", name="status_valid"),)


class ReviewItem(Base):
    __tablename__ = "review_item"

    id: Mapped[int] = _pk()
    scan_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scan_run.id", ondelete="SET NULL"), nullable=True
    )
    offer_id: Mapped[int | None] = mapped_column(
        ForeignKey("offer.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_conflict: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    candidate_facts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_disposition: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(
            f"admin_disposition IN {sql_in(REVIEW_DISPOSITIONS)}",
            name="admin_disposition_valid",
        ),
    )


class Candidate(Base):
    """A pre-publication candidate fact produced by a scan (F004 ingestion).

    A Candidate is the persisted form of an adapter's ``CandidateFacts``: parsed
    from a source document but **not** a published offer. It belongs to a
    ``scan_run`` and a ``source`` and optionally maps to a ``service``/``offer``
    once resolved. ``candidate_facts`` holds the extracted material facts;
    ``content_hash`` is a deterministic hash of those facts (so re-scanning
    identical input yields an identical hash) and ``candidate_key`` is a stable
    identity hash used for change detection across scans. ``official`` records
    whether the originating source is trusted official; only official candidates
    receive ``evidence``. A candidate is never born ``verified``.
    """

    __tablename__ = "candidate"

    id: Mapped[int] = _pk()
    scan_run_id: Mapped[int] = mapped_column(
        ForeignKey("scan_run.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("source.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[int | None] = mapped_column(
        ForeignKey("service.id", ondelete="SET NULL"), nullable=True
    )
    offer_id: Mapped[int | None] = mapped_column(
        ForeignKey("offer.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'candidate'")
    )
    candidate_facts: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    candidate_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    official: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = _created_at()

    evidence: Mapped[list[Evidence]] = relationship(back_populates="candidate")

    __table_args__ = (
        CheckConstraint(
            f"verification_state IN {sql_in(VERIFICATION_STATES)}",
            name="verification_state_valid",
        ),
    )


class DiscoveryCandidate(Base):
    """A quarantined community-derived discovery candidate (F004 ingestion).

    Community repositories may *discover* candidates and expose coverage gaps but
    can never establish facts (docs/SOURCE_REUSE_AND_PROVENANCE.md). Every
    community-derived candidate is quarantined here with its provenance
    (repository, url, licence, discovery date, import method, verification
    status) and is **never** linked to ``evidence`` or ``offer_version``: there
    are deliberately no foreign keys to either. Official verification, if it ever
    happens, occurs through the separate official-evidence pipeline.
    """

    __tablename__ = "discovery_candidate"

    id: Mapped[int] = _pk()
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("source.id", ondelete="SET NULL"), nullable=True
    )
    repository: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    licence: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovery_date: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=text("CURRENT_DATE")
    )
    import_method: Mapped[str] = mapped_column(Text, nullable=False)
    verification_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unverified'")
    )
    candidate_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(
            f"import_method IN {sql_in(IMPORT_METHODS)}",
            name="import_method_valid",
        ),
        CheckConstraint(
            f"verification_status IN {sql_in(DISCOVERY_VERIFICATION_STATUSES)}",
            name="verification_status_valid",
        ),
    )
