"""Microsoft Azure OFFICIAL free-tier extraction profiles (F008 P5).

Provider-specific selectors expressed purely as **data**, registered through the
F008 S3 seam so adding Azure touches no shared module.

Every profile here was derived from the live ``azure.microsoft.com`` and
``learn.microsoft.com`` pages on 2026-08-14 by an owner-run reconciling
generator that resolves each pinned block against the LIVE parse and refuses to
write a fixture whose blocks or table cells differ from live in either
direction. Nothing below was transcribed from a browser by hand, because
transcription is exactly where a *composed* quotation creeps in.

Four structural facts about those pages drive the shapes here, and all four were
MEASURED with this repository's own parser rather than assumed.

* **Azure's per-category free-service list is client-rendered.** MEASURED on
  ``https://azure.microsoft.com/en-us/pricing/free-services/``: 203234 bytes
  served, **zero** ``<table>`` elements, and the headings "Free for your first 12
  months" and "65+ always-free services with an Azure account" are served with no
  allowance prose beneath either of them. That is the same shape AWS and Google
  Cloud presented, and it is reported rather than worked around.
* **Two Azure pages ARE matrix-extractable, and they are documentation pages
  rather than marketing pages.** ``learn.microsoft.com`` serves the App Service
  quota table (``Quota`` / ``Description``, 5 rows) and the Static Web Apps plan
  comparison (``Feature`` / ``Free plan (For personal projects)`` / ..., 13 rows).
  Both were selected by header signature, both match exactly one live table, and
  every live row of each is mapped.
* **The remaining five sources publish their terms entirely as prose**, so they
  are ``mode="assertions"``: they declare no table selector, read no table, and
  take 100% of their facts from blocks pinned verbatim. Three of those five live
  documents contain no table at all.
* **Azure publishes several different free things under overlapping branding**,
  and conflating them is the central correctness risk here -- see below.

**The four Azure free-offer kinds must never be conflated.** Azure publishes a
credit-backed *Azure free account*, a *12 months free services* introductory
window, per-service *free plans and free tiers* (one of which is explicitly
lifetime), and eligibility-gated *programmes* such as Azure for Students. Only
the third kind could ever be perpetual. Each profile below pins its identity,
its offer type and its term to blocks on its own document, and no profile
borrows a fact from another.

**THE HEADLINE: Azure DOES state a perpetual free tier in a block of its own,
and it is still not Z0.** I predicted before probing that this would be the
prediction I got wrong, because the AWS builder's most valuable disclosed error
was concluding too early that a provider never describes a perpetual offer in
isolation. Widening the sweep past the marketing hub found Microsoft stating it
plainly: ``https://learn.microsoft.com/en-us/azure/cosmos-db/free-tier`` is
titled "Lifetime Free Tier - Azure Cosmos DB" and says, in a block of its own::

    Free tier lasts indefinitely for the lifetime of the account and it comes
    with all the benefits and features of a regular Azure Cosmos DB account.

That is rule 1 of ``docs/DATA_MODEL.md`` satisfied by quotation, so
:data:`AZURE_COSMOS_DB_FREE_TIER` is ``always_free``. **And it is still
``Z1_BILLING_EXPOSURE``**, because the block that grants the allowance also
states::

    The throughput and storage consumed beyond these limits are billed at
    regular price.

Azure therefore reproduces the AWS Step Functions finding independently, on a
different provider and a different document: **perpetual does not mean Z0**.

**The unfavourable finding is published, not omitted.** The Azure free account
terms state, verbatim::

    All you need is a phone number, a credit card or a debit card (non-prepaid),
    and a Microsoft account or a GitHub account. Only credit cards are accepted
    in Hong Kong and Brazil.

That block sits under the "Payment options" heading of a document whose own
heading is "Azure free account terms & conditions", so ``requires_card = True``
on :data:`AZURE_FREE_ACCOUNT` is a quotation about *that* offer rather than a
composition of two blocks. It makes the offer ``Z1_BILLING_EXPOSURE``. The
qualification travels with it as :data:`FREE_ACCOUNT_PAYMENT_PURPOSE`, so the
"will initially NOT be charged" half is not silently dropped.

**A second prediction I got wrong, and it changed the slice.** I expected no
Azure page to state a SAFE exhaustion behaviour for a free tier. App Service
does, in a block of its own::

    If an app exceeds the CPU (Short), CPU (Day), or Bandwidth quota, the app is
    stopped until the quota resets. During this time, all incoming requests
    result in an HTTP 403 error.

That is a non-billing stop. :data:`AZURE_APP_SERVICE_QUOTAS` is therefore the
closest any offer in this slice comes to Z0: it clears gate 3 -- definite billing
exposure -- entirely, so every one of its blocking conditions is an *unknown*
rather than an exposure.

**It is TWO unknowns from Z0, not one, and the distinction is load-bearing.**
Gate 4 reports ``requires_card`` and ``has_paid_dependencies`` independently, and
no block on this document states either::

    [1] Whether a payment card is required is unknown.
    [2] Whether the offer has paid dependencies is unknown.

**Resolving the card alone still yields ``UNKNOWN``.** MEASURED by exhaustive
enumeration over the tri-state combinations, holding this document's own
``offer_type`` and ``exhaustion_behaviour``: exactly **1 of 9** reaches Z0, and
it needs BOTH facts resolved favourably. Pinned by
``test_the_safest_azure_offer_is_two_unknowns_from_z0``.

An earlier revision of this module said "fails **only** on gate 4 ... one unknown
separates it from Z0". Every word of that was true -- gate 4 *is* the only gate
that fires -- and it still reliably read as "one FACT is missing", which is
false. It is recorded here rather than quietly rewritten, because a later slice
that supplied the card fact expecting Z0 would get ``UNKNOWN`` and go hunting for
a bug that does not exist. ``hard_stop`` would classify identically -- both values
are in ``SAFE_EXHAUSTION`` -- and ``site_disabled_until_reset`` is chosen because
"stopped until the quota resets" is what the sentence actually says.

**Why ``requires_card`` is UNKNOWN on the other six profiles.** The
payment-method block lives on the free-account terms page and describes that
offer's signup. The free-services hub, the Cosmos DB, App Service, Static Web
Apps and DevOps documents each state nothing about a payment method. Carrying
the free-account sentence onto them would be cross-document composition -- the
error the Google Cloud slice avoided by recording ``billing_account: required``
instead of inventing a card claim -- so ``requires_card`` is simply absent there.

**Why ``requires_card = False`` is NOT pinned on the Students page, and why that
is not an omission.** ``https://azure.microsoft.com/en-us/free/students/``
publishes the bare list item "No credit card required" -- the only block in this
entire sweep stating that a card is *not* needed, and a favourable fact that must
not go missing. It is carried whole as :data:`STUDENTS_CARD_CLAIM` on the offer's
``card_claim`` field, so it IS published. It is not converted into
``requires_card=False`` because the live page carries **two** offers: the Azure
for Students bullet list and, immediately after it, an Azure for Startups list
whose own bullet states the opposite direction ("Spending protection-credit card
required only for identity verification and services beyond credit*"). Read
alone the bullet names no offer, so pinning it would splice one offer's terms
onto a page that publishes two. The choice is **verdict-neutral**, and that is
asserted by test rather than claimed here: ``student_program`` is in the
classifier's ``TEMPORARY_CONDITIONAL_OFFER_TYPES``, so even with
``requires_card=False`` the offer could reach Z2 at best and never Z0, and with
``has_paid_dependencies`` unknown it is ``UNKNOWN`` either way.

**Why ``exhaustion_behaviour`` is UNKNOWN on three profiles.** The free-services
hub and the Students page state no consequence at all in served HTML (the
Students FAQ answers are client-rendered; only the question headings are served).
The DevOps page states how to obtain MORE capacity -- "simply buy the number of
pipelines you need" -- but never what happens if you do not, and deriving an
exhaustion rule from a purchase instruction would be inference rather than
quotation. All three are therefore left unknown, gate 4 returns ``UNKNOWN``, and
Z0 is withheld.

**Why Static Web Apps and App Service are ``other``.** ``docs/DATA_MODEL.md`` ->
"Choosing between ``always_free`` and ``recurring_quota``" is applied in order,
from official evidence only. Rule 1 needs the provider to identify an
*indefinitely available* zero-priced plan; both documents identify a Free plan
but neither states that it is indefinite. Rule 2 needs a replenishing allowance
while the containing plan is **not** zero-priced; here the containing plan *is*
the Free plan, so rule 2 does not apply either. Rule 3 is therefore reached
explicitly: "If official evidence does not establish which commercial structure
applies, do not infer it from words such as 'monthly', 'included', or 'free'.
Use ``other`` and route the candidate for review until the structure is
evidenced." These are the first two applications of rule 3 in this repository,
and they are recorded here so a reviewer can check the determination against the
rule rather than against an author's intuition.

**``other`` is NOT a safety mechanism, and nothing here should be read as
implying it is.** MEASURED against the real engine: ``other`` appears in neither
``TEMPORARY_CONDITIONAL_OFFER_TYPES`` nor ``SELF_HOSTED_OFFER_TYPES``, so it is
gated nowhere, and ``other`` + ``requires_card=False`` +
``has_paid_dependencies=False`` + a safe stop classifies **Z0_TRUE_FREE**. The
Z0-capable offer types are ``always_free``, ``recurring_quota``,
``personal_use_free`` and ``other``. What actually withholds Z0 from these two
rule-3 offers is the unknown card and paid-dependency facts, plus the publication
gate -- **not the offer type**. Rule 3's "route the candidate for review" is an
instruction to the *author*, not a behaviour of the classifier. That is pinned by
``test_offer_type_other_is_not_a_safety_mechanism`` so the assumption is a tested
property rather than something a reader has to take on trust.

Note the SCOPE of that particular pin: it is Azure-scoped and ``other``-scoped,
and it establishes "gated nowhere" by naming two frozensets. The engine-wide
version of the same claim -- every offer type in the closed vocabulary swept
across the whole material input space, with measured Z0 reachability compared as
a set against what the engine's declared gates imply -- lives in
``tests/unit/test_z0_classifier.py``
(``test_z0_reachability_matches_the_engine_s_declared_gates`` and
``test_offer_type_other_is_z0_reachable_and_that_is_recorded_here``). That is
what catches a gate on ``offer_type`` appearing somewhere other than those two
frozensets, which the narrower pin cannot see. The list of Z0-capable types
above is therefore a statement of fact re-derived by measurement, not a
remembered one.

**Why DevOps is ``recurring_quota``.** Both legs of rule 2 are quoted from the
same document: the service is not itself zero-priced ("First 5 users free, then
$6 per user per month") and the free allowance replenishes on a schedule ("The
free amounts of build and cloud-based load testing reset on the first day of the
month"). Each leg is pinned as its own fact so the determination is visible in
the evidence rather than asserted in a comment.

**Why the 12 months free services offer is ``trial``.** It is not
``always_free``: its whole definition is a bounded introductory window. It is not
``new_customer_credit``: no credit is granted by it -- the $200 credit is the
separate free-account offer, pinned on a different document. It is not
``recurring_quota``: it is a one-off window rather than a replenishing grant.
Among the closed vocabulary ``trial`` is the only value matching "12 months free
services ... available only to new customers". The alternative reading ``other``
would also withhold Z0 (via gate 4 rather than gate 5), so this taxonomy choice
changes no Z0 verdict.
"""

