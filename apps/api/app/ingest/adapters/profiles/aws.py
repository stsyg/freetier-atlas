"""AWS OFFICIAL free-tier extraction profiles (F008 P4).

Provider-specific selectors expressed purely as **data**, registered through the
F008 S3 seam so adding AWS touches no shared module.

Every profile here was derived from the live ``aws.amazon.com`` pages on
2026-08-14. Four structural facts about those pages drive the shapes below, and
all four were MEASURED with the repository's own parser rather than assumed:

* **AWS marketing and documentation pages are largely client-rendered, and two of
  them are entirely so.** ``docs.aws.amazon.com`` served 1166 bytes for the
  Billing guide's free-tier page and 1083 bytes for the Lambda billing page; both
  parse to zero tables, zero headings and zero body blocks. Their content is not
  in the served HTML, so neither page is used here. That is reported rather than
  worked around.
* **Only one AWS page in the sweep publishes free-tier facts in a table the
  engine can select.** ``https://aws.amazon.com/free/`` carries exactly one
  ``<table>``, with the live header row ``Benefits`` / ``Free plan`` /
  ``Paid plan`` and 6 body rows. Every other probed page either has no table with
  a header row at all, or its header-row tables are pay-as-you-go price tables
  belonging to no free offer. Measured across 13 pricing pages, ``_header_row``
  returned ``expected one header row, found 0`` for 4/4 Lambda tables, 3/3
  DynamoDB tables and 8/8 S3 tables.
* **The remaining five sources publish their terms entirely as prose**, so they
  are ``mode="assertions"``: they declare no table selector, read no table, and
  take 100% of their facts from blocks pinned verbatim. Nothing is synthesized to
  satisfy the extractor, and on three of those five documents nothing *could*
  have been taken from a table because the live page has none.
* **AWS never describes one offer kind in isolation.** That shapes every identity
  pin below and is the reason one whole offer kind is deliberately absent -- see
  "Why no ``always_free`` offer is extracted".

**The three AWS offer kinds must never be conflated.** AWS publishes perpetual
"Always Free" offers, a time-limited introductory tier, and short-term trials and
credits, all under the single brand "AWS Free Tier". Only the first could ever be
perpetual. The profiles here keep them apart by pinning each offer's identity,
its offer type and its expiry to blocks that describe *that* offer, and by
declining to extract the one kind AWS does not describe in a block of its own.

**Why no ``always_free`` offer is taken from the FREE TIER HUB pages, and where
one IS taken from.** MEASURED on the live FAQ page: the "always free" vocabulary
appears only inside blocks that simultaneously describe something else. General
Q1 defines Always Free in the same block that grants the $200 credit; General Q3
does the same; the fullest definition sits inside the answer to a **paid plan**
question; and the only always-free-scoped heading is a question, not a
description. Pinning an ``always_free`` identity to any of them would splice one
offer's identity onto another offer's context, which is inference rather than
quotation, so none of the three hub sources emits ``always_free``.

An earlier draft of this module concluded from that alone that AWS never
describes a perpetual offer in a block of its own. **That conclusion was wrong,
and it was wrong in the favourable-to-omission direction.** Widening the sweep to
service pricing pages found that AWS does state it plainly, per service:
``https://aws.amazon.com/step-functions/pricing/`` says, in a block of its own,
"The Step Functions Free Tier does not automatically expire at the end of your 12
month AWS Free Tier term, and is available to both existing and new AWS customers
indefinitely." That is rule 1 of ``docs/DATA_MODEL.md`` satisfied by quotation, so
:data:`AWS_STEP_FUNCTIONS_FREE_TIER` is ``always_free``.

**And it is still not Z0.** The same page states "You are charged per state
transition above the free tier", which is ``automatic_billing`` and therefore a
definite billing exposure. A perpetual AWS allowance whose overage is billed is
``Z1_BILLING_EXPOSURE``, and that pairing -- indefinite *and* billed -- is the
single most important thing this module demonstrates.

The most important always-free fact from the hub pages is still published,
carried whole as :data:`FAQ_OVERAGE_BEGINS_CHARGES` on the free-plan offer's
``overage_note``.

**The unfavourable findings are published, not omitted.** The load-bearing
sentence for anyone hoping an AWS account is free to open is, verbatim::

    Yes, you are required to provide a valid payment method to sign up for an
    AWS account, whether you choose a free plan or a paid plan.

That block names the **free plan** explicitly, so ``requires_card = True`` on the
free-plan offer is a quotation and not a composition of two blocks. It makes that
offer ``Z1_BILLING_EXPOSURE``. The second load-bearing sentence is::

    If you exceed the free usage limits, your account will begin incurring
    charges at the standard pay-as-you-go service rates ...

**Why ``requires_card`` is UNKNOWN on the other three profiles.** The
payment-method block lives on the FAQ page and names the free plan. The plan
comparison page, the Free Tier Terms and the DynamoDB pricing page each state
nothing about a payment method. Carrying the FAQ sentence onto them would be
cross-document composition -- exactly the error the Google Cloud slice avoided
when it recorded ``billing_account: required`` rather than inventing a card
claim -- so ``requires_card`` is simply absent there and the resulting verdict is
``UNKNOWN``. No AWS profile in this module claims a card is *not* required.

**Why ``exhaustion_behaviour`` is UNKNOWN on two profiles.** The Free Tier Terms'
Legacy section states no exhaustion behaviour for the 12 Month Free Tier, and the
DynamoDB pricing page states overage charges only inside worked pricing examples
tied to specific invented workloads. Deriving a general exhaustion rule from a
worked example would be inference, so both are left unknown. The classifier's
gate 4 therefore returns ``UNKNOWN`` for them, which is the honest outcome and
still withholds Z0. Contrast API Gateway, whose page states the consequence
directly -- "If you exceed this number of calls per month, you will be charged
the API Gateway usage rates" -- so ``automatic_billing`` there is a quotation and
that offer classifies ``Z1_BILLING_EXPOSURE``.

**Why DynamoDB is ``recurring_quota``.** ``docs/DATA_MODEL.md`` -> "Choosing
between ``always_free`` and ``recurring_quota``" is applied in order, from
official evidence only. Rule 1 needs the provider to identify an indefinitely
available zero-priced plan, tier or SKU; the DynamoDB page identifies none.
Rule 2 covers a free allowance that replenishes on a schedule while the
containing service is not itself zero-priced: the page states the free tier
"offers the following benefits **each month**", and separately that the service
has "different pricing for data storage, reads, and writes". Both legs are pinned
as their own facts so the determination is visible in the evidence rather than
asserted in a comment.

**Why the 12 Month Free Tier is ``trial``.** It is not ``always_free``: that value
requires no stated end date, and this offer's whole definition is a stated
12-month introductory period. It is not ``new_customer_credit``: no credit is
granted by it. It is not ``recurring_quota``: the offer is a one-off window, not a
replenishing grant. Among the closed vocabulary ``trial`` is the only value that
matches "Offers that apply for a 12 month introductory period". The alternative
reading, ``other``, would also withhold Z0 (via gate 4 rather than gate 5), so
this taxonomy choice changes no Z0 verdict; it is recorded here so a reviewer can
check it against the rule rather than against an author's intuition. The same
reasoning, applied to the same words, makes the API Gateway free tier ``trial``
too: its own sentence bounds it to "up to 12 months".
"""

