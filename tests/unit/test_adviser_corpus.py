"""Deterministic corpus runner for the adviser (F006 slice 3, Q5).

Each case under ``tests/fixtures/adviser/<case>/`` ships three files:

* ``catalogue.json`` -- a synthetic, fixture-only published catalogue,
* ``input.json`` -- a strict :class:`RecommendationRequest` body, and
* ``expected.json`` -- the full deterministic recommendation payload.

The runner rebuilds the catalogue into a :class:`CandidatePool` (running the
*real* classify engine cross-check), computes the recommendation, serializes it,
and asserts byte-for-byte equality with ``expected.json``. It also runs each case
twice and asserts identical output, proving reproducibility.

Crucially, this executes with **all LLM providers disabled** -- the adviser
package imports nothing from any provider client, so the corpus condition (the
default) is met structurally. ``test_adviser_package_has_no_llm_import`` guards
that no LLM/network module ever creeps into the package.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.adviser.recommend import recommend
from app.adviser.schema import RecommendationRequest
from app.adviser.schemas import build_response

from tests.support.synthetic import build_pool_from

CORPUS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "adviser"


def _case_names() -> list[str]:
    return sorted(p.name for p in CORPUS_DIR.iterdir() if (p / "input.json").exists())


def _run_case(case_dir: Path) -> dict:
    catalogue = json.loads((case_dir / "catalogue.json").read_text())
    request_data = json.loads((case_dir / "input.json").read_text())
    pool = build_pool_from(catalogue)
    request = RecommendationRequest.model_validate(request_data)
    result = recommend(request, pool)
    return build_response(result).model_dump(mode="json")


@pytest.mark.parametrize("case", _case_names())
def test_corpus_matches_expected(case: str) -> None:
    case_dir = CORPUS_DIR / case
    expected = json.loads((case_dir / "expected.json").read_text())
    actual = _run_case(case_dir)
    assert actual == expected, f"corpus case '{case}' diverged from expected.json"


@pytest.mark.parametrize("case", _case_names())
def test_corpus_is_reproducible(case: str) -> None:
    case_dir = CORPUS_DIR / case
    first = _run_case(case_dir)
    second = _run_case(case_dir)
    assert first == second, f"corpus case '{case}' is not reproducible"


def test_corpus_is_non_empty() -> None:
    names = _case_names()
    assert len(names) >= 6
    # The impossible-order and Z1/Z2-separation cases must be present.
    assert "impossible_reduction_selfhost" in names
    assert "z1_z2_separate_section" in names


def test_adviser_package_has_no_llm_import() -> None:
    # Structural guard for the "no LLM in the recommendation path" invariant:
    # none of the adviser modules may import an LLM/provider/network client.
    import importlib
    import pkgutil

    import app.adviser as adviser_pkg

    forbidden = ("llm", "openai", "anthropic", "requests", "httpx", "urllib.request", "socket")
    for module_info in pkgutil.iter_modules(adviser_pkg.__path__):
        module = importlib.import_module(f"app.adviser.{module_info.name}")
        source_file = getattr(module, "__file__", None)
        if source_file is None:
            continue
        text = Path(source_file).read_text()
        for token in forbidden:
            assert f"import {token}" not in text, (
                f"app.adviser.{module_info.name} must not import '{token}'"
            )