from __future__ import annotations

from ..html import HtmlExtractionProfile, HtmlMatrixRow, HtmlTextAssertion
from . import register_html_profile

# --------------------------------------------------------------------------- #
# Azure free account (https://azure.microsoft.com/en-us/pricing/offers/        #
# ms-azr-0044p/) -- the ONLY page in this sweep stating the card requirement.  #
# --------------------------------------------------------------------------- #

#: The verbatim block that grants the credit, bounds it to 30 days, bounds the
#: free services to 12 months, and limits the offer to one account per customer.
#: Everything this profile knows about the offer's NATURE comes from this block.
FREE_ACCOUNT_CREDIT_AND_TERM = (
    "We offer eligible customers $200 in Azure credits (\u201cCredits\u201d) to be used within "
    "the first 30 days of sign-up and 12 months of select free services (services subject to "
    "change). This offer is limited to one Azure free account per eligible customer and cannot "
    "be combined with any other offer unless otherwise permitted by Microsoft."
)

#: The verbatim block restating the credit window and stating that unused credit
#: does not roll over. Carried whole so the no-rollover clause is not dropped.
FREE_ACCOUNT_CREDIT_WINDOW = (
    "With the Azure free account, eligible customers receive $200 in credits which can be used "
    "within the first 30 days on most Azure services. Any unused credits cannot be carried over "
    "to subsequent months and cannot be transferred to other Azure subscriptions."
)

