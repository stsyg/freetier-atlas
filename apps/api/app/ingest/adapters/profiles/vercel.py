"""Vercel official-document extraction profiles (F008 P2).

Each profile selects one production table by its visible header signature and
pivots one real tier column. Exact same-document prose supplies normalized
identity and eligibility fields. The profiles deliberately do not map
``requires_card`` or ``has_paid_dependencies``: Vercel's official pages do not
prove either condition for these offers, so the publication gate must review
rather than publish them.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..html import HtmlExtractionProfile, HtmlMatrixRow, HtmlTextAssertion
from . import register_html_profile


def _rows(*pairs: tuple[str, str]) -> dict[str, HtmlMatrixRow]:
    return {label: HtmlMatrixRow(field) for label, field in pairs}


VERCEL_HOBBY_PLAN = register_html_profile(
    HtmlExtractionProfile(
        name="vercel_hobby_plan",
        header_signature=("Resource", "Hobby Included Usage"),
        mode="matrix",
        matrix_metric_header="Resource",
        matrix_tier_header="Hobby Included Usage",
        matrix_rows=_rows(
            ("Global Config Reads", "global_config_reads"),
            ("Global Config Writes", "global_config_writes"),
            ("Active CPU", "active_cpu"),
            ("Provisioned Memory", "provisioned_memory"),
            ("Function Invocations", "function_invocations"),
            ("Image Transformations", "image_transformations"),
            ("Image Cache Reads", "image_cache_reads"),
            ("Image Cache Writes", "image_cache_writes"),
            ("Speed Insights Events", "speed_insights_events"),
            ("Speed Insights Projects", "speed_insights_projects"),
            ("Web Analytics Events", "web_analytics_events"),
            ("Workflow Events", "workflow_events"),
            ("Workflow Data Written", "workflow_data_written"),
        ),
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="Vercel Hobby Plan",
                field="service",
                value="Vercel Hobby",
                scope="title",
            ),
            HtmlTextAssertion(
                text=(
                    "The Hobby plan is free and aimed at developers with personal projects, "
                    "and small-scale applications. It offers a generous set of features for "
                    "individual users on a per month basis:"
                ),
                field="offer_type",
                value="always_free",
            ),
            HtmlTextAssertion(
                text=(
                    "As the Hobby plan is a free tier there are no billing cycles. In most "
                    "cases, if you exceed your usage limits on the Hobby plan, you will have "
                    "to wait until 30 days have passed before you can use the feature again."
                ),
                field="exhaustion_behaviour",
                value="hard_stop",
            ),
            HtmlTextAssertion(
                text=(
                    "As stated in the fair use guidelines, the Hobby plan restricts users to "
                    "non-commercial, personal use only."
                ),
                field="notes",
                value=(
                    "As stated in the fair use guidelines, the Hobby plan restricts users to "
                    "non-commercial, personal use only."
                ),
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


VERCEL_SANDBOX_PRICING = register_html_profile(
    HtmlExtractionProfile(
        name="vercel_sandbox_pricing",
        header_signature=(
            "Hobby (Included)",
            "Pro (Per month)",
            "Enterprise (Per month)",
        ),
        mode="matrix",
        matrix_metric_header="",
        matrix_tier_header="Hobby (Included)",
        matrix_rows=_rows(
            ("Sandbox Active CPU", "sandbox_active_cpu"),
            ("Sandbox Provisioned Memory", "sandbox_provisioned_memory"),
            ("Sandbox Creations", "sandbox_creations"),
            ("Sandbox Data Transfer", "sandbox_data_transfer"),
            ("Snapshot Storage", "snapshot_storage"),
            ("Max Runtime Duration", "max_runtime_duration"),
            ("Concurrent Sandboxes", "concurrent_sandboxes"),
            ("vCPU Allocation Rate", "vcpu_allocation_rate"),
        ),
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="Vercel Sandbox pricing and quotas",
                field="service",
                value="Vercel Sandbox",
                scope="title",
            ),
            HtmlTextAssertion(
                text=(
                    "On each billing cycle, Hobby plans receive a monthly allotment of "
                    "Sandbox usage at no cost. Pro and Enterprise plans are charged based "
                    "on usage."
                ),
                field="offer_type",
                value="always_free",
            ),
            HtmlTextAssertion(
                text=(
                    "Once you exceed your included limit on Hobby, sandbox creation is "
                    "paused until the next billing cycle. Pro and Enterprise usage is "
                    "charged against your account."
                ),
                field="exhaustion_behaviour",
                value="hard_stop",
            ),
            HtmlTextAssertion(
                text=(
                    "Vercel sends you notifications as you approach your usage quotas. You "
                    "will not be charged for any additional usage. Once you exceed the "
                    "quotas, sandbox creation is paused until 30 days have passed since you "
                    "first used the feature."
                ),
                field="notes",
                value=(
                    "Vercel sends you notifications as you approach your usage quotas. You "
                    "will not be charged for any additional usage. Once you exceed the "
                    "quotas, sandbox creation is paused until 30 days have passed since you "
                    "first used the feature."
                ),
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


VERCEL_PRO_TRIAL = register_html_profile(
    HtmlExtractionProfile(
        name="vercel_pro_trial",
        header_signature=("Pro Trial Limits",),
        mode="matrix",
        matrix_metric_header="",
        matrix_tier_header="Pro Trial Limits",
        matrix_rows=_rows(
            ("Owner Members", "owner_members"),
            ("Team Members (total, including Owners)", "team_members"),
            ("Projects", "projects"),
            ("Active CPU", "active_cpu"),
            ("Provisioned Memory", "provisioned_memory"),
            ("Function Invocations", "function_invocations"),
            ("Image transformations", "image_transformations"),
            ("Image cache reads", "image_cache_reads"),
            ("Image cache writes", "image_cache_writes"),
            ("Domains per Project", "domains_per_project"),
        ),
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="Understanding Vercel's Pro Plan Trial",
                field="service",
                value="Vercel Pro Trial",
                scope="title",
            ),
            HtmlTextAssertion(
                text=(
                    "The Pro trial offers an opportunity to explore Pro features for free "
                    "during the trial period. There are some limitations."
                ),
                field="offer_type",
                value="trial",
            ),
            HtmlTextAssertion(
                text=(
                    "Once your usage of Active CPU, Provisioned Memory, or Function "
                    "Invocations exceeds or reaches 100% of the Pro trial usage, your trial "
                    "will be paused."
                ),
                field="exhaustion_behaviour",
                value="hard_stop",
            ),
            HtmlTextAssertion(
                text=(
                    "Your trial finishes after 14 days or once your team exceeds the usage "
                    "limits, whichever happens first. After which, you can opt for one of "
                    "two paths:"
                ),
                field="notes",
                value=(
                    "Your trial finishes after 14 days or once your team exceeds the usage "
                    "limits, whichever happens first. After which, you can opt for one of "
                    "two paths:"
                ),
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


__all__: Sequence[str] = (
    "VERCEL_HOBBY_PLAN",
    "VERCEL_SANDBOX_PRICING",
    "VERCEL_PRO_TRIAL",
)
