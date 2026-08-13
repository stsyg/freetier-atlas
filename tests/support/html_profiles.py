"""Test-only generic HTML profiles for production-shape fixtures."""

from __future__ import annotations

from app.ingest.adapters import (
    HtmlExtractionProfile,
    HtmlMatrixRow,
    HtmlTextAssertion,
)
from app.ingest.adapters.profiles import register_html_profile

VERCEL_SANDBOX_MATRIX = register_html_profile(
    HtmlExtractionProfile(
        name="test_vercel_sandbox_matrix",
        header_signature=(
            "Hobby (Included)",
            "Pro (Per month)",
            "Enterprise (Per month)",
        ),
        mode="matrix",
        matrix_metric_header="",
        matrix_tier_header="Hobby (Included)",
        matrix_rows={
            "Sandbox Active CPU": HtmlMatrixRow("sandbox_active_cpu"),
            "Sandbox Provisioned Memory": HtmlMatrixRow("provisioned_memory"),
            "Sandbox Creations": HtmlMatrixRow("sandbox_creations"),
        },
        ignored_matrix_rows=("Sandbox Data Transfer",),
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
                    "On each billing cycle, Hobby plans receive a monthly allotment "
                    "of Sandbox usage at no cost. Pro and Enterprise plans are "
                    "charged based on usage."
                ),
                field="offer_type",
                value="always_free",
            ),
            HtmlTextAssertion(
                text=(
                    "On each billing cycle, Hobby plans receive a monthly allotment "
                    "of Sandbox usage at no cost. Pro and Enterprise plans are "
                    "charged based on usage."
                ),
                field="eligibility",
                value="Hobby plan",
            ),
            HtmlTextAssertion(
                text=(
                    "Once you exceed your included limit on Hobby, sandbox creation "
                    "is paused until the next billing cycle. Pro and Enterprise usage "
                    "is charged against your account."
                ),
                field="exhaustion_behaviour",
                value="hard_stop",
            ),
            HtmlTextAssertion(
                text=(
                    "Vercel sends you notifications as you approach your usage quotas. "
                    "You will not be charged for any additional usage. Once you exceed "
                    "the quotas, sandbox creation is paused until 30 days have passed "
                    "since you first used the feature."
                ),
                field="notes",
                value="No additional usage charge; creation resumes after 30 days.",
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)

__all__ = ("VERCEL_SANDBOX_MATRIX",)