from __future__ import annotations

from collections.abc import Sequence

from ..html import HtmlExtractionProfile, HtmlMatrixRow, HtmlTextAssertion
from . import register_html_profile

#: The verbatim block that proves a payment method is required to open ANY AWS
#: account, including a free-plan one. This is the single most important fact in
#: this module, and it is an UNFAVOURABLE one.
FAQ_PAYMENT_METHOD_REQUIRED = (
    "Yes, you are required to provide a valid payment method to sign up for an AWS account, "
    "whether you choose a free plan or a paid plan."
)

#: The verbatim block that explains WHY the payment method is required and when
#: it is charged. Carried whole so the "will not charge until you upgrade"
#: qualification travels with the card fact instead of being dropped.
FAQ_PAYMENT_METHOD_PURPOSE = (
    "AWS requires a valid payment method to verify your identity and prevent abuse of AWS "
    "resources. AWS will not charge your payment method until you upgrade to paid plan, and you "
    "will not need to reenter your payment method when you upgrade."
)

#: The verbatim block that proves exceeding an AWS free usage limit BEGINS
#: CHARGES at standard rates. It is carried whole as a note rather than converted
#: into ``exhaustion_behaviour`` for the free plan, because the same block states
#: that a free-plan account sees no charges on its bills -- the credits absorb
#: them. Converting it would misattribute the paid-plan half of the answer to the
#: free plan.
FAQ_OVERAGE_BEGINS_CHARGES = (
    "If you exceed the free usage limits, your account will begin incurring charges at the "
    "standard pay-as-you-go service rates (see each service page for full pricing details). Your "
    "Free Tier credits will automatically apply to these charges. If your account is under the "
    "free plan, you won\u2019t see any charges on your AWS Bills. If your account is under a paid "
    "plan, you\u2019ll simply pay for charges that exceed your Free Tier credit balance."
)

