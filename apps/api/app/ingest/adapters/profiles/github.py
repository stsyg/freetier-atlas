"""GitHub OFFICIAL free-tier extraction profiles (F008 slice P1).

Provider-specific selectors expressed purely as **data**, registered through the
F008 S3 seam so adding GitHub touches no shared module. Each profile reads one
offer-centric row (one GitHub product on its published free allowance) from a
captured official ``docs.github.com`` snapshot.

Every per-limit value is coerced verbatim as ``text`` (never ``list``) so a real
allowance such as ``2,000`` is captured exactly rather than being split on its
thousands separator. A column that is absent yields ``None`` (UNKNOWN) -- never a
fabricated number: "unknown is better than guessed".

Two conventions are deliberate and load-bearing for the Z0 verdict:

* ``exhaustion behaviour`` records what GitHub does when the free allowance is
  used up **for an account with no payment method on file**. The billing pages
  for Actions, Packages and Codespaces all state verbatim: "If your account does
  not have a valid payment method on file, usage is blocked once you use up your
  quota." That is a safe stop (``hard_stop``), not ``automatic_billing`` -- which
  is precisely why these offers can reach Z0.
* ``offer type`` distinguishes a perpetual allowance (``always_free``) from a
  **time-limited trial** (``trial``). The GitHub Enterprise Cloud trial requires
  no payment method yet expires after 30 days, so it must never be Z0; declaring
  it ``trial`` is what makes the classifier withhold that verdict.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..html import HtmlColumn, HtmlExtractionProfile
from . import register_html_profile

#: Columns every GitHub profile shares: the service identity plus the four
#: material Z0 conditions (offer type, card, paid dependencies, exhaustion).
_IDENTITY_COLUMNS: dict[str, HtmlColumn] = {
    "service": HtmlColumn("service", "text"),
    "offer type": HtmlColumn("offer_type", "text"),
    "card required": HtmlColumn("requires_card", "bool"),
    "paid dependencies": HtmlColumn("has_paid_dependencies", "bool"),
}

GITHUB_ACTIONS_BILLING = register_html_profile(
    HtmlExtractionProfile(
        name="github_actions_billing",
        table_id="github-actions-free-use",
        columns={
            **_IDENTITY_COLUMNS,
            "minutes per month": HtmlColumn("minutes_per_month", "text"),
            "artifact storage": HtmlColumn("artifact_storage", "text"),
            "cache storage per repository": HtmlColumn("cache_storage_per_repository", "text"),
            "public repository usage": HtmlColumn("public_repository_usage", "text"),
            "exhaustion behaviour": HtmlColumn("exhaustion_behaviour", "text"),
        },
        required_fields=("service", "offer_type"),
    )
)

GITHUB_PACKAGES_BILLING = register_html_profile(
    HtmlExtractionProfile(
        name="github_packages_billing",
        table_id="github-packages-free-use",
        columns={
            **_IDENTITY_COLUMNS,
            "storage": HtmlColumn("storage", "text"),
            "data transfer per month": HtmlColumn("data_transfer_per_month", "text"),
            "public package storage": HtmlColumn("public_package_storage", "text"),
            "inbound data transfer": HtmlColumn("inbound_data_transfer", "text"),
            "exhaustion behaviour": HtmlColumn("exhaustion_behaviour", "text"),
        },
        required_fields=("service", "offer_type"),
    )
)

GITHUB_CODESPACES_BILLING = register_html_profile(
    HtmlExtractionProfile(
        name="github_codespaces_billing",
        table_id="github-codespaces-free-quota",
        columns={
            **_IDENTITY_COLUMNS,
            "compute time per month": HtmlColumn("compute_time_per_month", "text"),
            "storage per month": HtmlColumn("storage_per_month", "text"),
            "exhaustion behaviour": HtmlColumn("exhaustion_behaviour", "text"),
        },
        required_fields=("service", "offer_type"),
    )
)

GITHUB_PAGES_LIMITS = register_html_profile(
    HtmlExtractionProfile(
        name="github_pages_limits",
        table_id="github-pages-usage-limits",
        columns={
            **_IDENTITY_COLUMNS,
            "published site size": HtmlColumn("published_site_size", "text"),
            "source repository size": HtmlColumn("source_repository_size", "text"),
            "bandwidth per month": HtmlColumn("bandwidth_per_month", "text"),
            "builds per hour": HtmlColumn("builds_per_hour", "text"),
            "deployment timeout": HtmlColumn("deployment_timeout", "text"),
            "sites per account": HtmlColumn("sites_per_account", "text"),
            "exhaustion behaviour": HtmlColumn("exhaustion_behaviour", "text"),
        },
        required_fields=("service", "offer_type"),
    )
)

#: The deliberate NON-Z0 profile. Structurally identical to the others, so the
#: non-Z0 verdict is produced by the captured *facts* (a 30-day expiry declared
#: as ``trial``) and not by any special-casing in code.
GITHUB_ENTERPRISE_CLOUD_TRIAL = register_html_profile(
    HtmlExtractionProfile(
        name="github_enterprise_cloud_trial",
        table_id="github-enterprise-cloud-trial",
        columns={
            **_IDENTITY_COLUMNS,
            "trial length days": HtmlColumn("trial_length_days", "text"),
            "licenses": HtmlColumn("licenses", "text"),
            "organizations": HtmlColumn("organizations", "text"),
            "actions minutes": HtmlColumn("actions_minutes", "text"),
            "exhaustion behaviour": HtmlColumn("exhaustion_behaviour", "text"),
        },
        required_fields=("service", "offer_type"),
    )
)

__all__: Sequence[str] = (
    "GITHUB_ACTIONS_BILLING",
    "GITHUB_PACKAGES_BILLING",
    "GITHUB_CODESPACES_BILLING",
    "GITHUB_PAGES_LIMITS",
    "GITHUB_ENTERPRISE_CLOUD_TRIAL",
)
