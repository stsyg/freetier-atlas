"""Contract tests for the Oracle Cloud provider coverage declaration.

The declaration's job is to be honest in BOTH directions: it may not claim a
category is free without evidence, and it may not claim a category is absent
without evidence either.

Oracle makes both halves unusually easy to get wrong, and in opposite ways.

It publishes a comprehensive **Always Free** enumeration on two independent
official documents, so nine of the fourteen categories really are evidenced --
far more than any other provider in this repository. Declaring those `unknown`
out of caution would be the *omission* failure: a wrongly-omitted free offer is
a defect too.

And because that enumeration looks exhaustive, it is tempting to read the five
categories it omits as `not_offered`. That would be the *unsupported absence*
failure. Oracle's own FAQ says "As new Always Free services become available, you
will automatically be able to use those as well", so the list is a current state
rather than a closed set. All five stay `unknown`.

Nothing is `verified_free`, and that is the finding rather than an omission: a
payment card is part of obtaining the account that carries every one of these
offers, and Oracle states so verbatim.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from app.config import ConfigError, load_and_validate
from app.config.models import ProviderConfig
from app.read_api.taxonomy import canonical_slugs

REPO_ROOT = Path(__file__).resolve().parents[2]
ORACLE_CONFIG = REPO_ROOT / "config" / "examples" / "providers" / "oracle.example.yaml"

EXPECTED: dict[str, tuple[str, str | None]] = {
    "compute-vms": ("offered_no_z0", "oracle-always-free-resources"),
    "containers-app-hosting": ("unknown", None),
    "serverless-functions": ("unknown", None),
    "relational-databases": ("offered_no_z0", "oracle-mysql-heatwave-always-free"),
    "nosql-key-value": ("offered_no_z0", "oracle-always-free-resources"),
    "object-file-storage": ("offered_no_z0", "oracle-always-free-resources"),
    "networking-cdn-dns": ("offered_no_z0", "oracle-always-free-resources"),
    "queues-messaging-jobs": ("offered_no_z0", "oracle-always-free-resources"),
    "auth-identity": ("unknown", None),
    "cicd-source-control": ("unknown", None),
    "monitoring-logs-tracing": ("offered_no_z0", "oracle-always-free-resources"),
    "ai-inference-embeddings": ("unknown", None),
    "email-notifications-comms": ("offered_no_z0", "oracle-always-free-resources"),
    "secrets-config-devtools": ("offered_no_z0", "oracle-always-free-resources"),
}

SOURCE_IDS = [
    "oracle-always-free-resources",
    "oracle-free-tier",
    "oracle-always-free-services",
    "oracle-cloud-free-tier",
    "oracle-free-credit-promotion",
    "oracle-mysql-heatwave-always-free",
]


def _load() -> ProviderConfig:
    model = load_and_validate(ORACLE_CONFIG)
    assert isinstance(model, ProviderConfig)
    return model


def test_oracle_loads_with_six_official_sources_over_six_documents() -> None:
    model = _load()
    raw = yaml.safe_load(ORACLE_CONFIG.read_text(encoding="utf-8"))

    assert [source["id"] for source in raw["sources"]] == SOURCE_IDS
    assert all(source.trust_level == "official" for source in model.sources)
    assert all(
        (source.url or "").startswith(("https://www.oracle.com/", "https://docs.oracle.com/"))
        for source in model.sources
    )
    documents = {(source.url or "").split("#", 1)[0] for source in model.sources}
    assert len(documents) == 6
    assert len(model.sources) == 6
    # Both official hosts are used, and only those two.
    assert set(model.provider.official_domains) == {"www.oracle.com", "docs.oracle.com"}


def test_the_evidence_is_split_across_both_official_hosts() -> None:
    """A slice resting entirely on marketing copy would be weaker than this one.

    Three sources are Oracle Cloud Infrastructure DOCUMENTATION and three are
    the public marketing/FAQ host. The perpetuity statement and the card
    requirement are each evidenced on both, independently.
    """

    model = _load()
    docs = [s for s in model.sources if "docs.oracle.com" in (s.url or "")]
    www = [s for s in model.sources if "www.oracle.com" in (s.url or "")]
    assert len(docs) == 3
    assert len(www) == 3


def test_oracle_declares_exact_canonical_state_and_provenance_map() -> None:
    model = _load()

    assert tuple(model.coverage) == canonical_slugs()
    assert len(model.coverage) == 14
    assert {slug: (entry.state, entry.source) for slug, entry in model.coverage.items()} == EXPECTED
    assert sum(entry.state == "offered_no_z0" for entry in model.coverage.values()) == 9
    assert sum(entry.state == "unknown" for entry in model.coverage.values()) == 5
    assert sum(entry.state == "not_offered" for entry in model.coverage.values()) == 0
    assert all(entry.state != "verified_free" for entry in model.coverage.values())
    # Provenance is a declared source in this file, never a bare URL, so every
    # claim traces to a capture with its own capture.json.
    assert all(entry.evidence_url is None for entry in model.coverage.values())
    assert all(entry.rationale for entry in model.coverage.values())


def test_no_category_asserts_absence_without_evidence() -> None:
    """`not_offered` is a positive claim, and this slice can support none of them.

    Oracle's Always Free enumeration omits five categories, which is suggestive
    and is NOT proof. The FAQ says new Always Free services are added over time,
    so the list is a current state, not a closed set.
    """

    model = _load()
    for slug, entry in model.coverage.items():
        assert entry.state != "not_offered", (
            f"{slug}: declaring an Oracle category absent needs an official page proving it, "
            "which this slice did not verify."
        )


@pytest.mark.parametrize(
    "slug",
    [
        "containers-app-hosting",
        "serverless-functions",
        "auth-identity",
        "cicd-source-control",
        "ai-inference-embeddings",
    ],
)
def test_each_unknown_names_what_was_actually_probed(slug: str) -> None:
    """An `unknown` that names no probe is indistinguishable from laziness."""

    model = _load()
    rationale = (model.coverage[slug].rationale or "").lower()
    assert "measured (live)" in rationale
    assert "oracle.com/" in rationale
    assert "unknown" in rationale
    # Each one must say what it looked at AND why the omission is not proof.
    assert "always free" in rationale


def test_the_unknowns_explain_why_omission_is_not_proof_of_absence() -> None:
    model = _load()
    containers = (model.coverage["containers-app-hosting"].rationale or "").lower()
    # The strongest counter-example is named rather than hidden: two entries in
    # Oracle's own list could arguably be read as application hosting.
    assert "apex application development" in containers
    assert "not treated as proof of absence" in containers

    serverless = (model.coverage["serverless-functions"].rationale or "").lower()
    assert "not proof" in serverless
    assert "not_offered" in serverless


@pytest.mark.parametrize(
    "slug",
    [
        "compute-vms",
        "relational-databases",
        "nosql-key-value",
        "object-file-storage",
        "networking-cdn-dns",
        "queues-messaging-jobs",
        "monitoring-logs-tracing",
        "email-notifications-comms",
        "secrets-config-devtools",
    ],
)
def test_each_offered_category_quotes_the_allowance_it_rests_on(slug: str) -> None:
    """An `offered_no_z0` with no quoted allowance is an assertion, not evidence."""

    model = _load()
    entry = model.coverage[slug]
    rationale = entry.rationale or ""
    assert entry.source in SOURCE_IDS
    assert '"' in rationale, f"{slug}: the allowance must be quoted, not summarised"
    assert "no z0 offer is produced" in rationale.lower()


def test_rationales_pin_the_unfavourable_product_truth() -> None:
    model = _load()
    rationales = {slug: (entry.rationale or "").lower() for slug, entry in model.coverage.items()}
    raw_text = ORACLE_CONFIG.read_text(encoding="utf-8").lower()
    # Comments wrap, so compare against the file with comment markers and line
    # breaks collapsed. This normalises FORMAT only; the words still have to be
    # there in order.
    flat = " ".join(line.lstrip().lstrip("#").strip() for line in raw_text.splitlines())
    flat = " ".join(flat.split())

    # The two sentences that decide every Oracle verdict must be quoted in the
    # config, not merely implied by the state values.
    assert "most users need a mobile phone number and a credit card to create an account" in flat
    assert (
        "we use your contact information and credit/debit card information for account setup "
        "and identity verification" in flat
    )

    # The perpetual-but-still-card-gated finding is the most important one here.
    assert "free of charge in the home region of the tenancy" in rationales["compute-vms"]
    assert "perpetual" in rationales["compute-vms"]
    assert "z1_billing_exposure" in rationales["compute-vms"]

    # And the shape of each refusal is stated, not just the verdict.
    assert "absent rather than false" in rationales["compute-vms"]
    assert "unstated on that document" in rationales["nosql-key-value"]

    # The unfavourable side-facts are published rather than dropped.
    assert "incur regular block volume costs" in rationales["object-file-storage"]
    assert "virtual private vaults are not included" in rationales["secrets-config-devtools"]

    # No Z0 claim may hide behind vocabulary: the only place the token may appear
    # is the comment that explains why nothing here uses it.
    assert "state: verified_free" not in raw_text


def test_the_always_free_finding_is_neither_overstated_nor_understated() -> None:
    """Oracle's tier IS perpetual. The config must say so AND must not imply free."""

    model = _load()
    flat = " ".join(
        line.lstrip().lstrip("#").strip()
        for line in ORACLE_CONFIG.read_text(encoding="utf-8").splitlines()
    )
    flat = " ".join(flat.split())
    # Understating would be the omission failure.
    assert "GENUINELY PERPETUAL" in flat
    # Overstating would be the false-claim failure.
    assert "Perpetual is still not Z0" in flat
    for slug, entry in model.coverage.items():
        if entry.state == "offered_no_z0":
            assert "no z0 offer is produced" in (entry.rationale or "").lower(), slug


