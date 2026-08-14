"""GitHub OFFICIAL free-tier extraction profiles (F008 slice P1).

Provider-specific selectors expressed purely as **data**, registered through the
F008 S3 seam so adding GitHub touches no shared module.

Every profile in this module was re-derived from the live ``docs.github.com``
pages on 2026-08-13. Two structural facts about those pages drive the shapes
below, and both were measured rather than assumed:

* The Actions, Packages and Codespaces billing pages publish their allowances in
  a real table whose rows are **plans** and whose columns are **metrics** -- the
  transpose of the Vercel pages. A matrix profile pivots one tier column across
  row labels, so each profile here declares the live plan column as its metric
  header and one live metric column as its tier column. Every live plan row is
  mapped, so a plan GitHub adds later rejects the document (``unknown_matrix_rows``)
  instead of being silently dropped.
* The GitHub Pages limits page and the Enterprise Cloud trial page contain **no
  table at all** (measured: zero ``<table>`` elements in the live markup). Their
  allowances are published as ``<li>``/``<p>`` prose. Those two profiles are
  therefore ``mode="assertions"``: they declare no table selector, read no
  table, and take 100% of their published facts from text assertions pinned to
  the verbatim live prose. Nothing is synthesized to satisfy the extractor, so
  each committed capture is the live page minus irrelevant chrome -- see the
  fixtures' ``capture.json``.

**Why the material Z0 conditions are assertions, not table cells.** The claim
that makes a Z0 verdict reachable is ``requires_card = False``. Reading it out of
a table cell means the claim is only as good as the person who typed the cell: if
GitHub reworded or deleted the sentence that actually justifies it, the cell would
still read "No" and every test would still pass. Pinning it to the verbatim block
with whole-block normalized equality inverts that -- any rewording, truncation or
deletion yields ``assertion_not_found`` and REJECTS the candidate.

The load-bearing sentence, verbatim on the Actions, Packages and Codespaces
billing pages, is::

    If your account does not have a valid payment method on file, usage is
    blocked once you use up your quota.

It proves two separate facts at once and is therefore pinned twice, to different
fields: an account with no payment method on file is never charged
(``requires_card = False``) and its usage stops rather than billing
(``exhaustion_behaviour = hard_stop``). That safe stop is precisely why these
offers can reach Z0.

``offer_type`` distinguishes a perpetual allowance (``always_free``) from a
**time-limited trial** (``trial``). The GitHub Enterprise Cloud trial requires no
payment method -- verbatim, "You do not need to provide a payment method to start
a trial." -- and still expires, verbatim, "The trial lasts for 30 days and
includes the following features." Declaring it ``trial`` is what makes the
classifier withhold Z0, so a no-card offer is never published as free forever.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..html import HtmlExtractionProfile, HtmlMatrixRow, HtmlTextAssertion
from . import register_html_profile

#: The verbatim sentence that proves an account with no payment method on file is
#: never charged, and that its usage stops instead. Present on the Actions,
#: Packages and Codespaces billing pages.
NO_PAYMENT_METHOD_BLOCKS_USAGE = (
    "If your account does not have a valid payment method on file, usage is "
    "blocked once you use up your quota."
)


def _rows(*pairs: tuple[str, str]) -> dict[str, HtmlMatrixRow]:
    return {label: HtmlMatrixRow(field) for label, field in pairs}


def _no_card_and_safe_stop() -> tuple[HtmlTextAssertion, ...]:
    """Pin both facts the no-payment-method sentence proves, to that one block."""

    return (
        HtmlTextAssertion(
            text=NO_PAYMENT_METHOD_BLOCKS_USAGE,
            field="requires_card",
            value=False,
        ),
        HtmlTextAssertion(
            text=NO_PAYMENT_METHOD_BLOCKS_USAGE,
            field="exhaustion_behaviour",
            value="hard_stop",
        ),
    )


GITHUB_ACTIONS_BILLING = register_html_profile(
    HtmlExtractionProfile(
        name="github_actions_billing",
        header_signature=(
            "Plan",
            "Artifact storage",
            "Minutes (per month)",
            "Cache storage (per repository)",
            "Custom image storage",
        ),
        mode="matrix",
        matrix_metric_header="Plan",
        matrix_tier_header="Minutes (per month)",
        matrix_rows=_rows(
            ("GitHub Free", "minutes_per_month_github_free"),
            ("GitHub Pro", "minutes_per_month_github_pro"),
            ("GitHub Free for organizations", "minutes_per_month_github_free_for_organizations"),
            ("GitHub Team", "minutes_per_month_github_team"),
            ("GitHub Enterprise Cloud", "minutes_per_month_github_enterprise_cloud"),
        ),
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="GitHub Actions billing - GitHub Docs",
                field="service",
                value="GitHub Actions",
                scope="title",
            ),
            HtmlTextAssertion(
                text=(
                    "The following amounts of time for standard runners, artifact storage, "
                    "and cache storage are included in your GitHub plan. At the start of each "
                    "month, the minutes used by the account are reset to zero."
                ),
                field="offer_type",
                value="always_free",
            ),
            *_no_card_and_safe_stop(),
            HtmlTextAssertion(
                text=(
                    "GitHub Actions usage is free for self-hosted runners and for public "
                    "repositories that use standard GitHub-hosted runners. See Choosing the "
                    "runner for a job."
                ),
                field="has_paid_dependencies",
                value=False,
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)

GITHUB_PACKAGES_BILLING = register_html_profile(
    HtmlExtractionProfile(
        name="github_packages_billing",
        header_signature=("Plan", "Storage", "Data transfer (per month)"),
        mode="matrix",
        matrix_metric_header="Plan",
        # The live Storage column publishes "500MB" with no separating space, which
        # the publication re-validator deliberately refuses to parse (an ambiguous
        # "M" magnitude directly followed by "B"). Pivoting the data-transfer column
        # keeps every published number re-validatable; the Storage column is
        # retained verbatim in the capture and disclosed there as un-pivoted.
        matrix_tier_header="Data transfer (per month)",
        matrix_rows=_rows(
            ("GitHub Free", "data_transfer_per_month_github_free"),
            ("GitHub Pro", "data_transfer_per_month_github_pro"),
            (
                "GitHub Free for organizations",
                "data_transfer_per_month_github_free_for_organizations",
            ),
            ("GitHub Team", "data_transfer_per_month_github_team"),
            ("GitHub Enterprise Cloud", "data_transfer_per_month_github_enterprise_cloud"),
        ),
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="GitHub Packages billing - GitHub Docs",
                field="service",
                value="GitHub Packages",
                scope="title",
            ),
            HtmlTextAssertion(
                text=(
                    "The following amounts of storage and data transfer are included in your "
                    "GitHub plan. At the start of each month, the data transfer for the "
                    "account is reset to zero."
                ),
                field="offer_type",
                value="always_free",
            ),
            *_no_card_and_safe_stop(),
            HtmlTextAssertion(
                text=(
                    "GitHub Packages usage is free for public packages. In addition, data "
                    "transferred in from any source is free."
                ),
                field="has_paid_dependencies",
                value=False,
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)

GITHUB_CODESPACES_BILLING = register_html_profile(
    HtmlExtractionProfile(
        name="github_codespaces_billing",
        header_signature=("Account plan", "Storage per month", "Compute time per month"),
        mode="matrix",
        matrix_metric_header="Account plan",
        matrix_tier_header="Compute time per month",
        matrix_rows=_rows(
            (
                "GitHub Free for personal accounts",
                "compute_time_per_month_github_free_for_personal_accounts",
            ),
            ("GitHub Pro", "compute_time_per_month_github_pro"),
        ),
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="GitHub Codespaces billing - GitHub Docs",
                field="service",
                value="GitHub Codespaces",
                scope="title",
            ),
            # The included quota belongs to *every* personal account, including a
            # free one, so obtaining it depends on no paid product. That is the
            # page's own basis for both the perpetual offer type and the absence
            # of a paid dependency, so one block pins both.
            HtmlTextAssertion(
                text=(
                    "All GitHub personal accounts include a quota of free compute time and "
                    "storage for GitHub Codespaces. Any usage beyond the included amounts is "
                    "billed to the personal account."
                ),
                field="offer_type",
                value="always_free",
            ),
            HtmlTextAssertion(
                text=(
                    "All GitHub personal accounts include a quota of free compute time and "
                    "storage for GitHub Codespaces. Any usage beyond the included amounts is "
                    "billed to the personal account."
                ),
                field="has_paid_dependencies",
                value=False,
            ),
            *_no_card_and_safe_stop(),
        ),
        required_fields=("service", "offer_type"),
    )
)

#: The live GitHub Pages limits page contains no table, so this profile declares
#: none: every fact below is pinned to one verbatim ``<li>``/``<p>`` block, so a
#: reworded limit rejects the document rather than publishing a stale number.
GITHUB_PAGES_LIMITS = register_html_profile(
    HtmlExtractionProfile(
        name="github_pages_limits",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="GitHub Pages limits - GitHub Docs",
                field="service",
                value="GitHub Pages",
                scope="title",
            ),
            # Pages is available on GitHub Free itself, so the offer is perpetual,
            # needs no payment method and depends on no paid product.
            HtmlTextAssertion(
                text=(
                    "GitHub Pages is available in public repositories with GitHub Free and "
                    "GitHub Free for organizations, and in public and private repositories "
                    "with GitHub Pro, GitHub Team, GitHub Enterprise Cloud, and GitHub "
                    "Enterprise Server. See GitHub's plans."
                ),
                field="offer_type",
                value="always_free",
            ),
            HtmlTextAssertion(
                text=(
                    "GitHub Pages is available in public repositories with GitHub Free and "
                    "GitHub Free for organizations, and in public and private repositories "
                    "with GitHub Pro, GitHub Team, GitHub Enterprise Cloud, and GitHub "
                    "Enterprise Server. See GitHub's plans."
                ),
                field="requires_card",
                value=False,
            ),
            HtmlTextAssertion(
                text=(
                    "GitHub Pages is available in public repositories with GitHub Free and "
                    "GitHub Free for organizations, and in public and private repositories "
                    "with GitHub Pro, GitHub Team, GitHub Enterprise Cloud, and GitHub "
                    "Enterprise Server. See GitHub's plans."
                ),
                field="has_paid_dependencies",
                value=False,
            ),
            # Exceeding a soft quota degrades or suspends service and never bills:
            # Pages has no metered paid tier to bill into.
            HtmlTextAssertion(
                text=(
                    "In order to provide consistent quality of service for all GitHub Pages "
                    "sites, rate limits may apply. These rate limits are not intended to "
                    "interfere with legitimate uses of GitHub Pages. If your request triggers "
                    "rate limiting, you will receive an appropriate response with an HTTP "
                    "status code of 429, along with an informative HTML body."
                ),
                field="exhaustion_behaviour",
                value="throttled",
            ),
            HtmlTextAssertion(
                text="Published GitHub Pages sites may be no larger than 1 GB.",
                field="published_site_size",
                value="1 GB",
            ),
            HtmlTextAssertion(
                text=(
                    "GitHub Pages source repositories have a recommended limit of 1 GB. For "
                    "more information, see About large files on GitHub."
                ),
                field="source_repository_size",
                value="1 GB",
            ),
            HtmlTextAssertion(
                text="GitHub Pages sites have a soft bandwidth limit of 100 GB per month.",
                field="bandwidth_per_month",
                value="100 GB",
            ),
            HtmlTextAssertion(
                text=(
                    "GitHub Pages sites have a soft limit of 10 builds per hour. This limit "
                    "does not apply if you build and publish your site with a custom GitHub "
                    "Actions workflow."
                ),
                field="builds_per_hour",
                value="10",
            ),
            HtmlTextAssertion(
                text="GitHub Pages deployments will timeout if they take longer than 10 minutes.",
                field="deployment_timeout",
                value="10 minutes",
            ),
            HtmlTextAssertion(
                text=(
                    "You can only create one user or organization site for each account on GitHub."
                ),
                field="sites_per_account",
                value="1",
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)

#: The deliberate NON-Z0 profile, and the live trial page likewise contains no
#: table, so this profile is assertion-only too. The non-Z0 verdict is produced
#: entirely by captured *facts* -- a 30-day expiry declared as ``trial`` -- and
#: by no special-casing in code.
GITHUB_ENTERPRISE_CLOUD_TRIAL = register_html_profile(
    HtmlExtractionProfile(
        name="github_enterprise_cloud_trial",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text=(
                    "Setting up a trial of GitHub Enterprise Cloud - GitHub Enterprise Cloud Docs"
                ),
                field="service",
                value="GitHub Enterprise Cloud trial",
                scope="title",
            ),
            # The single sentence that makes this offer NON-Z0: it expires.
            HtmlTextAssertion(
                text="The trial lasts for 30 days and includes the following features.",
                field="offer_type",
                value="trial",
            ),
            HtmlTextAssertion(
                text="The trial lasts for 30 days and includes the following features.",
                field="trial_length_days",
                value="30",
            ),
            # No payment method is required, and that is exactly why the offer
            # type has to carry the time limit: a no-card offer that expires must
            # never be published as free forever.
            HtmlTextAssertion(
                text="You do not need to provide a payment method to start a trial.",
                field="requires_card",
                value=False,
            ),
            HtmlTextAssertion(
                text="You do not need to provide a payment method to start a trial.",
                field="has_paid_dependencies",
                value=False,
            ),
            HtmlTextAssertion(
                text=(
                    "You can end your trial at any time by purchasing GitHub Enterprise or "
                    "canceling the trial. Otherwise, after 30 days, your trial will expire."
                ),
                field="exhaustion_behaviour",
                value="manual_upgrade_required",
            ),
            HtmlTextAssertion(
                text="Up to 50 licenses to grant access to users.",
                field="licenses",
                value="50",
            ),
            # The page says "three", not "3". Publishing the digit would be a
            # silent re-expression, so the word is carried verbatim.
            HtmlTextAssertion(
                text=(
                    "You can create up to three new organizations in the trial enterprise, or "
                    "transfer any number of existing organizations."
                ),
                field="organizations",
                value="three",
            ),
            HtmlTextAssertion(
                text="Up to 3,000 minutes of standard GitHub-hosted runners.",
                field="actions_minutes",
                value="3,000",
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)

__all__: Sequence[str] = (
    "NO_PAYMENT_METHOD_BLOCKS_USAGE",
    "GITHUB_ACTIONS_BILLING",
    "GITHUB_PACKAGES_BILLING",
    "GITHUB_CODESPACES_BILLING",
    "GITHUB_PAGES_LIMITS",
    "GITHUB_ENTERPRISE_CLOUD_TRIAL",
)
