"""The bucket stale flag on a GENUINELY multi-version offer (end to end).

The unit arms in ``tests/unit/test_read_api.py`` pin the rule against a
hand-built graph. This module pins the same behaviour against offer versions the
real publisher created, because the whole defect only exists once an offer has
more than one version, and no committed fixture has ever had one.

Why that matters, measured rather than assumed
----------------------------------------------
Publishing the committed corpus for real (7 provider configs, 6 published
offers) yields **exactly one version per offer**: ``publisher.py`` reuses the
prior version whenever the content hash is unchanged, so a repeat publish of
fixed fixtures can never create a second. The consequence is that the defect was
**latent** -- 0 differing buckets across 105 bucket-observations -- and that a
test wanting a second version must *earn* one by publishing changed content,
which is what ``_publish_twice`` does here.

Both directions, as everywhere on this surface
----------------------------------------------
A bucket whose latest versions are current must report ``verified_free`` even
with an expired ancestor present; a bucket whose LATEST version has expired must
still report ``stale``. A guard that cannot be shown to permit is
indistinguishable from one that broke the product.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.config.loader import load_and_validate
from app.config.models import ProviderConfig
from app.ingest.runner import build_fixture_fetcher, run_provider_scans
from app.models.domain import Evidence, Offer, Snapshot
from app.read_api import queries
from app.read_api import service as read_service
from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "cloudflare.example.yaml"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ingest" / "cloudflare" / "html"

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start Postgres (scripts/stack-up) and export it to enable.",
)

FREE = "Z0_TRUE_FREE"

#: A figure that appears in the committed Cloudflare fixture. Editing it is what
#: changes the content hash and therefore earns a second version. Asserted
#: present before use -- an edit that silently matched nothing would leave a
#: single-version offer and quietly turn every assertion below vacuous.
QUOTA_NEEDLE = "100,000"
QUOTA_REPLACEMENT = "90,000"


def _alembic_config() -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return cfg


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    from sqlalchemy import create_engine

    command.upgrade(_alembic_config(), "head")
    eng = create_engine(DATABASE_URL)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    conn = engine.connect()
    trans = conn.begin()
    sess = Session(bind=conn)
    try:
        yield sess
    finally:
        sess.close()
        trans.rollback()
        conn.close()


def _config() -> ProviderConfig:
    model = load_and_validate(str(CONFIG_PATH))
    return model if isinstance(model, ProviderConfig) else ProviderConfig(**model)


def _publish(session: Session, fixtures_dir: Path) -> None:
    config = _config()
    fetcher = build_fixture_fetcher(config, fixtures_dir)
    run_provider_scans(session, config, fetcher, publish=True)
    session.flush()


def _changed_fixture_tree(destination: Path) -> Path:
    """A copy of the committed fixtures with one quota figure edited.

    Stands in for a provider changing its published page: the bytes differ, so
    the content hash differs, so ``publisher.py`` writes a NEW version instead of
    reusing the prior one.
    """

    tree = destination / "cloudflare-html"
    shutil.copytree(FIXTURES_DIR, tree)
    edited = 0
    for html in tree.rglob("source.html"):
        text = html.read_text(encoding="utf-8")
        if QUOTA_NEEDLE in text:
            html.write_text(text.replace(QUOTA_NEEDLE, QUOTA_REPLACEMENT), encoding="utf-8")
            edited += 1
    assert edited > 0, (
        f"no committed fixture contained {QUOTA_NEEDLE!r}; the second publish would "
        "reuse the prior version and this module would assert nothing"
    )
    return tree


def _multi_version_offer(session: Session) -> Offer:
    provider = queries.fetch_provider(session, "cloudflare")
    assert provider is not None, "cloudflare provider should exist after publish"
    for svc in provider.services:
        for offer in svc.offers:
            if len(list(offer.versions)) > 1 and offer.zero_cost_class == FREE:
                return offer
    raise AssertionError("no published free offer acquired a second version")


@pytest.fixture
def two_versions(session: Session, tmp_path: Path) -> dict:
    """One real, free, published offer carrying a superseded ancestor version."""

    _publish(session, FIXTURES_DIR)
    _publish(session, _changed_fixture_tree(tmp_path))

    offer = _multi_version_offer(session)
    versions = sorted(offer.versions, key=lambda v: v.version_number)
    latest = queries.latest_version(offer)
    assert latest is not None
    assert latest.id == versions[-1].id, "fixture precondition: latest is the highest version"
    ancestors = [v for v in versions if v.id != latest.id]
    assert ancestors, "fixture precondition: at least one superseded version"

    category_slug = None
    cat_map = queries.category_map_for_providers(session, [offer.service.provider])
    category = cat_map.get(offer.service.category_id) if offer.service.category_id else None
    if category is not None:
        category_slug = category.slug

    return {
        "offer": offer,
        "latest_id": latest.id,
        "ancestor_ids": [v.id for v in ancestors],
        "provider_slug": offer.service.provider.slug,
        "category_slug": category_slug,
    }


def _age_evidence(session: Session, version_ids: list[int], *, days: int) -> int:
    """Backdate the snapshots behind ``version_ids``; returns rows changed.

    Models elapsed time -- a version published a year ago and never refreshed --
    which is the only way two versions of one offer ever acquire different
    currency. It fabricates no row the publisher did not create.
    """

    snapshot_ids = (
        session.execute(
            select(Evidence.snapshot_id).where(Evidence.offer_version_id.in_(version_ids))
        )
        .scalars()
        .all()
    )
    assert snapshot_ids, "the versions under test must rest on real snapshots"
    result = session.execute(
        update(Snapshot)
        .where(Snapshot.id.in_(snapshot_ids))
        .values(fetched_at=datetime.now(UTC) - timedelta(days=days))
    )
    session.flush()
    return result.rowcount


def _cell(session: Session, *, now: datetime, provider_slug: str, category_slug: str | None):
    """The real coverage cell, built through the shipped read path."""

    providers = queries.fetch_providers(session)
    cat_map = queries.category_map_for_providers(session, providers)
    context = queries.coverage_signal_context(session, providers, now=now)
    currency = queries.currency_context(
        session,
        now=now,
        offer_version_ids=queries.version_ids_for_offers(
            [o for p in providers for s in p.services for o in s.offers]
        ),
    )
    matrix = read_service.serialize_category_matrix(providers, cat_map, context, currency)
    for row in matrix.categories:
        if row.slug != category_slug:
            continue
        for cov in row.providers:
            if cov.provider_slug == provider_slug:
                return cov
    raise AssertionError(f"no cell for {category_slug}/{provider_slug}")


@skip_without_db
def test_a_real_republish_of_changed_content_creates_a_second_version(
    two_versions: dict,
) -> None:
    """The precondition every other test here rests on, asserted not assumed.

    A repeat publish of UNCHANGED content reuses the prior version, so this
    module would silently measure a single-version offer -- and a single-version
    offer cannot distinguish the two rules at all.
    """

    assert two_versions["ancestor_ids"], "no superseded version was created"
    assert two_versions["latest_id"] not in two_versions["ancestor_ids"]


@skip_without_db
def test_an_expired_ancestor_does_not_withhold_the_bucket_badge(
    session: Session, two_versions: dict
) -> None:
    """PERMIT ARM, end to end. The superseded version must not speak for the claim.

    ``fetch_stale_offer_version_ids`` is scoped to no version list, so the
    ancestor really does land in the stale set; the assertion below is that the
    bucket no longer inherits it.
    """

    aged = _age_evidence(session, two_versions["ancestor_ids"], days=400)
    assert aged > 0

    now = datetime.now(UTC)
    stale = queries.fetch_stale_offer_version_ids(session, now=now)
    assert set(two_versions["ancestor_ids"]) & stale, "the ancestor must actually be stale"
    assert two_versions["latest_id"] not in stale, "the latest claim must still be current"

    cell = _cell(
        session,
        now=now,
        provider_slug=two_versions["provider_slug"],
        category_slug=two_versions["category_slug"],
    )

    assert cell.derived_state == "verified_free"
    # ...and the cell does not contradict itself while saying so.
    assert cell.evidence_currency.current is True


@skip_without_db
def test_an_expired_latest_version_still_marks_the_bucket_stale(
    session: Session, two_versions: dict
) -> None:
    """WITHHOLD ARM, end to end. Expiry of the LIVE claim must still show.

    Same offer, same ancestor, but the latest version's own evidence has expired
    too. Nothing about ignoring ancestors may buy freshness the current evidence
    does not support.
    """

    aged = _age_evidence(
        session,
        two_versions["ancestor_ids"] + [two_versions["latest_id"]],
        days=400,
    )
    assert aged > 0

    now = datetime.now(UTC)
    stale = queries.fetch_stale_offer_version_ids(session, now=now)
    assert two_versions["latest_id"] in stale, "the latest claim must actually be stale"

    cell = _cell(
        session,
        now=now,
        provider_slug=two_versions["provider_slug"],
        category_slug=two_versions["category_slug"],
    )

    assert cell.derived_state == "stale"
