"""Google Cloud OFFICIAL free-tier extraction profiles (F008 P3).

Provider-specific selectors expressed purely as **data**, registered through the
F008 S3 seam so adding Google Cloud touches no shared module.

Every profile here was derived from the live ``cloud.google.com`` pages on
2026-08-14. Three structural facts about those pages drive the shapes below, and
all three were MEASURED rather than assumed:

* The Google Cloud Free Program page publishes the Always Free allowances in a
  real ``<table>`` whose rows are **products** and whose single value column is
  the free-tier limit (measured: 2 tables on the page, the target one carrying
  the live headers ``Google Cloud product`` / ``Free Tier usage limits`` and 29
  body rows). Every live product row is mapped, so a product Google adds later
  rejects the document (``unknown_matrix_rows``) instead of being silently
  dropped.
* The Firestore and BigQuery pricing pages each publish their own free allowance
  in a small dedicated table (``Free tier`` / ``Quota``, and ``Resource`` /
  ``Monthly free usage limits`` / ``Details``). Measured: on each page exactly
  one table carries the declared signature, so selection is unambiguous.
* The Google Cloud **Free Trial** shares a document with the Always Free tier and
  publishes nothing about itself in a table. Its profile is therefore
  ``mode="assertions"``: it declares no table selector, reads no table, and takes
  100% of its facts from prose pinned verbatim. Nothing is synthesized to satisfy
  the extractor.

**Always Free and the Free Trial are different offers and must never be
conflated.** They are extracted as two separate profiles from the same document
precisely so that conflation is structurally impossible: each pins its identity,
its offer type and its exhaustion behaviour to blocks inside its own section.

**The unfavourable findings are published, not omitted.** The load-bearing
sentence for the Always Free tier is, verbatim::

    Although the Free Tier lets you use certain Google Cloud products at no
    charge, there are monthly usage limits that are calculated per billing
    account. Any usage that exceeds the Free Tier usage limits is billed at
    standard rates.

That is ``automatic_billing``, which the classifier treats as a definite billing
exposure, so the Google Cloud Always Free tier is **Z1, never Z0**. The Free
Trial carries an equally direct sentence::

    During the sign up, you must provide a credit card or other payment method
    that is valid for the period of the Free Trial.

so ``requires_card = True`` for the trial is quoted, not inferred.

**Why ``requires_card`` is UNKNOWN for the Always Free tier.** MEASURED on the
live page: it states ``To use products that have a Free Tier, you need a Google
Cloud billing account.`` and ``A Google Cloud billing account is required to
access the Google Cloud Free Tier.`` NEITHER sentence says the billing account
requires a payment method. The card sentence that does exist is scoped to Free
Trial signup. Composing the two would be an inference, not a quotation, so
``requires_card`` is deliberately absent here and the billing-account
requirement is recorded as its own evidenced fact instead. Unknown is better than
guessed, and the Z0 verdict does not depend on it: ``automatic_billing`` already
blocks Z0 on its own.

**Why Firestore and BigQuery are ``recurring_quota`` and the program page is
``always_free``.** ``docs/DATA_MODEL.md`` -> "Choosing between ``always_free``
and ``recurring_quota``" is applied in order, from official evidence only:

* Rule 1 needs the provider to identify an indefinitely available zero-priced
  plan, tier or SKU. The program page states ``The Free Tier has no end date``
  verbatim, so ``gcp_free_tier_products`` is ``always_free``.
* Rule 2 covers a free allowance that replenishes on a schedule while the
  containing service is not itself zero-priced. Neither product page states an
  end date or a zero-priced tier of its own; each states that the service is
  charged, that a free allowance exists, and how it replenishes. Those three
  legs are pinned as separate facts so the determination is visible in the
  evidence rather than asserted in a comment.

The classifier treats the two values identically, so this choice changes no Z0
verdict; it is a taxonomy decision recorded here so a reviewer can check it
against the rule rather than against an author's intuition.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..html import HtmlExtractionProfile, HtmlMatrixRow, HtmlTextAssertion
from . import register_html_profile

#: The verbatim sentence that proves exceeding an Always Free allowance is
#: BILLED rather than stopped. This is what makes the Google Cloud Free Tier a
#: billing exposure, and it is the single most important fact in this module.
FREE_TIER_OVERAGE_IS_BILLED = (
    "Although the Free Tier lets you use certain Google Cloud products at no charge, there "
    "are monthly usage limits that are calculated per billing account. Any usage that "
    "exceeds the Free Tier usage limits is billed at standard rates."
)

#: The verbatim sentence that proves the Free Trial requires a payment method.
FREE_TRIAL_REQUIRES_PAYMENT_METHOD = (
    "During the sign up, you must provide a credit card or other payment method that is "
    "valid for the period of the Free Trial. Depending on your country, you might also need "
    "to verify your bank account. After you complete the signup and your identity and "
    "payment method are verified, your Free Trial period starts."
)

#: The verbatim sentence that establishes BigQuery's allowance outlives the trial
#: AND that exceeding it is charged. It also mentions a sandbox path that needs no
#: credit card -- which is why it is carried whole as ``notes`` and NOT converted
#: into ``requires_card = False``: the no-card claim is scoped to the BigQuery
#: sandbox, not to the offer this profile describes.
BIGQUERY_FREE_USAGE_AND_OVERAGE = (
    "As part of the Google Cloud Free Tier, BigQuery offers some resources free of charge "
    "up to a specific limit. These free usage limits are available during and after the "
    "free trial period. If you go over these usage limits and are no longer in the free "
    "trial period, you will be charged according to the pricing on this page. You can try "
    "BigQuery's free tier in the BigQuery sandbox without a credit card."
)

#: The verbatim sentence that establishes Firestore's free quota exists and that
#: continuing past it requires enabling billing.
FIRESTORE_FREE_QUOTA_AND_CONTINUATION = (
    "Firestore offers free quota that lets you get started at no cost. The free quota "
    "amounts are listed below. If you need more quota, you must enable billing for your "
    "Google Cloud project."
)


def _rows(*pairs: tuple[str, str]) -> dict[str, HtmlMatrixRow]:
    return {label: HtmlMatrixRow(field) for label, field in pairs}


GCP_FREE_TIER_PRODUCTS = register_html_profile(
    HtmlExtractionProfile(
        name="gcp_free_tier_products",
        header_signature=("Google Cloud product", "Free Tier usage limits"),
        mode="matrix",
        matrix_metric_header="Google Cloud product",
        matrix_tier_header="Free Tier usage limits",
        # All 29 live product rows, in live order. Completeness is the guard: an
        # unmapped row rejects the document rather than disappearing from the
        # published allowance set.
        matrix_rows=_rows(
            ("App Engine", "app_engine"),
            (
                "Agent Runtime on Gemini Enterprise Agent Platform",
                "agent_runtime_on_gemini_enterprise_agent_platform",
            ),
            ("Application Integration", "application_integration"),
            ("Artifact Registry", "artifact_registry"),
            ("BigQuery", "bigquery"),
            ("Cloud Build", "cloud_build"),
            ("Cloud Deploy", "cloud_deploy"),
            ("Cloud Key Management Service", "cloud_key_management_service"),
            (
                "Google Cloud Observability (Logging and Monitoring)",
                "google_cloud_observability_logging_and_monitoring",
            ),
            ("Cloud Natural Language API", "cloud_natural_language_api"),
            ("Cloud Run", "cloud_run"),
            ("Cloud Run functions", "cloud_run_functions"),
            ("Cloud Shell", "cloud_shell"),
            ("Cloud Source Repositories", "cloud_source_repositories"),
            ("Cloud Storage", "cloud_storage"),
            ("Cloud Vision", "cloud_vision"),
            ("Compute Engine", "compute_engine"),
            ("Datastream", "datastream"),
            ("Firestore", "firestore"),
            ("Google Kubernetes Engine (GKE)", "google_kubernetes_engine"),
            ("Pub/Sub", "pub_sub"),
            ("reCAPTCHA", "recaptcha"),
            ("Secret Manager", "secret_manager"),
            ("Security Command Center", "security_command_center"),
            ("Speech-to-Text", "speech_to_text"),
            ("Video Intelligence API", "video_intelligence_api"),
            ("Web Risk", "web_risk"),
            ("Workflows", "workflows"),
            ("Workload Manager", "workload_manager"),
        ),
        trusted_assertions=True,
        assertions=(
            # Identity is pinned to the Free Tier section's own definition
            # sentence rather than to the document title, because the title
            # covers BOTH offers on this page ("Free Google Cloud features and
            # trial offer") and would not distinguish them.
            HtmlTextAssertion(
                text=(
                    "The Google Cloud Free Tier gives you free usage of select Google Cloud "
                    "products, up to specified monthly limits."
                ),
                field="service",
                value="Google Cloud Free Tier",
            ),
            # "no end date" is the whole basis for the perpetual offer type.
            HtmlTextAssertion(
                text=(
                    "The Free Tier has no end date, but Google reserves the right to change "
                    "the offering, including changing or eliminating usage limits, with 30 "
                    "days' advance notice."
                ),
                field="offer_type",
                value="always_free",
            ),
            HtmlTextAssertion(
                text=FREE_TIER_OVERAGE_IS_BILLED,
                field="exhaustion_behaviour",
                value="automatic_billing",
            ),
            # The access precondition, recorded as its own fact. It says a
            # BILLING ACCOUNT is required; it does NOT say a card is, so it must
            # never be repinned to `requires_card`.
            HtmlTextAssertion(
                text=(
                    "To use products that have a Free Tier, you need a Google Cloud billing "
                    "account."
                ),
                field="billing_account",
                value="required",
            ),
            HtmlTextAssertion(
                text=(
                    "You don't have a negotiated pricing contract or a custom rate card with "
                    "Google, except as described for certain products listed in the Free Tier "
                    "usage limits table."
                ),
                field="eligibility",
                value=(
                    "You don't have a negotiated pricing contract or a custom rate card with "
                    "Google, except as described for certain products listed in the Free Tier "
                    "usage limits table."
                ),
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


#: The deliberate NON-Z0 control, and the only assertion-only profile here. The
#: live page publishes nothing about the trial in a table, so this profile reads
#: none and none was invented for it.
GCP_FREE_TRIAL = register_html_profile(
    HtmlExtractionProfile(
        name="gcp_free_trial",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            # One sentence carries the trial's identity, its time limit and the
            # fact that it IS a trial. Pinning all three to it means a reworded
            # duration cannot leave a stale "90" behind.
            HtmlTextAssertion(
                text=(
                    "The Google Cloud Free Trial is a 90-day program that lets new users try "
                    "the most popular Google Cloud products without any financial commitment. "
                    "You will not be billed for any Google Cloud usage during your Free Trial. "
                    "Access to products may be limited to prevent abuse."
                ),
                field="service",
                value="Google Cloud Free Trial",
            ),
            HtmlTextAssertion(
                text=(
                    "The Google Cloud Free Trial is a 90-day program that lets new users try "
                    "the most popular Google Cloud products without any financial commitment. "
                    "You will not be billed for any Google Cloud usage during your Free Trial. "
                    "Access to products may be limited to prevent abuse."
                ),
                field="offer_type",
                value="trial",
            ),
            HtmlTextAssertion(
                text=(
                    "The Google Cloud Free Trial is a 90-day program that lets new users try "
                    "the most popular Google Cloud products without any financial commitment. "
                    "You will not be billed for any Google Cloud usage during your Free Trial. "
                    "Access to products may be limited to prevent abuse."
                ),
                field="trial_length_days",
                value="90",
            ),
            # The credit is the trial's substance and is time-boxed. It is
            # captured as facts of its own so nothing about it can be mistaken
            # for a perpetual allowance.
            HtmlTextAssertion(
                text=(
                    "Signing up for the Free Trial creates a Free Trial billing account that "
                    "is preloaded with $300 in free Welcome credit which is valid for 90 days. "
                    "You can also use the Free Tier products, up to monthly usage limits. You "
                    "can spend your $300 credit on products and services covered by the Free "
                    "Trial, including on usage beyond the Free Tier limits."
                ),
                field="welcome_credit",
                value="$300",
            ),
            HtmlTextAssertion(
                text=(
                    "Signing up for the Free Trial creates a Free Trial billing account that "
                    "is preloaded with $300 in free Welcome credit which is valid for 90 days. "
                    "You can also use the Free Tier products, up to monthly usage limits. You "
                    "can spend your $300 credit on products and services covered by the Free "
                    "Trial, including on usage beyond the Free Tier limits."
                ),
                field="credit_validity_days",
                value="90",
            ),
            # THE unfavourable fact, quoted rather than inferred.
            HtmlTextAssertion(
                text=FREE_TRIAL_REQUIRES_PAYMENT_METHOD,
                field="requires_card",
                value=True,
            ),
            HtmlTextAssertion(
                text=(
                    "If you don't upgrade to a Paid billing account before 90 days pass or if "
                    "you spend the $300 in free credit, then your Free Trial billing account "
                    "will be closed and all of its associated projects and resources will be "
                    "stopped."
                ),
                field="exhaustion_behaviour",
                value="manual_upgrade_required",
            ),
            HtmlTextAssertion(
                text=(
                    "If you don't upgrade to a Paid billing account before 90 days pass or if "
                    "you spend the $300 in free credit, then your Free Trial billing account "
                    "will be closed and all of its associated projects and resources will be "
                    "stopped."
                ),
                field="notes",
                value=(
                    "If you don't upgrade to a Paid billing account before 90 days pass or if "
                    "you spend the $300 in free credit, then your Free Trial billing account "
                    "will be closed and all of its associated projects and resources will be "
                    "stopped."
                ),
            ),
            HtmlTextAssertion(
                text=(
                    "You've never been a paying user of Google Cloud, Google Maps Platform, "
                    "or Firebase."
                ),
                field="eligibility",
                value=(
                    "You've never been a paying user of Google Cloud, Google Maps Platform, "
                    "or Firebase."
                ),
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


GCP_FIRESTORE_FREE_TIER = register_html_profile(
    HtmlExtractionProfile(
        name="gcp_firestore_free_tier",
        header_signature=("Free tier", "Quota"),
        mode="matrix",
        matrix_metric_header="Free tier",
        matrix_tier_header="Quota",
        matrix_rows=_rows(
            ("Stored data", "stored_data"),
            ("Document reads", "document_reads"),
            ("Document writes", "document_writes"),
            ("Document deletes", "document_deletes"),
            ("Outbound data transfer", "outbound_data_transfer"),
        ),
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="Firestore pricing | Google Cloud",
                field="service",
                value="Firestore",
                scope="title",
            ),
            # Rule-2 leg 1: the containing service is itself metered.
            HtmlTextAssertion(
                text="When you use Firestore, you are charged for the following:",
                field="billing_basis",
                value="When you use Firestore, you are charged for the following:",
            ),
            # Rule-2 leg 2: a free allowance exists, and continuing past it
            # requires a manual, paid step rather than stopping silently.
            HtmlTextAssertion(
                text=FIRESTORE_FREE_QUOTA_AND_CONTINUATION,
                field="offer_type",
                value="recurring_quota",
            ),
            HtmlTextAssertion(
                text=FIRESTORE_FREE_QUOTA_AND_CONTINUATION,
                field="exhaustion_behaviour",
                value="manual_upgrade_required",
            ),
            # Rule-2 leg 3: the allowance replenishes on a schedule.
            HtmlTextAssertion(
                text="Quotas are applied daily and reset around midnight Pacific time.",
                field="quota_reset",
                value="Quotas are applied daily and reset around midnight Pacific time.",
            ),
            HtmlTextAssertion(
                text="Firestore allows exactly one free database per project.",
                field="free_databases_per_project",
                value="1",
            ),
            HtmlTextAssertion(
                text=(
                    "The following operations and features do not include free usage. You "
                    "must enable billing to use these features:"
                ),
                field="notes",
                value=(
                    "The following operations and features do not include free usage. You "
                    "must enable billing to use these features:"
                ),
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


GCP_BIGQUERY_FREE_TIER = register_html_profile(
    HtmlExtractionProfile(
        name="gcp_bigquery_free_tier",
        header_signature=("Resource", "Monthly free usage limits", "Details"),
        mode="matrix",
        matrix_metric_header="Resource",
        matrix_tier_header="Monthly free usage limits",
        matrix_rows=_rows(
            ("Storage", "storage"),
            ("Queries (analysis)", "queries_analysis"),
        ),
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="BigQuery | Google Cloud",
                field="service",
                value="BigQuery",
                scope="title",
            ),
            HtmlTextAssertion(
                text=BIGQUERY_FREE_USAGE_AND_OVERAGE,
                field="offer_type",
                value="recurring_quota",
            ),
            HtmlTextAssertion(
                text=BIGQUERY_FREE_USAGE_AND_OVERAGE,
                field="exhaustion_behaviour",
                value="automatic_billing",
            ),
            # Carried whole so the sandbox caveat travels with the offer instead
            # of being silently discarded or, worse, promoted to a no-card claim.
            HtmlTextAssertion(
                text=BIGQUERY_FREE_USAGE_AND_OVERAGE,
                field="notes",
                value=BIGQUERY_FREE_USAGE_AND_OVERAGE,
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


__all__: Sequence[str] = (
    "FREE_TIER_OVERAGE_IS_BILLED",
    "FREE_TRIAL_REQUIRES_PAYMENT_METHOD",
    "BIGQUERY_FREE_USAGE_AND_OVERAGE",
    "FIRESTORE_FREE_QUOTA_AND_CONTINUATION",
    "GCP_FREE_TIER_PRODUCTS",
    "GCP_FREE_TRIAL",
    "GCP_FIRESTORE_FREE_TIER",
    "GCP_BIGQUERY_FREE_TIER",
)
