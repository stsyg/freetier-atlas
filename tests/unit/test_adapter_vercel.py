"""Offline integrity and safety controls for the Vercel provider slice."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from app.classify.engine import OfferFacts, classify
from app.config import load_and_validate
from app.ingest import resolve_profile
from app.ingest.adapters.html import _DocumentCollector, _header_row

from tests.support.fixtures import available_cases, load_case, run_extraction_case

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "vercel.example.yaml"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "ingest" / "vercel" / "html"
SOURCE_CASES = ("vercel-hobby-plan", "vercel-sandbox-pricing", "vercel-pro-trial")
PROFILE_NAMES = ("vercel_hobby_plan", "vercel_sandbox_pricing", "vercel_pro_trial")
SYNTHETIC_IDS = ("vercel-hobby-offers", "vercel-blob-hobby")


@pytest.mark.parametrize("case", available_cases("vercel", "html"))
def test_every_vercel_fixture_extracts_exactly_as_declared(case: str) -> None:
    run_extraction_case("vercel", "html", case, official_domains=("vercel.com",))


def test_the_five_document_cases_and_three_official_sources_are_present() -> None:
    cases = set(available_cases("vercel", "html"))
    assert {"unchanged", "changed", "partial", "malformed", "contradictory"} <= cases
    assert set(SOURCE_CASES) <= cases


@pytest.mark.parametrize("case", SOURCE_CASES)
def test_capture_structure_matches_every_retained_target_cell(case: str) -> None:
    fixture = load_case("vercel", "html", case)
    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))
    collector = _DocumentCollector()
    collector.feed(fixture.content.decode("utf-8"))
    collector.close()
    profile = resolve_profile(fixture.profile)

    matches = []
    for table in collector.tables:
        header_index, header = _header_row(table)
        if header_index is None:
            continue
        normalized = {cell.strip().lower() for cell in header.cells}
        if set(profile.header_signature) <= normalized:
            matches.append((table, header_index, header))
    assert len(matches) == 1
    table, header_index, header = matches[0]
    assert list(header.cells) == capture["structure"]["headers"]
    assert [list(row.cells) for row in table.rows[header_index + 1 :]] == capture["structure"][
        "rows"
    ]
    assert capture["target_table_rows_removed"] == []
    assert capture["target_table_cells_removed"] == []


@pytest.mark.parametrize("case", SOURCE_CASES)
def test_asserted_blocks_match_the_pinned_capture_hashes(case: str) -> None:
    fixture = load_case("vercel", "html", case)
    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))
    profile = resolve_profile(fixture.profile)
    actual = [
        hashlib.sha256(assertion.text.encode("utf-8")).hexdigest()
        for assertion in profile.assertions
    ]
    assert actual == capture["structure"]["asserted_block_sha256"]


@pytest.mark.parametrize("name", PROFILE_NAMES)
def test_profiles_map_every_live_row_and_never_assert_z0_gate_facts(name: str) -> None:
    profile = resolve_profile(name)
    assert profile.mode == "matrix"
    assert profile.header_signature
    assert profile.ignored_matrix_rows == ()
    assert all(row.required for row in profile.matrix_rows.values())
    assert not {"requires_card", "has_paid_dependencies"} & {
        assertion.field for assertion in profile.assertions
    }


@pytest.mark.parametrize("case", SOURCE_CASES)
def test_every_candidate_keeps_both_material_z0_gates_unknown(case: str) -> None:
    _, (candidate,) = run_extraction_case("vercel", "html", case, official_domains=("vercel.com",))
    assert "requires_card" not in candidate.facts
    assert "has_paid_dependencies" not in candidate.facts
    result = classify(
        OfferFacts(
            offer_type=str(candidate.facts["offer_type"]),
            requires_card=None,
            has_paid_dependencies=None,
            exhaustion_behaviours=(str(candidate.facts["exhaustion_behaviour"]),),
        )
    )
    assert result.zero_cost_class != "Z0_TRUE_FREE"
    assert any("payment card" in reason for reason in result.blocking_conditions)


def test_pro_trial_is_the_deliberate_evidence_backed_non_z0_control() -> None:
    _, (candidate,) = run_extraction_case(
        "vercel", "html", "vercel-pro-trial", official_domains=("vercel.com",)
    )
    assert candidate.facts["offer_type"] == "trial"
    assert candidate.facts["image_transformations"] == "Max. 10K/month"
    assert candidate.facts["notes"].startswith("Your trial finishes after 14 days")
    verdict = classify(
        OfferFacts(
            offer_type="trial",
            requires_card=None,
            has_paid_dependencies=None,
            exhaustion_behaviours=("hard_stop",),
        )
    )
    assert verdict.zero_cost_class != "Z0_TRUE_FREE"


def test_config_sources_and_q9a_q10a_declarations_are_complete() -> None:
    config = load_and_validate(CONFIG_PATH)
    assert [source.id for source in config.sources] == [
        "vercel-hobby-plan",
        "vercel-sandbox-pricing",
        "vercel-pro-trial",
    ]
    assert len(config.coverage) == 14
    assert (
        sum(
            entry.state in {"verified_free", "offered_no_z0"}
            and bool(entry.source or entry.evidence_url)
            for entry in config.coverage.values()
        )
        >= 3
    )
    assert set(config.service_categories) == {
        "Vercel Hobby",
        "Vercel Sandbox",
        "Vercel Pro Trial",
    }
    text = CONFIG_PATH.read_text(encoding="utf-8")
    block = text.split("service_categories:", 1)[1].split("\ncoverage:", 1)[0]
    lines = [line for line in block.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#") or ":" not in line:
            continue
        assert lines[index - 1].lstrip().startswith("#")


def test_old_pr50_synthetic_table_ids_are_absent() -> None:
    paths = [REPO_ROOT / "apps" / "api" / "app" / "ingest" / "adapters" / "profiles" / "vercel.py"]
    paths.extend(FIXTURE_ROOT.glob("*/source.html"))
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert all(synthetic_id not in corpus for synthetic_id in SYNTHETIC_IDS)


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("extra_column", None),
        ("reordered_columns", None),
        ("renamed_tier", "table_not_found"),
        ("duplicated_table", "ambiguous_table"),
        ("rowspan", "irregular_row_width"),
        ("mapped_row_removed", "missing_matrix_rows"),
        ("whitespace_entities", None),
    ],
)
def test_predicted_structural_mutations_match_observation(
    mutation: str, expected_error: str | None
) -> None:
    """Prediction recorded in the parametrization before each mutation is run."""

    fixture = load_case("vercel", "html", "vercel-sandbox-pricing")
    source = fixture.source_path.read_text(encoding="utf-8")
    if mutation == "extra_column":
        before_body, body = source.split("<tbody>", 1)
        before_body = before_body.replace(
            "<th>Enterprise (Per month)</th>",
            "<th>Extra</th><th>Enterprise (Per month)</th>",
        )
        source = before_body + "<tbody>" + body.replace("</tr>", "<td>x</td></tr>")
    elif mutation == "reordered_columns":
        source = source.replace(
            "<th>Hobby (Included)</th><th>Pro (Per month)</th>",
            "<th>Pro (Per month)</th><th>Hobby (Included)</th>",
        )
        source = source.replace(
            "<td>5 hours/month</td><td>$0.128/hour</td>",
            "<td>$0.128/hour</td><td>5 hours/month</td>",
        )
    elif mutation == "renamed_tier":
        source = source.replace("Hobby (Included)", "Hobby Included")
    elif mutation == "duplicated_table":
        table = source.split("<table>", 1)[1].split("</table>", 1)[0]
        source = source.replace("</table>", f"</table><table>{table}</table>", 1)
    elif mutation == "rowspan":
        source = source.replace(
            "<td>Sandbox Active CPU</td>", '<td rowspan="2">Sandbox Active CPU</td>'
        )
    elif mutation == "mapped_row_removed":
        row_label = source.index("<td>Sandbox Active CPU</td>")
        start = source.rfind("<tr>", 0, row_label)
        end = source.index("</tr>", start) + len("</tr>")
        source = source[:start] + source[end:]
    else:
        source = source.replace("Sandbox Active CPU", "  Sandbox&nbsp;Active CPU  ")

    from tests.support.fixtures import build_fixture_adapter

    adapter = build_fixture_adapter(fixture, official_domains=("vercel.com",), body=source.encode())
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.facts.get("error") == expected_error