#: The verbatim block that establishes what happens when the free plan runs out:
#: the account is CLOSED and continuing requires a manual upgrade to a paid plan.
FAQ_PLAN_CLOSURE = (
    "When your free plan expires, AWS closes your account, and you\u2019ll lose access to your "
    "resources and data. AWS will retain your data for 90 days after your free plan expires. "
    "During this period, you have the option to upgrade to paid plan to reopen your account and "
    "restore access to your resources. If you don\u2019t upgrade your account within 90 days, AWS "
    "will permanently erase your AWS account and all its content."
)

#: The verbatim block that ties the free plan to a 6-month window AND to credit
#: exhaustion. Pinning the offer type here rather than to the purpose sentence
#: means a reworded duration cannot leave a stale offer type behind.
FAQ_PLAN_EXPIRY = (
    "Your free plan expires the earlier of (1) 6-months from the date you opened your AWS "
    "account, or (2) once you have exhausted your Free Tier credits."
)

#: The verbatim block naming the two kinds of Offer in AWS's own legal language.
#: It is the only place AWS states the 12 Month Free Tier's name and nature.
TERMS_TWO_KINDS_OF_OFFER = (
    "The AWS Free Tier program consists of offers (\u201cOffers\u201d) for use of AWS Services "
    "under the AWS Service Terms and the terms of the AWS Customer Agreement or other agreement "
    'with us governing your use of AWS Services (the "Agreement"). There are two kinds of Offers: '
    "(1) Offers that apply for a 12 month introductory period (\u201c12 Month Free Tier\u201d); "
    "and (2) other Offers including trials and Offers without a set duration limit (generally "
    "called \u201cOther Offers\u201d). Both kinds of Offers are further described on the AWS Free "
    "Tier page."
)

#: The verbatim block stating the 12 Month Free Tier's duration and its
#: new-customers-only eligibility.
TERMS_12_MONTH_AVAILABILITY = (
    "The 12 Month Free Tier is only available to new AWS customers, and is available for 12 "
    "months following your AWS sign-up date. The Other Offers are available to both existing and "
    "new AWS customers, and may be limited in duration (such as for trials) or in available free "
    "usage (such as the amount of free storage for a database Offer). You will not be eligible "
    "for any Offers if you or your entity create(s) more than one account to receive additional "
    "benefits under the Offers. An Organization (under AWS Organizations) can only benefit from "
    "Offers from one account in the Organization, and to calculate the Organization\u2019s use of "
    "AWS Services under any Offers, we will aggregate the usage across all accounts in the "
    "Organization. You will be charged standard rates for use of AWS Services if we determine "
    "that you are not eligible for an Offer."
)

