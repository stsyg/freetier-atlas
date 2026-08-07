"""Vercel OFFICIAL free-tier extraction profiles (F008 P2).

Provider-specific selectors are declarative data registered through the shared
profile seam. Missing material columns remain ``None`` and ``offer_type`` is
required so malformed or ambiguous rows cannot reach classification.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..html import HtmlColumn, HtmlExtractionProfile
from . import register_html_profile

_IDENTITY_COLUMNS: dict[str, HtmlColumn] = {
    "service": HtmlColumn("service", "text"),
    "offer type": HtmlColumn("offer_type", "text"),
    "card required": HtmlColumn("requires_card", "bool"),
    "paid dependencies": HtmlColumn("has_paid_dependencies", "bool"),
}

VERCEL_HOBBY_PLAN = register_html_profile(
    HtmlExtractionProfile(
        name="vercel_hobby_plan",
        table_id="vercel-hobby-offers",
        columns={
            **_IDENTITY_COLUMNS,
            "projects": HtmlColumn("projects", "text"),
            "deployments per day": HtmlColumn("deployments_per_day", "text"),
            "active cpu per month": HtmlColumn("active_cpu_per_month", "text"),
            "provisioned memory per month": HtmlColumn("provisioned_memory_per_month", "text"),
            "invocations per month": HtmlColumn("invocations_per_month", "text"),
            "edge requests per month": HtmlColumn("edge_requests_per_month", "text"),
            "fast data transfer per month": HtmlColumn("fast_data_transfer_per_month", "text"),
            "analytics events per month": HtmlColumn("analytics_events_per_month", "text"),
            "runtime log retention": HtmlColumn("runtime_log_retention", "text"),
            "config reads per month": HtmlColumn("config_reads_per_month", "text"),
            "config writes per month": HtmlColumn("config_writes_per_month", "text"),
            "usage restriction": HtmlColumn("notes", "text"),
            "exhaustion behaviour": HtmlColumn("exhaustion_behaviour", "text"),
        },
        required_fields=("service", "offer_type"),
    )
)

VERCEL_BLOB_PRICING = register_html_profile(
    HtmlExtractionProfile(
        name="vercel_blob_pricing",
        table_id="vercel-blob-hobby",
        columns={
            **_IDENTITY_COLUMNS,
            "storage per month": HtmlColumn("storage_per_month", "text"),
            "simple operations per month": HtmlColumn("simple_operations_per_month", "text"),
            "advanced operations per month": HtmlColumn("advanced_operations_per_month", "text"),
            "data transfer per month": HtmlColumn("data_transfer_per_month", "text"),
            "exhaustion behaviour": HtmlColumn("exhaustion_behaviour", "text"),
        },
        required_fields=("service", "offer_type"),
    )
)

VERCEL_QUEUES_PRICING = register_html_profile(
    HtmlExtractionProfile(
        name="vercel_queues_pricing",
        table_id="vercel-queues-hobby",
        columns={
            **_IDENTITY_COLUMNS,
            "api operations per month": HtmlColumn("api_operations_per_month", "text"),
            "exhaustion behaviour": HtmlColumn("exhaustion_behaviour", "text"),
        },
        required_fields=("service", "offer_type"),
    )
)

VERCEL_PRO_TRIAL = register_html_profile(
    HtmlExtractionProfile(
        name="vercel_pro_trial",
        table_id="vercel-pro-trial",
        columns={
            **_IDENTITY_COLUMNS,
            "trial length days": HtmlColumn("trial_length_days", "text"),
            "trial credit": HtmlColumn("trial_credit", "text"),
            "active cpu": HtmlColumn("active_cpu", "text"),
            "provisioned memory": HtmlColumn("provisioned_memory", "text"),
            "function invocations per month": HtmlColumn("function_invocations_per_month", "text"),
            "exhaustion behaviour": HtmlColumn("exhaustion_behaviour", "text"),
        },
        required_fields=("service", "offer_type"),
    )
)

VERCEL_STORAGE_BOUNDARY = register_html_profile(
    HtmlExtractionProfile(
        name="vercel_storage_boundary",
        table_id="vercel-marketplace-storage",
        columns={
            **_IDENTITY_COLUMNS,
            "marketplace providers": HtmlColumn("marketplace_providers", "text"),
            "exhaustion behaviour": HtmlColumn("exhaustion_behaviour", "text"),
        },
        required_fields=("service", "offer_type"),
    )
)

VERCEL_AI_GATEWAY_PROMOTION = register_html_profile(
    HtmlExtractionProfile(
        name="vercel_ai_gateway_promotion",
        table_id="vercel-ai-gateway-promotion",
        columns={
            **_IDENTITY_COLUMNS,
            "promotion period": HtmlColumn("promotion_period", "text"),
            "exhaustion behaviour": HtmlColumn("exhaustion_behaviour", "text"),
        },
        required_fields=("service", "offer_type"),
    )
)

__all__: Sequence[str] = (
    "VERCEL_HOBBY_PLAN",
    "VERCEL_BLOB_PRICING",
    "VERCEL_QUEUES_PRICING",
    "VERCEL_PRO_TRIAL",
    "VERCEL_STORAGE_BOUNDARY",
    "VERCEL_AI_GATEWAY_PROMOTION",
)