#: THE exhaustion evidence: on credit exhaustion (or after 30 days) continuing
#: requires a MANUAL upgrade to pay-as-you-go. Quoted, not inferred.
FREE_ACCOUNT_UPGRADE_REQUIRED = (
    "Within 30 days of sign-up or upon exhaustion of the credits (whichever occurs first), you "
    "must upgrade to a pay-as-you-go subscription by removing the spending limit. This allows "
    "continued use of the Azure free account and select free services for the term."
)

#: The verbatim block listing what the credit may NOT buy. This is the closest
#: Azure comes to naming paid dependencies, and it is deliberately carried as a
#: note rather than mapped to ``has_paid_dependencies``: it says the credit
#: cannot be spent on those products, not that the offer depends on them.
FREE_ACCOUNT_CREDIT_EXCLUSIONS = (
    "Azure credits may not be used to purchase Azure support plans, Azure DevOps, Visual Studio "
    "subscriptions, Visual Studio App Center services, Azure ExpressRoute, third-party branded "
    "products, products sold through Azure Marketplace, or products otherwise licensed "
    "separately from Azure (for example, Microsoft Azure Active Directory Premium)."
)

#: THE unfavourable fact, quoted rather than inferred. It sits under the
#: "Payment options" heading of the Azure free account terms, so it describes
#: THIS offer's signup and no composition is involved.
FREE_ACCOUNT_PAYMENT_REQUIRED = (
    "All you need is a phone number, a credit card or a debit card (non-prepaid), and a "
    "Microsoft account or a GitHub account. Only credit cards are accepted in Hong Kong and "
    "Brazil."
)

#: Carried whole so the "will initially NOT be charged" qualification travels
#: with the card fact instead of being dropped -- and so that the reader also
#: sees the temporary authorization hold, which is a real charge-adjacent event.
FREE_ACCOUNT_PAYMENT_PURPOSE = (
    "Your credit card or debit card will initially NOT be charged, except for a temporary "
    "authorization hold. Any taxes which may result from receiving services at no charge are "
    "the sole responsibility of the recipient. More details on pricing and billing can be found "
    "on the pricing page."
)

#: The verbatim cancellation clause. "Payment is required for any outstanding
#: fees incurred" is the sentence that stops this offer being read as $0.
FREE_ACCOUNT_CANCELLATION = (
    "You may cancel at any time. Payment is required for any outstanding fees incurred."
)


# --------------------------------------------------------------------------- #
# 12 months free services (https://azure.microsoft.com/en-us/pricing/          #
# free-services/) -- the introductory window, from the hub's own footnote.     #
# --------------------------------------------------------------------------- #