#: The verbatim block that establishes the DynamoDB free allowance exists, that it
#: replenishes MONTHLY, and on what basis it is counted. This is rule-2 leg 3.
DDB_FREE_TIER_IS_MONTHLY = (
    "The DynamoDB free tier is enough for about 200M requests/month (depending on item size) and "
    "can be used for personal apps, prototypes, or learning/certification needs. It uses "
    "provisioned capacity and the DynamoDB Standard table class. The DynamoDB free tier offers "
    "the following benefits each month on a per Region, per-payer account basis:"
)

#: Rule-2 leg 1: the containing service is itself metered and priced.
DDB_SERVICE_IS_PRICED = (
    "DynamoDB offers two table classes, with different pricing for data storage, reads, and "
    "writes. Both table classes offer similar performance but allow you to optimize costs based "
    "on your access patterns. The DynamoDB Standard table class is the default and recommended "
    "for most workloads. The DynamoDB Standard-Infrequent Access (Standard-IA) table class is "
    "best suited for data that is accessed infrequently and storage is the dominant cost. Learn "
    "more about DynamoDB table classes."
)

#: Carried whole so the credit cross-reference travels with the DynamoDB offer
#: instead of being silently discarded or, worse, mistaken for part of the
#: perpetual monthly allowance. The $200 credit is a SEPARATE, time-limited offer.
DDB_CREDIT_CROSS_REFERENCE = (
    "In addition to the DynamoDB free tier, you can get up to $200 USD in credits with the AWS "
    "Free Tier to experience the full set of DynamoDB features for up to 6 months. Access your "
    "DynamoDB free tier."
)

#: The verbatim block on the plan-comparison page that grants the credit, bounds
#: it to 6 months, and states the account closes. Everything the plan-comparison
#: profile knows about the offer's nature comes from this one block.
FREE_TIER_CREDIT_AND_WINDOW = (
    "When you create a new AWS Free Tier account, you get $100 in credits immediately. As you "
    "explore key services, you can earn up to $100 more. That's up to $200 over 6 months to "
    "build, break things, and experiment with no charges and no surprise bills on the Free plan. "
    "The account closes on its own 6 months after you open it or when your credits run out, "
    "whichever comes first. You won\u2019t be charged unless you convert to a Paid plan."
)


#: The verbatim block that states the API Gateway allowance, bounds it to 12
#: months, AND states that exceeding it is CHARGED. It is the only block found on
#: any AWS page in this sweep that carries all three in one quotation, which is
#: why this service is covered at all.
API_GATEWAY_FREE_TIER_AND_OVERAGE = (
    "The Amazon API Gateway free tier includes one million API calls received for REST APIs, one "
    "million API calls received for HTTP APIs, and one million messages and 750,000 connection "
    "minutes for WebSocket APIs per month for up to 12 months. If you exceed this number of calls "
    "per month, you will be charged the API Gateway usage rates."
)

#: The verbatim block that restates the 12-month bound, the new-customer
#: eligibility, and pay-as-you-go rates on expiry or overage.
API_GATEWAY_TERM_AND_EXPIRY = (
    "These free tier offers are only available to new AWS customers, and are available for 12 "
    "months following your AWS sign-up date. When your 12 month free usage term expires or if "
    "your application use exceeds the tiers, you simply pay standard, pay-as-you-go service rates."
)


#: The verbatim block that makes the Step Functions free tier PERPETUAL in AWS's
#: own words. This is the only block found anywhere in this sweep that states an
#: AWS free offer is indefinite in a block of its own, and it is what makes
#: ``always_free`` a quotation here rather than an inference.
STEP_FUNCTIONS_INDEFINITE = (
    "The Step Functions Free Tier does not automatically expire at the end of your 12 month AWS "
    "Free Tier term, and is available to both existing and new AWS customers indefinitely."
)

#: The verbatim block stating the allowance and its billing cadence.
STEP_FUNCTIONS_ALLOWANCE = (
    "The Step Functions free tier includes 4,000 free state transitions per month. All charges "
    "are metered daily and billed monthly."
)

