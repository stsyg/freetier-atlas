"""Coverage-declaration assertions for provider slices (F008 slice S2).

Wave-3 provider slices (one per provider) call these from their own tests so a
provider that quietly declares ``unknown`` -- or, worse, ``not_offered`` -- over a
category in which it demonstrably has a published offer fails **its own** suite,
rather than only surfacing later in an admin queue nobody is watching.

The real logic lives in :mod:`app.ingest.reconcile_coverage` (it is production
behaviour, not test-only behaviour); this module is the thin, discoverable
entry point plus a database-free variant for pure config checks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.config.models import ProviderConfig
from app.ingest.reconcile_coverage import assert_no_coverage_contradictions
from app.read_api.coverage import (
    CoverageSignals,
    derive_coverage_state,
    describe_mismatch,
    is_material_mismatch,
    mismatch_detail,
)

__all__: Sequence[str] = (
    "assert_no_coverage_contradictions",
    "assert_declarations_match_signals",
)


def assert_declarations_match_signals(
    config: ProviderConfig, signals: Mapping[str, CoverageSignals]
) -> None:
    """Database-free variant: check a config against per-category signals.

    ``signals`` maps a canonical category slug to the signals a slice expects its
    published catalogue to produce. Categories **absent** from ``signals`` are
    ones the slice makes no assertion about, and are skipped -- so a slice can
    assert only the pairs it actually cares about.

    That skip is load-bearing rather than cosmetic. Absence used to be modelled
    as "nothing is published there", which derived ``unknown`` and was never
    material. Since the ``f008-obsC`` ruling a derived ``unknown`` *can* be
    material (under a declared ``verified_free``), so modelling silence as
    evidence of absence would make every partial call fail on categories the
    caller never claimed anything about. The real declared-vs-derived check
    against an actual catalogue is
    :func:`app.ingest.reconcile_coverage.assert_no_coverage_contradictions`;
    this variant only judges what it was given.
    """

    failures: list[str] = []
    for slug, entry in sorted(config.coverage.items()):
        expected = signals.get(slug)
        if expected is None:
            continue
        derived = derive_coverage_state(expected)
        if not is_material_mismatch(entry.state, derived):
            continue
        failures.append(
            describe_mismatch(
                mismatch_detail(
                    provider_slug=config.provider.id,
                    category_slug=slug,
                    declared_state=entry.state,
                    derived_state=derived,
                    signals=expected,
                )
            )
        )
    if failures:
        joined = "\n  ".join(failures)
        raise AssertionError(
            f"{config.provider.id}: {len(failures)} coverage declaration(s) contradict "
            f"the expected published catalogue:\n  {joined}"
        )