#: The ONLY block on the free-services hub that states the offer's terms. The
#: rest of the page's free-service list is client-rendered. The leading "[*] *"
#: is live markup and is reproduced exactly, because whole-block equality does
#: not forgive it.
FREE_SERVICES_TWELVE_MONTH_TERMS = (
    "[*] *12 months free services is available only to new customers who have not previously had "
    "an Azure account or received 12 months of free services. It is not currently available to "
    "customers who sign up directly for pay as you go in China and India. Customers who try "
    "Azure free must move to pay as you go within 30 days to continue receiving 12 months free "
    "services."
)

#: Azure's own heading asserting that a perpetual free-service set exists. It is
#: carried as a published NOTE and deliberately NOT used as an offer identity:
#: it names no service, no allowance and no exhaustion behaviour, so pinning an
#: ``always_free`` offer to it would publish an offer backed by a strap line.
#: The perpetual offer this slice DOES publish is Cosmos DB, which states its
#: allowance, its perpetuity and its overage consequence in blocks of their own.
FREE_SERVICES_ALWAYS_FREE_HEADING = "65+ always-free services with an Azure account"

FREE_SERVICES_TWELVE_MONTH_HEADING = "Free for your first 12 months"


# --------------------------------------------------------------------------- #
# Azure Cosmos DB lifetime free tier -- THE HEADLINE FINDING.                  #
# --------------------------------------------------------------------------- #

#: The verbatim block that grants the allowance AND states the overage is
#: BILLED. Both facts in one quotation is what makes this offer's Z1 verdict
#: impossible to read as an author's summary.
COSMOS_ALLOWANCE_AND_OVERAGE = (
    "Azure Cosmos DB free tier makes it easy to get started, develop, test your applications, or "
    "even run small production workloads for free. When free tier is enabled on an account, you "
    "get the first 1000 RU/s and 25 GB of storage in the account for free. The throughput and "
    "storage consumed beyond these limits are billed at regular price. Free tier is available "
    "for all API accounts with provisioned throughput, autoscale throughput, single, or multiple "
    "write regions."
)

#: Rule 1 of docs/DATA_MODEL.md, satisfied by quotation. This is the only block
#: found anywhere in this sweep that states an Azure free tier is indefinite in
#: a block of its own, and it is what makes ``always_free`` a quotation here
#: rather than an inference from the word "free".
COSMOS_LASTS_INDEFINITELY = (
    "Free tier lasts indefinitely for the lifetime of the account and it comes with all the "
    "benefits and features of a regular Azure Cosmos DB account. These benefits include "
    "unlimited storage and throughput (RU/s), SLAs, high availability, turnkey global "
    "distribution in all Azure regions, and more."
)

#: The verbatim eligibility block: one free-tier account per subscription, and
#: it must be opted into at creation time. An allowance you must opt in to is
#: not the same as one you receive by default, and that distinction is published.
COSMOS_ONE_ACCOUNT_PER_SUBSCRIPTION = (
    "You can have up to one free tier Azure Cosmos DB account per an Azure subscription, and you "
    "must opt in when creating the account. If you don't see the option to apply the free tier "
    "discount, another account in the subscription has already been enabled with free tier. If "
    "you create an account with free tier and then delete it, you can apply free tier for a new "
    "account. When creating a new account, it\u2019s recommended to enable the free tier discount "
    "if it\u2019s available."
)

#: Microsoft's own block distinguishing this LIFETIME tier from the time-limited
#: Azure free account. It is pinned precisely because conflating those two is
#: the single most likely way to publish a false perpetual claim for Azure.
COSMOS_NOT_THE_FREE_ACCOUNT = (
    "Azure Cosmos DB free tier is different from the Azure free account. The Azure free account "
    "offers Azure credits and resources for free for a limited time. When using Azure Cosmos DB "
    "as a part of this free account, you get 25-GB storage and 400 RU/s of provisioned "
    "throughput for 12 months."
)

#: The verbatim condition under which the account actually stays at $0.
COSMOS_KEEP_ACCOUNT_FREE = (
    "To keep your account free of charge, your account shouldn't have any more RU/s or storage "
    "consumption other than the one offered by the Azure Cosmos DB free tier."
)


# --------------------------------------------------------------------------- #
# Azure App Service Free and Shared plans -- the SAFE exhaustion behaviour.    #
# --------------------------------------------------------------------------- #

#: THE decisive fact, and the one that makes this the closest Azure offer to Z0:
#: exceeding the quota STOPS the app rather than billing for it, which clears the
#: billing gate entirely. It does NOT make the offer one fact from Z0 -- both
#: ``requires_card`` and ``has_paid_dependencies`` remain unknown on this
#: document, so the offer is TWO unknowns away. Deleting this block must reject
#: the document rather than leave the offer's exhaustion merely unknown.
APP_SERVICE_QUOTA_STOP = (
    "If an app exceeds the CPU (Short), CPU (Day), or Bandwidth quota, the app is stopped until "
    "the quota resets. During this time, all incoming requests result in an HTTP 403 error."
)

#: The verbatim block that scopes the quota table to the FREE (and Shared) plan.
#: Without it the table would be a set of numbers belonging to no offer.
APP_SERVICE_FREE_PLAN_SCOPE = (
    "If the app is hosted in a Free or Shared plan, quotas define the limits on the resources "
    "that the app can use. Quotas for apps in a Free or Shared plan are:"
)

