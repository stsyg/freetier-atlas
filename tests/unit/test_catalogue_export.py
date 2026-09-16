"""DB-free unit tests for the catalogue static export (F009-S2, slice 1).

These cover the pure surface of :mod:`app.export.catalogue`: the ``as_of``
stamping (invariant 1), the "unknown stays unknown" guarantee carried through the
envelope (invariant 3), and the canonical/deterministic rendering (invariant 5).
The full session-bound build, route parity, the staleness boundary flip
(invariant 2) and the per-source window split (invariant 4) are exercised against
a real database in ``tests/integration/test_catalogue_export.py``.

Each test asserts a discriminating fact -- something that flips if the code under
test is broken -- and the unknown-stays-unknown test is paired with a positive
control so a verdict of "unknown" cannot be an instrument stuck at one value.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from app.export import catalogue
from app.export.catalogue import canonical_json, render_export, write_export
from app.read_api import service
from app.read_api.currency import (
    ANCHOR_OFFER_VERSION,
    UNCHECKED,
    CurrencyContext,
    assess_currency,
)

from tests.support.synthetic import build_catalogue

_AS_OF = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Canonical, deterministic rendering (invariant 5)                            #
# --------------------------------------------------------------------------- #


def test_canonical_json_sorts_keys_and_terminates_with_newline() -> None:
    text = canonical_json({"b": 1, "a": 2})
    assert text == '{\n  "a": 2,\n  "b": 1\n}\n'
    assert text.endswith("\n")


def test_canonical_json_preserves_non_ascii() -> None:
    # ensure_ascii=False keeps a literal accented character rather than a
    # ``\u00e9`` escape, so a provider name round-trips a static host verbatim.
    assert "Frankfurt-Rhône" in canonical_json({"name": "Frankfurt-Rhône"})


def test_render_export_is_byte_identical_regardless_of_insertion_order() -> None:
    first = {"catalogue/b.json": {"y": 1, "x": 2}, "manifest.json": {"n": 1}}
    second = {"manifest.json": {"n": 1}, "catalogue/b.json": {"x": 2, "y": 1}}
    assert render_export(first) == render_export(second)


def test_render_export_orders_paths_deterministically() -> None:
    rendered = render_export(
        {"catalogue/z.json": {"a": 1}, "catalogue/a.json": {"a": 1}, "manifest.json": {"a": 1}}
    )
    assert list(rendered) == ["catalogue/a.json", "catalogue/z.json", "manifest.json"]


# --------------------------------------------------------------------------- #
# as_of stamping on every artefact (invariant 1)                              #
# --------------------------------------------------------------------------- #


def test_envelope_stamps_as_of_and_metadata() -> None:
    envelope = catalogue._envelope(_AS_OF, "/catalogue/providers", ["body"])
    assert envelope["as_of"] == "2026-01-15T12:00:00+00:00"
    assert envelope["snapshot_version"] == catalogue.SNAPSHOT_VERSION
    assert envelope["generator"] == catalogue.GENERATOR
    assert envelope["route"] == "/catalogue/providers"
    assert envelope["kind"] == "route"
    assert envelope["data"] == ["body"]
    # A plain route artefact carries no corpus note.
    assert "note" not in envelope


def test_envelope_rejects_naive_as_of() -> None:
    # A snapshot's currency clock must be unambiguous; a naive datetime could be
    # any zone, so it is refused rather than silently assumed to be UTC.
    with pytest.raises(ValueError, match="timezone-aware"):
        catalogue._envelope(datetime(2026, 1, 15, 12, 0), "/catalogue/providers", [])


def test_corpus_envelope_carries_kind_and_note() -> None:
    envelope = catalogue._envelope(
        _AS_OF, "/catalogue/search", {"results": []}, kind="corpus", note="a corpus"
    )
    assert envelope["kind"] == "corpus"
    assert envelope["note"] == "a corpus"


# --------------------------------------------------------------------------- #
# Writing artefacts to disk (determinism across platforms)                    #
# --------------------------------------------------------------------------- #


def test_write_export_uses_lf_newlines_and_creates_nested_dirs(tmp_path) -> None:
    written = write_export(
        {"catalogue/offers/7.json": {"a": 1}, "manifest.json": {"a": 1}}, tmp_path
    )
    # Returned paths are the deterministic, sorted set actually written.
    assert written == ["catalogue/offers/7.json", "manifest.json"]
    nested = tmp_path / "catalogue" / "offers" / "7.json"
    assert nested.is_file()
    raw = nested.read_bytes()
    assert b"\r\n" not in raw  # LF only, even on Windows
    assert raw.endswith(b"\n")


# --------------------------------------------------------------------------- #
# Unknown stays unknown through the export pipeline (invariant 3)             #
# --------------------------------------------------------------------------- #


def _synthetic_published_offer(*, evidence_age_days: int | None):
    """A single published Z0 offer whose evidence currency the caller controls.

    ``evidence_age_days=None`` models the declaration-only shape: evidence with
    no checkable fetch time, which must resolve to *unchecked*, never current.
    """

    data = {
        "providers": [
            {
                "id": 1,
                "slug": "acme",
                "name": "Acme",
                "services": [
                    {
                        "id": 1,
                        "canonical_name": "Compute",
                        "offers": [
                            {
                                "id": 7,
                                "zero_cost_class": "Z0_TRUE_FREE",
                                "version": {
                                    "version_number": 1,
                                    "evidence_age_days": evidence_age_days,
                                    "evidence": [{"official": True, "url": "https://example/x"}],
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }
    offer = next(o for o in build_catalogue(data).offers if o.id == 7)
    offer.status = "published"
    return offer


def test_unknown_evidence_stays_unknown_through_export_pipeline() -> None:
    offer = _synthetic_published_offer(evidence_age_days=None)
    version_id = offer.versions[0].id
    unknown = CurrencyContext(index={(ANCHOR_OFFER_VERSION, version_id): UNCHECKED}, now=_AS_OF)

    body = service.serialize_offer_detail(offer, {}, unknown).model_dump(mode="json")
    envelope = catalogue._envelope(_AS_OF, "/catalogue/offers/7", body)
    # Round-trip through the real serialization path a static host would read.
    reparsed = json.loads(canonical_json(envelope))
    data = reparsed["data"]

    # The exported DATA itself encodes non-currency: a renderer cannot present
    # this as fresh without re-deriving currency (which it must not do).
    assert data["evidence_currency"]["current"] is False
    assert data["evidence_currency"]["checked"] is False
    assert data["confidence_label"] == "unknown"
    assert data["freshness"] is None  # absent measurement, never 0.0
    # And the artefact still carries the snapshot clock.
    assert reparsed["as_of"] == "2026-01-15T12:00:00+00:00"


def test_fresh_evidence_is_current_positive_control() -> None:
    # The positive control for the test above: identical pipeline, but evidence
    # fetched just now. If this did NOT read as current, the unknown assertion
    # above would prove nothing (a stuck instrument).
    offer = _synthetic_published_offer(evidence_age_days=0)
    version_id = offer.versions[0].id
    fresh = CurrencyContext(
        index={(ANCHOR_OFFER_VERSION, version_id): assess_currency(_AS_OF, _AS_OF, "daily")},
        now=_AS_OF,
    )

    body = service.serialize_offer_detail(offer, {}, fresh).model_dump(mode="json")
    assert body["evidence_currency"]["current"] is True
    assert body["evidence_currency"]["checked"] is True
    assert body["freshness"] is not None