#: The verbatim block that proves the service is metered AND that exceeding the
#: free tier is CHARGED. The trailing " _" is a live markup artefact and is
#: reproduced exactly, because whole-block equality does not forgive it.
STEP_FUNCTIONS_CHARGED_ABOVE_FREE_TIER = (
    "With AWS Step Functions, you pay for the number state transitions you use per month. You are "
    "charged per state transition above the free tier. See the State Transitions Pricing Table "
    "for details. _"
)


def _rows(*pairs: tuple[str, str]) -> dict[str, HtmlMatrixRow]:
    return {label: HtmlMatrixRow(field) for label, field in pairs}


AWS_FREE_TIER_PLAN = register_html_profile(
    HtmlExtractionProfile(
        name="aws_free_tier_plan",
        header_signature=("Benefits", "Free plan", "Paid plan"),
        mode="matrix",
        matrix_metric_header="Benefits",
        matrix_tier_header="Free plan",
        # All 6 live benefit rows, in live order. Completeness is the guard: a
        # benefit AWS adds later rejects the document (`unknown_matrix_rows`)
        # instead of disappearing from the published comparison.
        #
        # The tier column read is `Free plan`. The live table's `Paid plan`
        # column is deliberately NOT unpivoted -- this profile describes the free
        # offer, and the capture declares the omission explicitly.
        matrix_rows=_rows(
            ("Receive up to $200 USD free credits", "receive_up_to_200_usd_free_credits"),
            ("Free usage of select services", "free_usage_of_select_services"),
            ("Access to all AWS services", "access_to_all_aws_services"),
            ("Billed for the usage", "billed_for_the_usage"),
            ("Scale workloads beyond credit", "scale_workloads_beyond_credit"),
            ("Promotional Credits", "promotional_credits"),
        ),
        trusted_assertions=True,
        assertions=(
            # Identity is pinned to the page's own top-level heading rather than
            # to the document title, because the title ("Free Cloud Computing
            # Services - AWS Free Tier") is marketing copy that would not survive
            # a rewording of the strap line.
            HtmlTextAssertion(
                text="AWS Free Tier",
                field="service",
                value="AWS Free Tier",
                scope="heading",
            ),
            # The credit is what this offer IS, so the offer type is pinned to
            # the block that grants it. A rewritten credit sentence rejects the
            # document rather than leaving a stale `new_customer_credit` behind.
            HtmlTextAssertion(
                text=FREE_TIER_CREDIT_AND_WINDOW,
                field="offer_type",
                value="new_customer_credit",
            ),
            HtmlTextAssertion(
                text=FREE_TIER_CREDIT_AND_WINDOW,
                field="credit_amount",
                value="$200",
            ),
            HtmlTextAssertion(
                text=FREE_TIER_CREDIT_AND_WINDOW,
                field="free_plan_duration_months",
                value="6",
            ),
            # Carried whole so the account-closure clause travels with the offer.
            HtmlTextAssertion(
                text=FREE_TIER_CREDIT_AND_WINDOW,
                field="notes",
                value=FREE_TIER_CREDIT_AND_WINDOW,
            ),
            HtmlTextAssertion(
                text="Get up to $200 in credits for new customers",
                field="eligibility",
                value="Get up to $200 in credits for new customers",
                scope="heading",
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


#: The richest AWS evidence page, and the only one that states the payment-method
#: requirement. Assertion-only: the live document contains zero tables.
AWS_FREE_PLAN = register_html_profile(
    HtmlExtractionProfile(
        name="aws_free_plan",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text=(
                    "The free plan allows you to experiment with AWS services and build "
                    "proof-of-concepts at no cost for up to 6 months until you upgrade to a paid "
                    "plan."
                ),
                field="service",
                value="AWS Free Tier free plan",
            ),
            # The offer type is pinned to the expiry block, which is the block
            # that ties the plan to the CREDITS and to a time limit at once.
            HtmlTextAssertion(
                text=FAQ_PLAN_EXPIRY,
                field="offer_type",
                value="new_customer_credit",
            ),
            HtmlTextAssertion(
                text=FAQ_PLAN_EXPIRY,
                field="free_plan_duration_months",
                value="6",
            ),
            HtmlTextAssertion(
                text=FAQ_PLAN_CLOSURE,
                field="exhaustion_behaviour",
                value="manual_upgrade_required",
            ),
            HtmlTextAssertion(
                text=FAQ_PLAN_CLOSURE,
                field="notes",
                value=FAQ_PLAN_CLOSURE,
            ),
            # THE unfavourable fact, quoted rather than inferred. This block names
            # the free plan itself, so no composition is involved.
            HtmlTextAssertion(
                text=FAQ_PAYMENT_METHOD_REQUIRED,
                field="requires_card",
                value=True,
            ),
            HtmlTextAssertion(
                text=FAQ_PAYMENT_METHOD_PURPOSE,
                field="payment_method_purpose",
                value=FAQ_PAYMENT_METHOD_PURPOSE,
            ),
            HtmlTextAssertion(
                text=(
                    "You would be ineligible for free plan or Free Tier credits if you have an "
                    "existing AWS account or have had one in the past. The free plan and Free "
                    "Tier credits are available only to new AWS customers."
                ),
                field="eligibility",
                value=(
                    "You would be ineligible for free plan or Free Tier credits if you have an "
                    "existing AWS account or have had one in the past. The free plan and Free "
                    "Tier credits are available only to new AWS customers."
                ),
            ),
            HtmlTextAssertion(
                text=(
                    "Free Tier credits expire 12 months from the date you create your AWS "
                    "account. You can view the credit expiration date in the Credits page in the "
                    "AWS Billing and Cost Management Console."
                ),
                field="credit_expiry_months",
                value="12",
            ),
            # The always-free overage answer, carried WHOLE as its own note. It is
            # deliberately not `exhaustion_behaviour`: see the constant's comment.
            HtmlTextAssertion(
                text=FAQ_OVERAGE_BEGINS_CHARGES,
                field="overage_note",
                value=FAQ_OVERAGE_BEGINS_CHARGES,
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


#: The deliberate TIME-LIMITED control, taken from the authoritative Free Tier
#: Terms. Assertion-only: the live document contains zero tables.
AWS_12_MONTH_FREE_TIER = register_html_profile(
    HtmlExtractionProfile(
        name="aws_12_month_free_tier",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            # One block carries the offer's name, its 12-month nature and the fact
            # that AWS distinguishes it from trials and from undated offers.
            # Pinning identity and offer type to the same block means a reworded
            # definition cannot leave a stale `trial` behind.
            HtmlTextAssertion(
                text=TERMS_TWO_KINDS_OF_OFFER,
                field="service",
                value="AWS 12 Month Free Tier",
            ),
            HtmlTextAssertion(
                text=TERMS_TWO_KINDS_OF_OFFER,
                field="offer_type",
                value="trial",
            ),
            HtmlTextAssertion(
                text=TERMS_12_MONTH_AVAILABILITY,
                field="trial_length_months",
                value="12",
            ),
            HtmlTextAssertion(
                text=TERMS_12_MONTH_AVAILABILITY,
                field="eligibility",
                value=TERMS_12_MONTH_AVAILABILITY,
            ),
            HtmlTextAssertion(
                text=(
                    "New benefits added to the 12 Month Free Tier will be available to you for "
                    "the remainder of your one year term but will not extend it. If your one year "
                    "term has already expired, then you will not be entitled to any such new "
                    "benefits."
                ),
                field="notes",
                value=(
                    "New benefits added to the 12 Month Free Tier will be available to you for "
                    "the remainder of your one year term but will not extend it. If your one year "
                    "term has already expired, then you will not be entitled to any such new "
                    "benefits."
                ),
            ),
            # These two pin the offer to the LEGACY terms section, which is what
            # makes it impossible for this profile to be read as describing the
            # current program on the same page.
            HtmlTextAssertion(
                text="Applicable to AWS customers with 12-month free tier offers.",
                field="applicability",
                value="Applicable to AWS customers with 12-month free tier offers.",
            ),
            HtmlTextAssertion(
                text="Last Updated: August 14, 2018",
                field="terms_last_updated",
                value="August 14, 2018",
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


#: The one real SERVICE covered. Assertion-only: the live page has three tables
#: and NONE of them carries a header row, so no table on it is selectable and no
#: table was invented to make one.
AWS_DYNAMODB_FREE_TIER = register_html_profile(
    HtmlExtractionProfile(
        name="aws_dynamodb_free_tier",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text=("Amazon DynamoDB Pricing | NoSQL Key-Value Database | Amazon Web Services"),
                field="service",
                value="Amazon DynamoDB",
                scope="title",
            ),
            # Rule-2 leg 3: the allowance replenishes on a schedule.
            HtmlTextAssertion(
                text=DDB_FREE_TIER_IS_MONTHLY,
                field="offer_type",
                value="recurring_quota",
            ),
            HtmlTextAssertion(
                text=DDB_FREE_TIER_IS_MONTHLY,
                field="quota_basis",
                value=DDB_FREE_TIER_IS_MONTHLY,
            ),
            # Rule-2 leg 1: the containing service is itself metered.
            HtmlTextAssertion(
                text=DDB_SERVICE_IS_PRICED,
                field="billing_basis",
                value=DDB_SERVICE_IS_PRICED,
            ),
            # The five published allowances. AWS states them as five <li> items
            # under the intro block above, so they are pinned as five facts
            # rather than folded into a table that does not exist.
            HtmlTextAssertion(
                text="25 WCUs, 25 RCUs",
                field="provisioned_capacity",
                value="25 WCUs, 25 RCUs",
            ),
            HtmlTextAssertion(
                text="25 rWCUs for global tables, deployed across two AWS Regions",
                field="global_table_replicated_writes",
                value="25 rWCUs for global tables, deployed across two AWS Regions",
            ),
            HtmlTextAssertion(
                text="25 GB of data storage",
                field="data_storage",
                value="25 GB of data storage",
            ),
            HtmlTextAssertion(
                text="2.5 million stream read requests from DynamoDB Streams",
                field="stream_read_requests",
                value="2.5 million stream read requests from DynamoDB Streams",
            ),
            HtmlTextAssertion(
                text=(
                    "1 GB of data transfer out (15 GB for your first 12 months), aggregated "
                    "across AWS services"
                ),
                field="data_transfer_out",
                value=(
                    "1 GB of data transfer out (15 GB for your first 12 months), aggregated "
                    "across AWS services"
                ),
            ),
            HtmlTextAssertion(
                text=DDB_CREDIT_CROSS_REFERENCE,
                field="notes",
                value=DDB_CREDIT_CROSS_REFERENCE,
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


#: The one AWS SERVICE page in the sweep that states an allowance, a term bound
#: and an overage consequence in a single quotable block. Assertion-only: the
#: live document contains zero tables.
AWS_API_GATEWAY_FREE_TIER = register_html_profile(
    HtmlExtractionProfile(
        name="aws_api_gateway_free_tier",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="Amazon API Gateway Pricing",
                field="service",
                value="Amazon API Gateway",
                scope="title",
            ),
            # This offer is bounded to 12 months by its own sentence, so it is a
            # time-limited introductory offer and NOT an Always Free one. Pinning
            # the offer type to the same block as the term means a reworded term
            # cannot leave a stale value behind.
            HtmlTextAssertion(
                text=API_GATEWAY_FREE_TIER_AND_OVERAGE,
                field="offer_type",
                value="trial",
            ),
            HtmlTextAssertion(
                text=API_GATEWAY_FREE_TIER_AND_OVERAGE,
                field="trial_length_months",
                value="12",
            ),
            # THE decisive fact: "you will be charged the API Gateway usage
            # rates". Quoted, not inferred, and it makes this offer a definite
            # billing exposure.
            HtmlTextAssertion(
                text=API_GATEWAY_FREE_TIER_AND_OVERAGE,
                field="exhaustion_behaviour",
                value="automatic_billing",
            ),
            HtmlTextAssertion(
                text=API_GATEWAY_FREE_TIER_AND_OVERAGE,
                field="quota_basis",
                value=API_GATEWAY_FREE_TIER_AND_OVERAGE,
            ),
            HtmlTextAssertion(
                text=API_GATEWAY_TERM_AND_EXPIRY,
                field="eligibility",
                value=API_GATEWAY_TERM_AND_EXPIRY,
            ),
            HtmlTextAssertion(
                text=API_GATEWAY_TERM_AND_EXPIRY,
                field="notes",
                value=API_GATEWAY_TERM_AND_EXPIRY,
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


#: The ONE genuinely perpetual AWS offer in this slice, and the proof that a
#: perpetual offer still need not be Z0: the same page that calls the tier
#: indefinite also says usage above it is charged. Assertion-only.
AWS_STEP_FUNCTIONS_FREE_TIER = register_html_profile(
    HtmlExtractionProfile(
        name="aws_step_functions_free_tier",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text="AWS Step Functions Pricing",
                field="service",
                value="AWS Step Functions",
                scope="title",
            ),
            # Rule 1 of docs/DATA_MODEL.md: the provider identifies an
            # indefinitely available free tier and the allowance belongs to it.
            # "does not automatically expire ... indefinitely" is the quotation
            # that carries it, so `always_free` is not inferred from the word
            # "free".
            HtmlTextAssertion(
                text=STEP_FUNCTIONS_INDEFINITE,
                field="offer_type",
                value="always_free",
            ),
            HtmlTextAssertion(
                text=STEP_FUNCTIONS_INDEFINITE,
                field="availability",
                value=STEP_FUNCTIONS_INDEFINITE,
            ),
            HtmlTextAssertion(
                text=STEP_FUNCTIONS_ALLOWANCE,
                field="free_state_transitions_per_month",
                value="4,000",
            ),
            HtmlTextAssertion(
                text=STEP_FUNCTIONS_ALLOWANCE,
                field="quota_basis",
                value=STEP_FUNCTIONS_ALLOWANCE,
            ),
            # THE decisive fact. A perpetual allowance whose overage is billed is
            # still a billing exposure, so this offer is Z1 and NOT Z0. Deleting
            # this block must reject the document rather than leave a perpetual
            # offer looking unconditionally free.
            HtmlTextAssertion(
                text=STEP_FUNCTIONS_CHARGED_ABOVE_FREE_TIER,
                field="exhaustion_behaviour",
                value="automatic_billing",
            ),
            HtmlTextAssertion(
                text=STEP_FUNCTIONS_CHARGED_ABOVE_FREE_TIER,
                field="billing_basis",
                value=STEP_FUNCTIONS_CHARGED_ABOVE_FREE_TIER,
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


__all__: Sequence[str] = (
    "FAQ_PAYMENT_METHOD_REQUIRED",
    "FAQ_PAYMENT_METHOD_PURPOSE",
    "FAQ_OVERAGE_BEGINS_CHARGES",
    "FAQ_PLAN_CLOSURE",
    "FAQ_PLAN_EXPIRY",
    "TERMS_TWO_KINDS_OF_OFFER",
    "TERMS_12_MONTH_AVAILABILITY",
    "DDB_FREE_TIER_IS_MONTHLY",
    "DDB_SERVICE_IS_PRICED",
    "DDB_CREDIT_CROSS_REFERENCE",
    "API_GATEWAY_FREE_TIER_AND_OVERAGE",
    "API_GATEWAY_TERM_AND_EXPIRY",
    "STEP_FUNCTIONS_INDEFINITE",
    "STEP_FUNCTIONS_ALLOWANCE",
    "STEP_FUNCTIONS_CHARGED_ABOVE_FREE_TIER",
    "FREE_TIER_CREDIT_AND_WINDOW",
    "AWS_FREE_TIER_PLAN",
    "AWS_FREE_PLAN",
    "AWS_12_MONTH_FREE_TIER",
    "AWS_DYNAMODB_FREE_TIER",
    "AWS_API_GATEWAY_FREE_TIER",
    "AWS_STEP_FUNCTIONS_FREE_TIER",
)
