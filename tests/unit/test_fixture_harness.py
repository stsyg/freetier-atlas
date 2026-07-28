"""The shared fixture harness and the profile registration seam (F008 S3).

Two things are proved here.

**The harness** (:mod:`tests.support.fixtures`) drives every committed extraction
case for every provider/adapter from data alone -- adding a provider fixture adds
test coverage without adding test code, which is what lets six provider slices
ship data only.

**The registration seam** (:mod:`app.ingest.adapters.profiles`) is the reason
those six slices can be built *concurrently*. The decisive test is
:func:`test_a_new_provider_profile_registers_without_editing_any_shared_file`: a
throwaway provider module is discovered and registered while the bytes of every
shared file are asserted unchanged. If that ever fails, two concurrent provider
slices would be editing the same file and the parallelism is unsafe.
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from app.ingest.adapters import profiles as profiles_pkg
from app.ingest.adapters.html import HTML_EXTRACTION_PROFILES, resolve_profile
from app.ingest.adapters.profiles import (
    ProfileConflictError,
    load_provider_profiles,
    provider_profile_modules,
    register_html_profile,
    registered_profile_names,
)
from app.ingest.adapters.structured import JSON_EXTRACTION_PROFILES
from app.ingest.runner import fixture_mime_for

from tests.support import fixtures as harness

# Adapters whose fixtures declare their own extraction profile in expected.json.
_PROFILE_OVERRIDES = {("example", "structured"): "offer_api"}

#: Files a provider slice must NOT have to edit. Their bytes are pinned by the
#: conflict-surface probe below.
SHARED_FILES = (
    Path(profiles_pkg.__file__),
    Path(profiles_pkg.__file__).parent.parent / "html.py",
    Path(profiles_pkg.__file__).parent.parent / "structured.py",
    Path(profiles_pkg.__file__).parent.parent / "__init__.py",
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _extraction_cases() -> list[tuple[str, str, str]]:
    """Every committed extraction case, discovered from the fixture tree."""

    found: list[tuple[str, str, str]] = []
    for provider_dir in sorted(p for p in harness.FIXTURE_ROOT.iterdir() if p.is_dir()):
        for adapter_dir in sorted(p for p in provider_dir.iterdir() if p.is_dir()):
            if adapter_dir.name == "mcp":
                continue  # MCP is driven by its own offline client, not a URL fetch
            for case in harness.available_cases(provider_dir.name, adapter_dir.name):
                found.append((provider_dir.name, adapter_dir.name, case))
    return found


# --- Case vocabulary --------------------------------------------------------


def test_case_vocabulary_covers_the_documented_seven_cases() -> None:
    assert harness.CASES == (
        "unchanged",
        "changed",
        "partial",
        "malformed",
        "contradictory",
        "withdrawn",
        "stale",
    )
    # The two F008 additions are pipeline cases, not single-document shapes.
    assert harness.PIPELINE_CASES == ("withdrawn", "stale")
    assert set(harness.EXTRACTION_CASES) | set(harness.PIPELINE_CASES) == set(harness.CASES)
    assert not set(harness.EXTRACTION_CASES) & set(harness.PIPELINE_CASES)


def test_available_cases_is_deterministically_ordered() -> None:
    """Vocabulary cases first (in CASES order), then source-id-named ones."""

    assert harness.available_cases("example", "html") == (
        "unchanged",
        "changed",
        "partial",
        "malformed",
        "contradictory",
    )
    # A real provider names its fixtures after the source id it serves.
    assert harness.available_cases("cloudflare", "html") == (
        "cloudflare-pages-limits",
        "cloudflare-workers-limits",
    )
    assert harness.available_cases("no-such-provider", "html") == ()


# --- Data-driven extraction -------------------------------------------------


def test_the_committed_corpus_is_non_trivial() -> None:
    """Guard against the parametrisation silently collapsing to nothing."""

    cases = _extraction_cases()
    assert len(cases) >= 12
    assert {p for p, _, _ in cases} >= {"example", "cloudflare"}
    assert {a for _, a, _ in cases} >= {"html", "rss", "structured"}


@pytest.mark.parametrize(("provider", "adapter", "case"), _extraction_cases())
def test_every_committed_extraction_case_matches_its_expectation(
    provider: str, adapter: str, case: str
) -> None:
    """One test body, every fixture: a provider slice adds only data."""

    harness.run_extraction_case(
        provider, adapter, case, profile=_PROFILE_OVERRIDES.get((provider, adapter))
    )


def test_extraction_is_deterministic_across_repeat_loads() -> None:
    first = harness.content_hashes(
        harness.run_extraction_case("cloudflare", "html", "cloudflare-workers-limits")[1]
    )
    second = harness.content_hashes(
        harness.run_extraction_case("cloudflare", "html", "cloudflare-workers-limits")[1]
    )
    assert first == second
    assert all(len(h) == 64 for h in first)


def test_load_case_rejects_a_missing_case() -> None:
    with pytest.raises(harness.FixtureCaseError):
        harness.load_case("example", "html", "no-such-case")


def test_load_case_rejects_an_unknown_adapter_directory() -> None:
    with pytest.raises(harness.FixtureCaseError):
        harness.load_case("example", "not-an-adapter", "unchanged")


def test_a_profile_driven_case_without_a_profile_is_an_error_not_a_default() -> None:
    """Guessing which table to read would fabricate facts, so it is refused."""

    case = harness.load_case("example", "structured", "unchanged")
    assert case.profile is None
    with pytest.raises(harness.FixtureCaseError, match="extraction profile"):
        harness.build_fixture_adapter(case)


def test_the_harness_serves_the_mime_the_production_runner_would_serve() -> None:
    assert harness.load_case("example", "html", "unchanged").mime == "text/html"
    assert harness.load_case("example", "rss", "unchanged").mime == "application/rss+xml"
    assert harness.load_case("example", "structured", "unchanged").mime == "application/json"
    # ...and that is the runner's own function, not a duplicated table.
    assert harness.load_case("example", "rss", "unchanged").mime == fixture_mime_for("rss", "xml")


# --- The registration seam --------------------------------------------------


def test_cloudflare_profiles_come_from_their_own_provider_module() -> None:
    from app.ingest.adapters.profiles import cloudflare as cloudflare_module

    assert "cloudflare" in provider_profile_modules()
    owned = {
        "cloudflare_workers_limits": cloudflare_module.CLOUDFLARE_WORKERS_LIMITS,
        "cloudflare_pages_limits": cloudflare_module.CLOUDFLARE_PAGES_LIMITS,
    }
    for name, profile in owned.items():
        # The object the pipeline resolves IS the one the provider module owns.
        assert HTML_EXTRACTION_PROFILES[name] is profile
        assert resolve_profile(name) is profile


def test_cloudflare_extraction_is_unchanged_by_the_move() -> None:
    """The relocation is behaviour-neutral: identical facts, identical hashes."""

    _, workers = harness.run_extraction_case("cloudflare", "html", "cloudflare-workers-limits")
    _, pages = harness.run_extraction_case("cloudflare", "html", "cloudflare-pages-limits")
    assert len(workers) == 1
    assert len(pages) == 1
    assert workers[0].facts["service"] == "Cloudflare Workers"
    assert pages[0].facts["service"] == "Cloudflare Pages"
    # Content hashes are what the change-detection pipeline compares.
    assert harness.content_hashes(workers) != harness.content_hashes(pages)


def test_generic_html_profiles_stay_in_the_generic_module() -> None:
    """Only provider-agnostic shapes are literals in the shared adapter module."""

    html_path = Path(profiles_pkg.__file__).parent.parent / "html.py"
    html_source = html_path.read_text(encoding="utf-8")
    generic = {"quota_document", "pricing_document"}
    assert generic <= set(HTML_EXTRACTION_PROFILES)
    for name in generic:
        assert f'name="{name}"' in html_source
        assert resolve_profile(name).name == name


def test_registering_a_duplicate_name_fails_loudly(_isolated_registries: None) -> None:
    profile = HTML_EXTRACTION_PROFILES["cloudflare_workers_limits"]
    # Re-registering the identical object is a harmless no-op...
    assert register_html_profile(profile) is profile
    # ...but a different profile under a taken name is a conflict, not a shadow.
    clone = type(profile)(**{**profile.__dict__})
    with pytest.raises(ProfileConflictError, match="already registered"):
        register_html_profile(clone)


def test_registered_profile_names_reports_all_three_registries() -> None:
    names = registered_profile_names()
    assert set(names) == {"html", "json", "mcp"}
    assert "cloudflare_workers_limits" in names["html"]
    assert set(names["json"]) == set(JSON_EXTRACTION_PROFILES)


def test_load_provider_profiles_is_idempotent() -> None:
    before = registered_profile_names()
    load_provider_profiles()
    load_provider_profiles()
    assert registered_profile_names() == before


@pytest.fixture
def _isolated_registries() -> Iterator[None]:
    """Snapshot/restore the profile registries so a probe cannot leak."""

    saved = {k: dict(v) for k, v in _REGISTRIES.items()}
    try:
        yield
    finally:
        for key, registry in _REGISTRIES.items():
            registry.clear()
            registry.update(saved[key])


_REGISTRIES = {"html": HTML_EXTRACTION_PROFILES, "json": JSON_EXTRACTION_PROFILES}


# --- THE CONFLICT-SURFACE PROBE ---------------------------------------------


def test_a_new_provider_profile_registers_without_editing_any_shared_file(
    tmp_path: Path, _isolated_registries: None
) -> None:
    """Adding a provider = adding ONE file. Nothing shared is touched.

    This is the property that makes six concurrent provider slices safe. The
    probe drops a throwaway provider module into the package's search path,
    lets auto-discovery find it, and then asserts that every shared file's bytes
    are byte-for-byte what they were before.
    """

    before = {path: _digest(path) for path in SHARED_FILES}

    module_name = "throwaway_probe_provider"
    (tmp_path / f"{module_name}.py").write_text(
        "from app.ingest.adapters.html import HtmlColumn, HtmlExtractionProfile\n"
        "from app.ingest.adapters.profiles import register_html_profile\n"
        "\n"
        "PROBE = register_html_profile(\n"
        "    HtmlExtractionProfile(\n"
        "        name='throwaway_probe_limits',\n"
        "        table_id='probe-free-tier',\n"
        "        columns={\n"
        "            'service': HtmlColumn('service', 'text'),\n"
        "            'offer type': HtmlColumn('offer_type', 'text'),\n"
        "        },\n"
        "    )\n"
        ")\n",
        encoding="utf-8",
    )

    original_path = list(profiles_pkg.__path__)
    profiles_pkg.__path__.append(str(tmp_path))
    try:
        assert module_name in provider_profile_modules()
        loaded = load_provider_profiles()
        assert f"app.ingest.adapters.profiles.{module_name}" in loaded

        # The new profile is live everywhere the pipeline looks for one.
        assert "throwaway_probe_limits" in HTML_EXTRACTION_PROFILES
        assert resolve_profile("throwaway_probe_limits").table_id == "probe-free-tier"
        assert "throwaway_probe_limits" in registered_profile_names()["html"]
    finally:
        profiles_pkg.__path__[:] = original_path
        sys.modules.pop(f"app.ingest.adapters.profiles.{module_name}", None)

    # ...and not one shared file changed to make that happen.
    assert {path: _digest(path) for path in SHARED_FILES} == before


def test_the_seam_touches_no_shared_file_for_the_existing_provider_either() -> None:
    """`html.py` must not name any provider -- otherwise it is a merge surface."""

    html_source = (Path(profiles_pkg.__file__).parent.parent / "html.py").read_text(
        encoding="utf-8"
    )
    assert "cloudflare" not in html_source.lower()