#: Microsoft's own statement of what the base tiers are FOR. Published because a
#: catalogue reader choosing a free tier should see "development and testing"
#: from the provider rather than discover it later.
APP_SERVICE_BASE_TIER_INTENT = (
    "App Service Free and Shared (preview) service plans are base tiers that run on the same "
    "Azure virtual machines as other App Service apps. Some apps might belong to other "
    "customers. These tiers are intended only for development and testing purposes."
)

APP_SERVICE_MEMORY_STOP = "If the app exceeds its Memory quota, it stops temporarily."

APP_SERVICE_FILESYSTEM_STOP = (
    "If app exceeds the Filesystem quota, any write operation fails. Write operation failures "
    "include any writes to logs."
)

APP_SERVICE_QUOTA_INCREASE = (
    "You can increase or remove quotas from your app by upgrading your App Service plan."
)


# --------------------------------------------------------------------------- #
# Azure Static Web Apps hosting plans.                                         #
# --------------------------------------------------------------------------- #

#: The verbatim block that identifies a Free plan and states that the OTHER plan
#: costs money. It establishes that a free plan exists; it establishes nothing
#: about how long it lasts, which is why the offer type is ``other``.
STATIC_WEB_APPS_TWO_PLANS = (
    "Azure Static Web Apps is available through two different plans, Free and Standard. See the "
    "pricing page for Standard plan costs. For information service level agreement details, see "
    "Service Level Agreements (SLA) for Online Services."
)

STATIC_WEB_APPS_PLAN_CHANGE = "You can move between Free or Standard plans via the Azure portal."


# --------------------------------------------------------------------------- #
# Azure DevOps Services -- rule 2 satisfied in both legs.                      #
# --------------------------------------------------------------------------- #

#: Rule-2 leg 1: the containing service is itself metered and priced.
DEVOPS_FIRST_FIVE_USERS_FREE = "First 5 users free, then $6 per user per month"

#: Rule-2 leg 3: the allowance and its monthly cadence. The block also states
#: how to buy MORE, which is deliberately NOT read as an exhaustion behaviour:
#: it says what to do to obtain more capacity, never what happens if you do not.
DEVOPS_PARALLEL_JOB_GRANT = (
    "Each Azure DevOps organization gets one parallel job with 1,800 minutes (30 hours) of build "
    "time every month using Microsoft-hosted agents. If you need more time or would like to run "
    "more than one job at a time, simply buy the number of pipelines you need. When you buy your "
    "first Microsoft-hosted pipeline, the number of parallel jobs in the organization remains at "
    "one; this purchase removes only the time limit on the free pipeline. To run two jobs "
    "concurrently, buy two Microsoft-hosted pipelines."
)

#: The second published allowance, carried whole so the self-hosted grant is not
#: silently folded into the Microsoft-hosted one.
DEVOPS_SELF_HOSTED_AGENT_GRANT = (
    "Additionally, each Azure DevOps organization gets one free self-hosted agent that can be "
    "used to run one parallel job with unlimited minutes. If you need to run more parallel jobs, "
    "simply buy more self-hosted pipelines."
)

#: Rule-2 leg 2: the free amounts RESET on a schedule. This is the block that
#: makes ``recurring_quota`` a quotation rather than an inference from the word
#: "month" appearing in a grant description.
DEVOPS_FREE_AMOUNTS_RESET = (
    "When do I get charged? All charges appear on your next monthly invoice. Charges are based "
    "on the number of users and build and deployment agents you purchased during the month, plus "
    "the actual usage of other services that were used. The free amounts of build and "
    "cloud-based load testing reset on the first day of the month."
)


# --------------------------------------------------------------------------- #
# Azure for Students.                                                          #
# --------------------------------------------------------------------------- #

#: The verbatim eligibility bullet. It is the block the offer TYPE is pinned to,
#: because eligibility is what makes this a programme rather than a plan.
STUDENTS_ELIGIBILITY = "Available only to full-time university students*"

#: The ONLY block in this entire sweep stating that a card is not required. It
#: is PUBLISHED here as a note and deliberately not converted into
#: ``requires_card=False`` -- see the module docstring for the reason and for the
#: proof that the choice is verdict-neutral.
STUDENTS_CARD_CLAIM = "No credit card required"

STUDENTS_TWELVE_MONTH_SERVICES = (
    "Free monthly amounts of 20+ popular services for 12 months (new Azure customers only)"
)

STUDENTS_ALWAYS_FREE_SERVICES = "Free monthly amounts of 65+ always-free services"

STUDENTS_CREDIT_AND_CATALOG = (
    "Access to full catalog of services up to free amounts and $100 credit"
)

STUDENTS_ANNUAL_RENEWAL = (
    "Renew your subscription annually and continue to get free access to Azure as long as you're "
    "a student"
)


def _rows(*pairs: tuple[str, str]) -> dict[str, HtmlMatrixRow]:
    return {label: HtmlMatrixRow(field) for label, field in pairs}


