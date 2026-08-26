"""Evidence currency: is the evidence behind a claim still inside its window?

F008 follow-up. The catalogue's first rule is that no unsupported claim that a
service is free may ever ship. Staleness was already understood at *ingest* time
(:func:`app.ingest.reconcile.assess_staleness`) and at the *coverage-declaration*
surface (``fetch_stale_offer_version_ids``), but both treat expiry as a property
of **published offer-version rows**. It is not. It is a property of **evidence**,
wherever that evidence happens to hang, and every surface that repeats a claim
needs to know whether the evidence still supports it.

Why a dataclass and not a boolean
---------------------------------
``stale`` alone cannot express the difference between the two ways a claim can
fail to be current, and collapsing them is how a gap hides:

* ``checked=True,  stale=True``  -- we looked, and the evidence has expired.
* ``checked=False, stale=False`` -- **we could not look at all.** There is no
  snapshot timestamp and/or no source schedule to compare against, so no
  statement about currency is available.

The second case must never be read as "fresh". A declaration backed only by an
``evidence_url`` (no ``source`` -> no ``schedule`` -> no ``snapshot`` -> no
``fetched_at``) is exactly this shape: it is time-invariant, and reporting it as
current would be a guess in the product's forbidden direction. "Unknown is
better than guessed" applies here in both directions, which is why
:func:`is_publishable_free_claim` refuses ``checked=False`` as well as
``stale=True`` while :meth:`EvidenceCurrency.freshness` returns ``None`` rather
than ``0.0`` for it -- an absent measurement, not a bad score.

Nothing here is stored
----------------------
Currency is recomputed on every read against a caller-supplied ``now``. Decision
Q11 (restated in :mod:`app.read_api.coverage`) is the reason: a stored
projection becomes a second source of truth that silently goes stale, which is
precisely the failure this module exists to expose. Persisting a freshness
figure at publish time is what made a five-year-expired claim report ``1.0``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

#: Anchor kinds a currency verdict can be keyed by. ``offer_version`` is the only
#: anchor that exists today; ``coverage_declaration`` is reserved for the
#: declaration-source slice, which is the work of making ``checked`` true for a
#: declaration that carries no source. Callers key by ``(kind, id)`` so that
#: slice adds a kind without changing a single call site.
ANCHOR_OFFER_VERSION = "offer_version"
ANCHOR_COVERAGE_DECLARATION = "coverage_declaration"

#: The label a claim's confidence collapses to once its evidence is no longer
#: known to be current. Deliberately ``"unknown"`` and not ``"low"``: a low
#: confidence still asserts a measurement was made and came out weak, whereas an
#: expired (or never-checkable) claim has no current measurement at all.
UNSUPPORTED_CONFIDENCE_LABEL = "unknown"


@dataclass(frozen=True, slots=True)
class EvidenceCurrency:
    """Whether the evidence behind one claim is still within its refresh window.

    ``age`` and ``window`` are the *worst* (oldest evidence) pair found for the
    anchor, so a claim resting on several sources is only as current as its
    stalest support. Overstating expiry only ever makes the catalogue admit
    uncertainty, which is the direction the product errs in.
    """

    #: True only when we checked AND the evidence is past its window.
    stale: bool = False
    #: Was a currency check possible at all (a timestamp existed to compare)?
    checked: bool = False
    #: Age of the oldest backing evidence at the moment of the check.
    age: timedelta | None = None
    #: The refresh window that age was compared against.
    window: timedelta | None = None
    #: When the oldest backing evidence was fetched.
    oldest_fetched_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.stale and not self.checked:
            raise ValueError("evidence cannot be known stale without having been checked")

    @property
    def current(self) -> bool:
        """True only for evidence we checked and found inside its window.

        The single predicate a caller should use to decide whether a free claim
        may still be repeated. ``not current`` covers both failure shapes.
        """

        return self.checked and not self.stale

    def freshness(self) -> float | None:
        """Freshness in ``[0, 1]`` recomputed against the age used for the check.

        ``None`` when no check was possible -- an absent measurement rather than
        a zero score, so a caller cannot mistake "we could not look" for "this
        scored badly". Reuses the publisher's own ratio so the read-time figure
        and the publish-time figure can never drift apart.
        """

        if not self.checked or self.age is None or self.window is None:
            return None
        # Imported lazily: ``app.publish`` pulls in ``app.config.models``, which
        # imports ``app.read_api.taxonomy``, so a module-level import here would
        # close a cycle. ``freshness_ratio`` is pure arithmetic with no I/O.
        from app.publish.confidence import freshness_ratio

        return freshness_ratio(self.age, self.window)

    def reason(self) -> str | None:
        """A one-line, human-readable explanation, or ``None`` when current."""

        if self.current:
            return None
        if self.stale:
            window = _describe(self.window)
            age = _describe(self.age)
            return (
                f"The official evidence backing this claim was last fetched {age} ago, "
                f"past its {window} refresh window, so it is no longer known to be current."
            )
        return (
            "No official evidence with a checkable fetch time backs this claim, "
            "so whether it is still current cannot be established."
        )


#: The verdict for an anchor we know nothing about. Deliberately not ``stale``:
#: absence of evidence is not evidence of expiry, and it is not freshness either.
UNCHECKED = EvidenceCurrency()


def _describe(delta: timedelta | None) -> str:
    """Render a duration in the largest sensible whole unit (never guessed)."""

    if delta is None:
        return "an unknown period"
    seconds = max(int(delta.total_seconds()), 0)
    for unit_seconds, name in (
        (86400, "day"),
        (3600, "hour"),
        (60, "minute"),
    ):
        if seconds >= unit_seconds:
            count = seconds // unit_seconds
            return f"{count} {name}{'s' if count != 1 else ''}"
    return f"{seconds} second{'s' if seconds != 1 else ''}"


def assess_currency(
    fetched_at: datetime | None,
    now: datetime,
    schedule: str | None,
) -> EvidenceCurrency:
    """Assess one piece of evidence against its source's refresh window.

    ``fetched_at is None`` yields :data:`UNCHECKED` -- there is nothing to
    compare, and inventing a timestamp would be the guess this module forbids.
    A missing/unparseable ``schedule`` still counts as *checked*: the ingest
    layer's documented default window applies, exactly as it does at ingest.
    """

    if fetched_at is None:
        return UNCHECKED

    # Lazy for the same cycle reason as ``freshness`` above; ``assess_staleness``
    # is pure. Reusing it keeps ONE definition of "past the window" in the
    # codebase, so the read surfaces and the ingest pipeline cannot disagree.
    from app.ingest.reconcile import assess_staleness

    assessment = assess_staleness(fetched_at, now, schedule)
    return EvidenceCurrency(
        stale=assessment.stale,
        checked=True,
        age=assessment.age,
        window=assessment.window,
        oldest_fetched_at=fetched_at,
    )


def worst(currencies: Sequence[EvidenceCurrency]) -> EvidenceCurrency:
    """Combine several verdicts into the least-current one.

    Precedence: any ``stale`` wins, then any ``unchecked``, then the oldest
    still-current verdict. A claim is only as current as its weakest support, and
    a single uncheckable source is enough to stop us asserting currency for the
    whole claim.
    """

    if not currencies:
        return UNCHECKED
    stale = [c for c in currencies if c.stale]
    if stale:
        return max(stale, key=lambda c: c.age or timedelta.max)
    if any(not c.checked for c in currencies):
        return UNCHECKED
    return max(currencies, key=lambda c: c.age or timedelta.min)


def is_publishable_free_claim(currency: EvidenceCurrency) -> bool:
    """May a *free* claim resting on this evidence still be repeated?

    The one predicate every surface should gate a free claim on. Fails closed on
    both shapes of non-currency, so adding a new surface cannot accidentally
    treat "we could not check" as permission.
    """

    return currency.current


def freshness_or_none(currency: EvidenceCurrency) -> float | None:
    """The read-time freshness figure a surface may publish, or ``None``.

    A thin, deliberately-named wrapper over :meth:`EvidenceCurrency.freshness`.
    It exists so that the rule the catalogue must never break -- *an unchecked
    claim reports no number at all, not a bad one* -- is a named thing a reviewer
    can grep for, rather than an easily-lost ``| None`` on a call site.

    The distinction is load-bearing all the way to the rendered page: the web
    formatter turns ``None`` into ``"Unknown"`` but turns ``0.0`` into ``"0%"``,
    so returning zero here would reproduce the original defect one layer up --
    a number where there is no measurement.
    """

    return currency.freshness()


def confidence_label_for(label: str, currency: EvidenceCurrency) -> str:
    """Cap a persisted confidence label by what the evidence still supports.

    A confidence score is frozen at publish time; it cannot know that the
    evidence under it later expired. A claim whose support is not current can
    never continue to read ``high``, so it collapses to
    :data:`UNSUPPORTED_CONFIDENCE_LABEL`. A current claim is returned untouched --
    this only ever removes unearned confidence, never adds any.
    """

    if currency.current:
        return label
    return UNSUPPORTED_CONFIDENCE_LABEL


def currency_for(
    anchor_kind: str,
    anchor_id: int | None,
    index: Mapping[tuple[str, int], EvidenceCurrency] | None,
) -> EvidenceCurrency:
    """Look a verdict up by anchor, failing closed to :data:`UNCHECKED`.

    A missing index (a caller that has not been threaded a clock yet) and an
    absent anchor both yield ``UNCHECKED`` rather than a current verdict, so an
    un-updated call site degrades to "cannot assert currency" instead of
    silently re-acquiring the old always-fresh behaviour.
    """

    if index is None or anchor_id is None:
        return UNCHECKED
    return index.get((anchor_kind, anchor_id), UNCHECKED)


@dataclass(frozen=True, slots=True)
class CurrencyContext:
    """The clock a read surface was given, plus the verdicts it resolved.

    Threading two loose values (``now`` and an index mapping) through a dozen
    serializers is how one of them quietly keeps the old always-fresh behaviour.
    Bundling them means a serializer either has the context or does not, and the
    "does not" case is :data:`NO_CURRENCY` -- which resolves every anchor to
    :data:`UNCHECKED` rather than to "current".

    Consistent with the adviser's ``build_candidate`` (F008 S5): supplying no
    index does **not** mean current. It means we cannot assert currency, which
    for a free claim is refused just as firmly as expiry.
    """

    #: ``(anchor_kind, anchor_id) -> verdict``. ``None`` means no clock reached
    #: this call site, which fails closed rather than reading as fresh.
    index: Mapping[tuple[str, int], EvidenceCurrency] | None = None
    #: The moment the verdicts were computed against. Carried for explanation
    #: and for tests that need to state the clock they read at; never used to
    #: recompute, so it cannot drift from ``index``.
    now: datetime | None = None

    def for_version(self, version_id: int | None) -> EvidenceCurrency:
        """The verdict for one offer version, failing closed when absent."""

        return currency_for(ANCHOR_OFFER_VERSION, version_id, self.index)

    def for_versions(self, version_ids: Sequence[int | None]) -> EvidenceCurrency:
        """The *least* current verdict across several versions.

        Used for provider-level rollups: a provider is only as current as its
        stalest published claim. An empty set is :data:`UNCHECKED`, not current --
        absence of claims is not evidence of freshness.
        """

        return worst([self.for_version(vid) for vid in version_ids])


#: The context a caller that has not been threaded a clock gets. Every anchor
#: resolves to :data:`UNCHECKED`, so an un-updated call site degrades to "cannot
#: assert currency" instead of silently re-acquiring the always-fresh behaviour.
NO_CURRENCY = CurrencyContext()


__all__: Sequence[str] = (
    "ANCHOR_COVERAGE_DECLARATION",
    "ANCHOR_OFFER_VERSION",
    "NO_CURRENCY",
    "UNCHECKED",
    "UNSUPPORTED_CONFIDENCE_LABEL",
    "CurrencyContext",
    "EvidenceCurrency",
    "assess_currency",
    "confidence_label_for",
    "currency_for",
    "freshness_or_none",
    "is_publishable_free_claim",
    "worst",
)