def test_the_arguable_category_mapping_is_declared_as_arguable() -> None:
    """A taxonomy judgement call must be visible as one."""

    model = _load()
    rationale = model.coverage["queues-messaging-jobs"].rationale or ""
    assert rationale.startswith("ARGUABLE mapping")
    # The mapping must rest on a row this slice actually extracts, quoted.
    assert '"Jobs (concurrent) Job duration: 24 hours"' in rationale
    # It follows a precedent rather than inventing one, and says which.
    assert "AWS" in rationale and "Step Functions" in rationale


def test_provider_rejects_a_duplicate_source_id(tmp_path: Path) -> None:
    raw = yaml.safe_load(ORACLE_CONFIG.read_text(encoding="utf-8"))
    raw["sources"].append(dict(raw["sources"][0]))
    path = tmp_path / "oracle-duplicate-sources.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems)
    assert "provider 'oracle'" in problems
    assert "duplicate source ids: oracle-always-free-resources" in problems


def test_coverage_rejects_an_undeclared_source_reference(tmp_path: Path) -> None:
    raw = yaml.safe_load(ORACLE_CONFIG.read_text(encoding="utf-8"))
    raw["coverage"]["nosql-key-value"]["source"] = "synthetic-oracle-source"
    path = tmp_path / "oracle-bad-source.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems)
    assert "nosql-key-value" in problems
    assert "synthetic-oracle-source" in problems
    assert "not declared in this file" in problems


def test_an_offered_category_without_provenance_is_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load(ORACLE_CONFIG.read_text(encoding="utf-8"))
    raw["coverage"]["nosql-key-value"].pop("source")
    path = tmp_path / "oracle-no-provenance.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems)
    assert "nosql-key-value" in problems
    assert "without provenance" in problems


def test_a_verified_free_claim_would_need_provenance_too(tmp_path: Path) -> None:
    """Guard against the failure this whole slice exists to prevent.

    Nothing here is `verified_free`. This proves the loader would still demand
    provenance if someone later added one, so the absence above is a decision
    rather than a gap the schema would have allowed anyway.
    """

    raw = yaml.safe_load(ORACLE_CONFIG.read_text(encoding="utf-8"))
    raw["coverage"]["compute-vms"] = {"state": "verified_free", "rationale": "unsupported"}
    path = tmp_path / "oracle-unsupported-free.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_and_validate(path)

    problems = "\n".join(excinfo.value.problems)
    assert "compute-vms" in problems
    assert "without provenance" in problems