AZURE_FREE_ACCOUNT = register_html_profile(
    HtmlExtractionProfile(
        name="azure_free_account",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            # Identity is pinned to the page's own terms heading rather than to
            # the document title ("Azure Free Trial | Microsoft Azure"), which is
            # marketing copy naming an offer Microsoft no longer calls a trial.
            HtmlTextAssertion(
                text="Azure free account terms & conditions",
                field="service",
                value="Azure free account",
                scope="heading",
            ),
            # The credit is what this offer IS, so the offer type is pinned to
            # the block that grants it. A rewritten credit sentence rejects the
            # document rather than leaving a stale `new_customer_credit` behind.
            HtmlTextAssertion(
                text=FREE_ACCOUNT_CREDIT_AND_TERM,
                field="offer_type",
                value="new_customer_credit",
            ),
            HtmlTextAssertion(
                text=FREE_ACCOUNT_CREDIT_AND_TERM,
                field="credit_amount",
                value="$200",
            ),
            HtmlTextAssertion(
                text=FREE_ACCOUNT_CREDIT_AND_TERM,
                field="credit_validity_days",
                value="30",
            ),
            HtmlTextAssertion(
                text=FREE_ACCOUNT_CREDIT_AND_TERM,
                field="free_services_term_months",
                value="12",
            ),
            HtmlTextAssertion(
                text=FREE_ACCOUNT_CREDIT_AND_TERM,
                field="eligibility",
                value=FREE_ACCOUNT_CREDIT_AND_TERM,
            ),
            HtmlTextAssertion(
                text=FREE_ACCOUNT_CREDIT_WINDOW,
                field="quota_basis",
                value=FREE_ACCOUNT_CREDIT_WINDOW,
            ),
            # Quoted exhaustion: continuing after the credits run out requires a
            # MANUAL upgrade. Not automatic billing, and not a safe stop either.
            HtmlTextAssertion(
                text=FREE_ACCOUNT_UPGRADE_REQUIRED,
                field="exhaustion_behaviour",
                value="manual_upgrade_required",
            ),
            HtmlTextAssertion(
                text=FREE_ACCOUNT_UPGRADE_REQUIRED,
                field="notes",
                value=FREE_ACCOUNT_UPGRADE_REQUIRED,
            ),
            # Deliberately a note, NOT `has_paid_dependencies`: the block says
            # the credit cannot be SPENT on those products, which is not the
            # same claim as the offer depending on a paid product.
            HtmlTextAssertion(
                text=FREE_ACCOUNT_CREDIT_EXCLUSIONS,
                field="credit_exclusions",
                value=FREE_ACCOUNT_CREDIT_EXCLUSIONS,
            ),
            # THE unfavourable fact, quoted rather than inferred.
            HtmlTextAssertion(
                text=FREE_ACCOUNT_PAYMENT_REQUIRED,
                field="requires_card",
                value=True,
            ),
            HtmlTextAssertion(
                text=FREE_ACCOUNT_PAYMENT_REQUIRED,
                field="payment_method_basis",
                value=FREE_ACCOUNT_PAYMENT_REQUIRED,
            ),
            HtmlTextAssertion(
                text=FREE_ACCOUNT_PAYMENT_PURPOSE,
                field="payment_method_purpose",
                value=FREE_ACCOUNT_PAYMENT_PURPOSE,
            ),
            HtmlTextAssertion(
                text=FREE_ACCOUNT_CANCELLATION,
                field="cancellation_policy",
                value=FREE_ACCOUNT_CANCELLATION,
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


AZURE_FREE_SERVICES = register_html_profile(
    HtmlExtractionProfile(
        name="azure_free_services",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text=FREE_SERVICES_TWELVE_MONTH_TERMS,
                field="service",
                value="Azure 12 months free services",
            ),
            # The offer type is pinned to the block that BOUNDS the offer, so a
            # reworded term cannot leave a stale `trial` behind.
            HtmlTextAssertion(
                text=FREE_SERVICES_TWELVE_MONTH_TERMS,
                field="offer_type",
                value="trial",
            ),
            HtmlTextAssertion(
                text=FREE_SERVICES_TWELVE_MONTH_TERMS,
                field="trial_length_months",
                value="12",
            ),
            HtmlTextAssertion(
                text=FREE_SERVICES_TWELVE_MONTH_TERMS,
                field="eligibility",
                value=FREE_SERVICES_TWELVE_MONTH_TERMS,
            ),
            HtmlTextAssertion(
                text=FREE_SERVICES_TWELVE_MONTH_HEADING,
                field="term_heading",
                value=FREE_SERVICES_TWELVE_MONTH_HEADING,
                scope="heading",
            ),
            # The favourable finding, PUBLISHED rather than omitted -- and
            # deliberately not used as an offer identity. See the docstring.
            HtmlTextAssertion(
                text=FREE_SERVICES_ALWAYS_FREE_HEADING,
                field="always_free_services_note",
                value=FREE_SERVICES_ALWAYS_FREE_HEADING,
                scope="heading",
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


AZURE_COSMOS_DB_FREE_TIER = register_html_profile(
    HtmlExtractionProfile(
        name="azure_cosmos_db_free_tier",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="Lifetime Free Tier - Azure Cosmos DB | Microsoft Learn",
                field="service",
                value="Azure Cosmos DB",
                scope="title",
            ),
            # Rule 1 of docs/DATA_MODEL.md: the provider identifies an
            # indefinitely available free tier and the allowance belongs to it.
            HtmlTextAssertion(
                text=COSMOS_LASTS_INDEFINITELY,
                field="offer_type",
                value="always_free",
            ),
            HtmlTextAssertion(
                text=COSMOS_LASTS_INDEFINITELY,
                field="availability",
                value=COSMOS_LASTS_INDEFINITELY,
            ),
            HtmlTextAssertion(
                text=COSMOS_ALLOWANCE_AND_OVERAGE,
                field="free_request_units_per_second",
                value="1000",
            ),
            HtmlTextAssertion(
                text=COSMOS_ALLOWANCE_AND_OVERAGE,
                field="free_storage",
                value="25 GB",
            ),
            HtmlTextAssertion(
                text=COSMOS_ALLOWANCE_AND_OVERAGE,
                field="quota_basis",
                value=COSMOS_ALLOWANCE_AND_OVERAGE,
            ),
            # THE decisive fact. A perpetual allowance whose overage is billed is
            # still a billing exposure, so this offer is Z1 and NOT Z0. Deleting
            # this block must reject the document rather than leave a perpetual
            # offer looking unconditionally free.
            HtmlTextAssertion(
                text=COSMOS_ALLOWANCE_AND_OVERAGE,
                field="exhaustion_behaviour",
                value="automatic_billing",
            ),
            HtmlTextAssertion(
                text=COSMOS_ONE_ACCOUNT_PER_SUBSCRIPTION,
                field="eligibility",
                value=COSMOS_ONE_ACCOUNT_PER_SUBSCRIPTION,
            ),
            HtmlTextAssertion(
                text=COSMOS_NOT_THE_FREE_ACCOUNT,
                field="notes",
                value=COSMOS_NOT_THE_FREE_ACCOUNT,
            ),
            HtmlTextAssertion(
                text=COSMOS_KEEP_ACCOUNT_FREE,
                field="free_usage_condition",
                value=COSMOS_KEEP_ACCOUNT_FREE,
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


AZURE_APP_SERVICE_QUOTAS = register_html_profile(
    HtmlExtractionProfile(
        name="azure_app_service_quotas",
        header_signature=("Quota", "Description"),
        mode="matrix",
        matrix_metric_header="Quota",
        matrix_tier_header="Description",
        # All 5 live quota rows, in live order. Completeness is the guard: a
        # quota Microsoft adds later rejects the document
        # (`unknown_matrix_rows`) instead of disappearing from the published set.
        matrix_rows=_rows(
            ("CPU (Short)", "cpu_short_quota"),
            ("CPU (Day)", "cpu_day_quota"),
            ("Memory", "memory_quota"),
            ("Bandwidth", "bandwidth_quota"),
            ("Filesystem", "filesystem_quota"),
        ),
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="Azure App Service Quotas and Metrics - Azure App Service | Microsoft Learn",
                field="service",
                value="Azure App Service",
                scope="title",
            ),
            # docs/DATA_MODEL.md rule 3, applied explicitly: the page identifies
            # a Free plan but states neither an end date nor perpetuity, so the
            # commercial structure is NOT established and must not be inferred
            # from the word "free". `other` routes the candidate for review.
            HtmlTextAssertion(
                text=APP_SERVICE_FREE_PLAN_SCOPE,
                field="offer_type",
                value="other",
            ),
            HtmlTextAssertion(
                text=APP_SERVICE_FREE_PLAN_SCOPE,
                field="quota_basis",
                value=APP_SERVICE_FREE_PLAN_SCOPE,
            ),
            # THE decisive fact, and the only SAFE exhaustion behaviour found on
            # any Azure page in this sweep.
            HtmlTextAssertion(
                text=APP_SERVICE_QUOTA_STOP,
                field="exhaustion_behaviour",
                value="site_disabled_until_reset",
            ),
            HtmlTextAssertion(
                text=APP_SERVICE_QUOTA_STOP,
                field="exhaustion_basis",
                value=APP_SERVICE_QUOTA_STOP,
            ),
            HtmlTextAssertion(
                text=APP_SERVICE_MEMORY_STOP,
                field="memory_exhaustion_note",
                value=APP_SERVICE_MEMORY_STOP,
            ),
            HtmlTextAssertion(
                text=APP_SERVICE_FILESYSTEM_STOP,
                field="filesystem_exhaustion_note",
                value=APP_SERVICE_FILESYSTEM_STOP,
            ),
            HtmlTextAssertion(
                text=APP_SERVICE_BASE_TIER_INTENT,
                field="notes",
                value=APP_SERVICE_BASE_TIER_INTENT,
            ),
            HtmlTextAssertion(
                text=APP_SERVICE_QUOTA_INCREASE,
                field="quota_increase_note",
                value=APP_SERVICE_QUOTA_INCREASE,
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


AZURE_STATIC_WEB_APPS_PLANS = register_html_profile(
    HtmlExtractionProfile(
        name="azure_static_web_apps_plans",
        # The live header row also carries a fourth column, "Dedicated plan
        # (Retired effective October 31st, 2025)". It is deliberately NOT part of
        # the signature: the signature is a SUBSET match, and pinning a retiring
        # plan's label would make this profile fail on the day Microsoft removes
        # a column that carries none of the facts read here.
        header_signature=(
            "Feature",
            "Free plan (For personal projects)",
            "Standard plan (For production apps)",
        ),
        mode="matrix",
        matrix_metric_header="Feature",
        matrix_tier_header="Free plan (For personal projects)",
        # All 13 live feature rows, in live order.
        matrix_rows=_rows(
            ("Web hosting", "web_hosting"),
            ("GitHub integration", "github_integration"),
            ("Azure DevOps integration", "azure_devops_integration"),
            ("Globally distributed static content", "globally_distributed_static_content"),
            ("Free, automatically renewing SSL certificates", "free_ssl_certificates"),
            ("Staging environments", "staging_environments"),
            ("Max app size", "max_app_size"),
            ("Custom domains", "custom_domains"),
            ("APIs via Azure Functions", "apis_via_azure_functions"),
            ("Authentication provider integration", "authentication_provider_integration"),
            ("Assign custom roles with a function", "assign_custom_roles_with_a_function"),
            ("Private endpoints", "private_endpoints"),
            ("Service Level Agreement (SLA)", "service_level_agreement"),
        ),
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="Azure Static Web Apps hosting plans | Microsoft Learn",
                field="service",
                value="Azure Static Web Apps",
                scope="title",
            ),
            # docs/DATA_MODEL.md rule 3 again: a Free plan exists and Standard
            # costs money, but nothing on this document establishes how long the
            # Free plan lasts. `other`, routed for review.
            HtmlTextAssertion(
                text=STATIC_WEB_APPS_TWO_PLANS,
                field="offer_type",
                value="other",
            ),
            HtmlTextAssertion(
                text=STATIC_WEB_APPS_TWO_PLANS,
                field="plan_structure",
                value=STATIC_WEB_APPS_TWO_PLANS,
            ),
            HtmlTextAssertion(
                text=STATIC_WEB_APPS_PLAN_CHANGE,
                field="notes",
                value=STATIC_WEB_APPS_PLAN_CHANGE,
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


AZURE_DEVOPS_SERVICES = register_html_profile(
    HtmlExtractionProfile(
        name="azure_devops_services",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="Azure DevOps Services Pricing | Microsoft Azure",
                field="service",
                value="Azure DevOps Services",
                scope="title",
            ),
            # Rule-2 leg 2: the free amounts RESET on a schedule. Pinning the
            # offer type here rather than to the grant means a reworded reset
            # clause cannot leave a stale `recurring_quota` behind.
            HtmlTextAssertion(
                text=DEVOPS_FREE_AMOUNTS_RESET,
                field="offer_type",
                value="recurring_quota",
            ),
            HtmlTextAssertion(
                text=DEVOPS_FREE_AMOUNTS_RESET,
                field="reset_basis",
                value=DEVOPS_FREE_AMOUNTS_RESET,
            ),
            # Rule-2 leg 1: the containing service is itself metered and priced.
            HtmlTextAssertion(
                text=DEVOPS_FIRST_FIVE_USERS_FREE,
                field="billing_basis",
                value=DEVOPS_FIRST_FIVE_USERS_FREE,
            ),
            HtmlTextAssertion(
                text=DEVOPS_PARALLEL_JOB_GRANT,
                field="quota_basis",
                value=DEVOPS_PARALLEL_JOB_GRANT,
            ),
            HtmlTextAssertion(
                text=DEVOPS_SELF_HOSTED_AGENT_GRANT,
                field="self_hosted_agent_grant",
                value=DEVOPS_SELF_HOSTED_AGENT_GRANT,
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


AZURE_STUDENTS = register_html_profile(
    HtmlExtractionProfile(
        name="azure_students",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="Azure for Students",
                field="service",
                value="Azure for Students",
                scope="heading",
            ),
            # Eligibility is what makes this a programme rather than a plan, so
            # the offer type is pinned to the eligibility bullet.
            HtmlTextAssertion(
                text=STUDENTS_ELIGIBILITY,
                field="offer_type",
                value="student_program",
            ),
            HtmlTextAssertion(
                text=STUDENTS_ELIGIBILITY,
                field="eligibility",
                value=STUDENTS_ELIGIBILITY,
            ),
            # PUBLISHED, but deliberately NOT `requires_card=False`. See the
            # module docstring: the page carries two offers with opposite card
            # terms, and this bullet names neither.
            HtmlTextAssertion(
                text=STUDENTS_CARD_CLAIM,
                field="card_claim",
                value=STUDENTS_CARD_CLAIM,
            ),
            HtmlTextAssertion(
                text=STUDENTS_TWELVE_MONTH_SERVICES,
                field="twelve_month_services",
                value=STUDENTS_TWELVE_MONTH_SERVICES,
            ),
            HtmlTextAssertion(
                text=STUDENTS_ALWAYS_FREE_SERVICES,
                field="always_free_services_note",
                value=STUDENTS_ALWAYS_FREE_SERVICES,
            ),
            HtmlTextAssertion(
                text=STUDENTS_CREDIT_AND_CATALOG,
                field="credit_and_catalog",
                value=STUDENTS_CREDIT_AND_CATALOG,
            ),
            HtmlTextAssertion(
                text=STUDENTS_ANNUAL_RENEWAL,
                field="renewal",
                value=STUDENTS_ANNUAL_RENEWAL,
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)
