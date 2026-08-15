"""Oracle Cloud OFFICIAL free-tier extraction profiles (F008 P6).

Provider-specific selectors expressed purely as **data**, registered through the
F008 S3 seam so adding Oracle touches no shared module. Oracle is the last of the
six F008 providers.

Every profile here was derived from the live ``oracle.com`` and
``docs.oracle.com`` pages on 2026-08-14, and **no quotation below was typed by
hand**. Each block was resolved against the live document's own parse by a short
unique needle, and the committed fixture was generated from the resolved
literal, by a generator that refuses to write when a needle matches zero or more
than one live block, when a resolved block occurs more than once live, or when
the parsed target-table rows differ from live. Transcription is where a
*composed* quotation creeps in -- a word normalised, whitespace collapsed, two
sentences merged -- and generating from the resolved literal removes the
opportunity instead of guarding against it.

**Oracle is the provider most likely to reach Z0, and it does not.** That is the
finding, and it rests on quoted text rather than on an absence of text.

**What is genuinely true, and evidenced.** Oracle's Always Free tier really is
perpetual, in Oracle's own words and in blocks that describe Always Free alone::

    All Oracle Cloud Infrastructure accounts (whether free or paid) have a set
    of resources that are free of charge in the home region of the tenancy, for
    the life of the account.

    Always Free services are available for an unlimited time.

Both satisfy rule 1 of ``docs/DATA_MODEL.md`` by quotation, so ``always_free``
here is not inferred from the word "free".

**Perpetuity does not ENTAIL zero cost -- and it does not preclude it either.**
MEASURED by driving the real classifier over every provider fixture committed to
this repository. Scope, stated because the two numbers below are NOT
interchangeable: 7 provider configurations exist here -- the six F008 providers
plus **Cloudflare, which is F005 and not an F008 provider**. Of **14** perpetual
(``always_free``) offers across all seven: **5 classify ``Z0_TRUE_FREE``**, **5
classify ``Z1_BILLING_EXPOSURE``**, and **4 remain ``UNKNOWN``**. Those five Z0
offers come from **two** providers -- GitHub Actions, Packages and Codespaces,
and Cloudflare Pages and Workers. **Restricted to the six F008 providers the Z0
count is 3, all from GitHub alone.**

So "perpetual" establishes nothing on its own: it is neither evidence for Z0 nor
evidence against it, and every material condition has to be evidenced separately,
per offer.

**What is distinctive about Oracle, stated narrowly enough to be checkable.**
Oracle is *not* the only provider whose perpetual offer is withheld -- AWS Step
Functions, Azure Cosmos DB and Google Cloud's free tier are all perpetual and all
``Z1``, and four further perpetual offers sit at ``UNKNOWN``. What is unique is
the REASON. Measured across all 14: AWS, Azure and GCP are each blocked by
``automatic_billing`` on exhaustion, whereas **Oracle is the only provider in
this repository whose perpetual offer is withheld by a quoted payment-card
requirement.**

**An earlier revision of this docstring stated a general law instead**, to the
effect that perpetual offers are never free, on the strength of three providers.
It was FALSE: this repository had already measured perpetual offers reaching
``Z0_TRUE_FREE`` five slices earlier, and
``tests/integration/test_ingest_github.py`` asserts exactly that on real ingested
rows. It was wrong in the **omission-favouring** direction -- a blanket
"perpetual is never free" under-reports genuinely free offers, and
under-reporting is a defect here exactly as much as over-claiming. It was ALSO
wrong in the opposite direction at the same time, by under-counting its own
support: GCP is a fourth provider whose perpetual offer is non-Z0, already in the
tree and unmentioned. It is recorded rather than quietly replaced, because the
claim shipped. ``tests/unit/test_adapter_oracle.py`` now pins the corrected
statement in both directions and forbids the refuted one from returning.

**What blocks Z0, and by what SHAPE.** The distinction matters, because a gate
that fails on a quoted sentence cannot be flipped by anything that later supplies
a missing field, while a gate that fails on absence can:

* ``oracle_always_free_services`` (the flagship), ``oracle_free_tier``,
  ``oracle_cloud_free_tier`` and ``oracle_mysql_heatwave_always_free`` are
  ``Z1_BILLING_EXPOSURE`` because ``requires_card`` is **True by QUOTATION**, on
  each offer's own document. No fact is carried across documents to reach it.
* ``oracle_always_free_resources`` is ``UNKNOWN`` because ``requires_card`` is
  **absent by ABSENCE**: the OCI Always Free Resources document states no payment
  condition at all, and carrying the requirement over from another Oracle page
  would be cross-document composition. This is disclosed rather than papered
  over, and it is the structurally weaker of the two shapes.
* ``oracle_free_credit_promotion`` is ``UNKNOWN`` for the same reason.

**The two card blocks, quoted.** From ``docs.oracle.com`` ``.../FreeTier/freetier.htm``::

    For security purposes, most users need a mobile phone number and a credit
    card to create an account. Your credit card will not be charged unless you
    upgrade your account.

From ``www.oracle.com/cloud/free/faq/`` (and, on their own documents, from
``www.oracle.com/cloud/free/`` and ``www.oracle.com/mysql/free/``)::

    To provide free Oracle Cloud accounts to our valued customers, we need to
    ensure that you are who you say you are. We use your contact information and
    credit/debit card information for account setup and identity verification.

**The reading, stated so a reviewer can check it rather than trust it.** Neither
block contains the word "required", and the first hedges with "most users". They
are read as ``requires_card = True`` because each states that supplying a card is
part of creating a *free* Oracle Cloud account, and the second goes on to say
Oracle periodically checks the validity of "your card" -- which presupposes one.
No Oracle document probed in this slice states that a card is *not* required, and
``requires_card = False`` would be flatly contradicted by both. The alternative,
leaving the field absent, would withhold Z0 too, via gate 4 rather than gate 3 --
so the choice changes no Z0 verdict, only whether the refusal is reported as a
definite billing exposure or as an unknown. The quoted reading is the stronger
and is used. A fifth document, ``.../GSG/Tasks/signingup_topic-Sign_Up_for_Free_
Oracle_Cloud_Promotion.htm``, states it as an imperative procedure step ("Select
Add payment verification method, and then select Credit Card."); it is not a
source here because it is a procedure page that states no offer type in a block
of its own, and pinning one to a button label would be inference.

**A trap that was measured and avoided.** The block "You will only be charged for
services that you use that exceeds Always Free." looks like an automatic-billing
statement for the Always Free offer. It is not. Its FAQ question -- carried in a
``<div class="cb105w3">`` that the collector does not capture -- is "How do I know
how much I am going be charged for Pay As You Go services?". The block's own
context is a PAID account, so pinning it as an Always Free exhaustion behaviour
would be composition. It is pinned as an exhaustion behaviour nowhere in this
module. The same applies to ``CFT_SWITCH_TO_PAYG``, which is carried whole as a
note precisely so the boundary stays visible.

**Structure, measured with the repository's own parser.** Exactly ONE probed
Oracle page publishes a free-tier fact in a table the engine can select: the OCI
Always Free Resources document, whose single ``<table>`` has the live header row
``Resource`` / ``Limit Name`` / ``Always Free`` and 6 body rows of Resource
Manager limits. Every other source here is ``mode="assertions"``: it declares no
table selector, reads no table, and its captured document contains no ``<table>``
at all, because the live page has none. Nothing was synthesized. For contrast,
``www.oracle.com/cloud/price-list/`` served 120 tables of which exactly one is
header-selectable, and that one is a PAID comparison table; and
``www.oracle.com/database/nosql/pricing/`` served 23 tables, one header-selectable
and also paid, with no free-tier prose in its served HTML at all.

**Blocks Oracle publishes twice, and why none of them is pinned.** On the OCI
Always Free Resources document the string "50,000 Object Storage API requests per
month" appears in both the Always-Free-only list and the paid/trial list, and the
Resource Manager table's numeric cells repeat. Whole-block equality requires
exactly one match, so pinning either would yield ``ambiguous_assertion`` against
the live page. The generator refuses to pin a block that occurs more than once
live, which is why no such block appears below.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..html import HtmlExtractionProfile, HtmlMatrixRow, HtmlTextAssertion
from . import register_html_profile

#: The document's own title, and the identity this profile extracts.
AFR_TITLE = "Always Free Resources"

#: THE block that makes ``always_free`` a quotation rather than an inference: Oracle
#: says the resources are 'free of charge ... for the life of the account'. This is rule
#: 1 of ``docs/DATA_MODEL.md`` satisfied by official text, and it describes the Always
#: Free resources ALONE -- no trial, no credit, no paid plan shares this block.
AFR_FREE_FOR_LIFE_OF_ACCOUNT = (
    "All Oracle Cloud Infrastructure accounts (whether free or paid) have a set of resources "
    "that are free of charge in the home region of the tenancy, for the life of the account. "
    "These resources display the Always Free-eligible label in the Console (for OCI Ampere A1 "
    "Compute shapes, see Compute)."
)

#: The ONLY block on this document that states the consequence of exhausting an Always
#: Free quota in a block of its own: 'the operation will fail with an error'. That is
#: ``request_rejected``, a SAFE stop -- and it is deliberately NOT generalised into a
#: claim about every Always Free quota. It is one quota's stated behaviour, quoted.
AFR_BACKUP_LIMIT_FAILS = (
    "You can have a maximum of five Always Free volume backups at any time. This applies to "
    "both boot volume and block volume backups. For example, you could have three boot volume "
    "backups for your Always Free instance and two block volume backups for your Always Free "
    "block volumes. In this example, if you try to create new backups, the operation will fail "
    "with an error until you delete existing Always Free volume backups. For more information "
    "about volume backups, see Overview of Block Volume Backups and Overview of Boot Volume "
    "Backups."
)

#: Rule-1 allowance: the AMD micro instances (category compute-vms).
AFR_COMPUTE_AMD = (
    "Micro instances (AMD processor): All tenancies get up to two Always Free VM instances "
    "using the VM.Standard.E2.1.Micro shape, which has an AMD processor."
)

#: Rule-1 allowance: the Arm Ampere A1 monthly OCPU/GB hours.
AFR_COMPUTE_ARM = (
    "OCI Ampere A1 Compute instances (Arm processor): All tenancies get the first 1,500 OCPU "
    "hours and 9,000 GB hours per month for free for VM instances using the "
    "VM.Standard.A1.Flex shape, which has an Arm processor. For Always Free tenancies, this is "
    "equivalent to 2 OCPUs and 12 GB of memory."
)

#: Carried whole because it is an UNFAVOURABLE condition on the compute allowance: an
#: idle Always Free instance may be reclaimed. Dropping it would flatter the offer.
AFR_IDLE_RECLAIM = (
    "Idle Always Free compute instances may be reclaimed by Oracle. Oracle will deem virtual "
    "machine and bare metal compute instances as idle if, during a 7-day period, the following "
    "are true:"
)

#: Rule-1 allowance: 200 GB of Block Volume storage plus five backups.
AFR_BLOCK_VOLUME = (
    "All tenancies receive a total of 200 GB of Block Volume storage, and five volume backups "
    "included in the Always Free resources. These amounts apply to both boot volumes and block "
    "volumes combined. When you provision a compute instance, the instance automatically "
    "receives a 50 GB boot volume for storage. You can also create and attach block volumes to "
    "expand the storage capacity of a compute instance. For more information, see Creating a "
    "Block Volume and Attaching a Block Volume to an Instance."
)

#: The UNFAVOURABLE billing sentence on this page, carried whole. It is deliberately NOT
#: converted into ``exhaustion_behaviour``: it states a cost for a volume created
#: OUTSIDE the home region, which is a placement rule and not the consequence of
#: exhausting the Always Free allowance. Converting it would be inference; publishing it
#: is honesty.
AFR_REGION_COST = (
    "To create an Always Free block volume, the volume must be created in the home region of "
    "the tenancy. Volumes created outside of the home region incur regular block volume costs."
)

#: Rule-1 allowance for object and archive storage in the Always-Free-only state. The
#: neighbouring '50,000 Object Storage API requests per month' block is published TWICE
#: on this page, so it is not pinned -- see the module docstring.
AFR_OBJECT_STORAGE = (
    "20 GB of combined Standard tier, Infrequent Access tier, and Archive tier data"
)

#: Rule-1 allowance: HSM key versions and 150 Always Free Vault secrets.
AFR_VAULT = (
    "All master encryption keys protected by software are free. All tenancies get 20 key "
    "versions of master encryption keys protected by a hardware security module (HSM) and 150 "
    "Always Free Vault secrets. You can spread these keys or secrets across any number of "
    "vaults in the tenancy, although virtual private vaults are not included in the Always "
    "Free resources."
)

#: The service whose limits are published in the one table this document exposes to the
#: engine, so the matrix rows below have a quoted context rather than floating free.
AFR_RESOURCE_MANAGER = (
    "All tenancies get a set of Always Free resources in the Resource Manager service that "
    "allow you to automate the process of provisioning infrastructure using Terraform. See "
    "Quickly Launch Your Always Free Resources Using Resource Manager for instructions on "
    "using Resource Manager to create your Always Free resources."
)

#: Rule-1 allowance: two Always Free Oracle Autonomous AI Databases.
AFR_AUTONOMOUS_DB = (
    "Oracle Autonomous AI Database: All tenancies get two Always Free Oracle Autonomous AI "
    "Databases. You can use these databases for transaction processing, data warehousing, "
    "Oracle APEX application development, or JSON-based application development. For current "
    "regional availability, see the Always Free Cloud Services table in Cloud "
    "Regions—Infrastructure and Platform Services."
)

#: Rule-1 allowance: the Oracle NoSQL Database read/write/storage allowance.
AFR_NOSQL = (
    "Oracle NoSQL Database: All tenancies get an Oracle NoSQL Database with up to 133 million "
    "reads per month, 133 million writes per month, and 3 tables with 25 GB storage per table. "
    "Learn more about Oracle NoSQL Database."
)

#: Rule-1 allowance: the Always Free MySQL HeatWave DB system.
AFR_MYSQL_HEATWAVE = (
    "Oracle MySQL HeatWave: All tenancies get a standalone MySQL HeatWave DB system with a "
    "single node HeatWave cluster in the home region. The Always Free DB system has 50 GB of "
    "storage to store data and log files. An extra 50 GB of backup storage is available. Learn "
    "more about Oracle MySQL HeatWave."
)

#: Rule-1 allowance: one Always Free Flexible Load Balancer at 10 Mbps.
AFR_LOAD_BALANCER = (
    "All Oracle Cloud Infrastructure tenancies created December 15, 2020 or later get one "
    "Always Free Flexible Load Balancer with a minimum and maximum bandwidth set to 10 Mbps."
)

#: Rule-1 allowance: one Network Load Balancer.
AFR_NETWORK_LOAD_BALANCER = (
    "As part of your Always Free resources, you get one Network Load Balancer."
)

#: Rule-1 allowance scoped explicitly to Free Tier tenancies, in Oracle's own words:
#: 'tenancies that are not paid and do not have Free Trial credits'.
AFR_VCN = (
    "Free Tier tenancies (tenancies that are not paid and do not have Free Trial credits) can "
    "have up to 2 virtual cloud networks (VCNs). A VCN is a software-defined network that you "
    "set up in the Oracle Cloud Infrastructure data centers in a particular region. VCNs "
    "include IPv4 and IPv6 support."
)

#: Rule-1 allowance: five certificate authorities and 150 certificates.
AFR_CERTIFICATES = (
    "All tenancies get five certificate authorities (CAs) and 150 certificates included in the "
    "Always Free resources."
)

#: Rule-1 allowance: APM tracing events and synthetic monitor runs.
AFR_APM = (
    "All tenancies get 1000 Application Performance Monitoring tracing events and 10 Synthetic "
    "Monitor runs per hour included in the Always Free resources. Learn more."
)

#: Rule-1 allowance: Monitoring ingestion and retrieval data points.
AFR_MONITORING = (
    "All tenancies get 500 million Monitoring service ingestion data points, and 1 billion "
    "retrieval data points included in the Always Free resources."
)

#: Rule-1 allowance: https and email notifications per month.
AFR_NOTIFICATIONS = (
    "As part of your Always Free resources, you can send 1 million https notifications per "
    "month, and 1000 email notifications per month. Learn more about OCI's Notifications "
    "service."
)

#: Rule-1 allowance: 3000 emails per month.
AFR_EMAIL_DELIVERY = (
    "As part of your Always Free resources, you can send 3000 emails for free per month. Learn "
    "more about OCI's Email Delivery service."
)

#: Rule-1 allowance: 10 TB per month of outbound data transfer.
AFR_OUTBOUND_DATA = (
    "As part of your Always Free resources, you get 10 TB per month of outbound data."
)

#: Rule-1 allowance: Bastion, stated free for free AND paid accounts.
AFR_BASTION = (
    "OCI's Bastion service provides restricted and time-limited Secure Shell Protocol (SSH) "
    "access to target resources that don't have public endpoints. Bastion is free for both "
    "free and paid accounts. See Bastion for more information."
)


#: Identity and offer type pinned to the SAME block, so a reworded credit sentence
#: rejects the document rather than leaving a stale ``new_customer_credit`` behind.
FT_TRIAL_CREDITS = (
    "The Free Trial provides you with $300 of cloud credits that are valid for up to 30 days. "
    "You can spend these credits on any eligible Oracle Cloud Infrastructure service."
)

#: THE decisive unfavourable block on this document, and it is a QUOTATION, not an
#: absence: Oracle states that a credit card is needed to create an account. The hedge
#: 'most users' is reproduced verbatim and is discussed in the module docstring; no
#: reading of this block supports ``requires_card = False``.
FT_CARD_REQUIRED = (
    "For security purposes, most users need a mobile phone number and a credit card to create "
    "an account. Your credit card will not be charged unless you upgrade your account."
)

#: The stated consequence when the trial's credits are gone: the paid resources are
#: reclaimed. ``resource_reclaimed`` is the literal predicate of this sentence.
FT_TRIAL_END_RECLAIM = (
    "Paid resources that were provisioned with your credits during your free trial are "
    "reclaimed by Oracle unless you upgrade your account."
)

#: A CROSS-REFERENCE carried whole. It names the trial and Always Free in one breath, so
#: it must never be used to pin an Always Free identity onto this trial offer -- that
#: would splice one offer's identity onto another's context. It is published so the
#: boundary between the two is visible rather than lost.
FT_ALWAYS_FREE_NEVER_EXPIRE = (
    "Oracle Cloud Infrastructure's Free Tier includes a free time-limited promotional trial "
    "that allows you to explore a wide range of Oracle Cloud Infrastructure products, and a "
    "set of Always Free offers that never expire."
)

#: What survives the trial, in Oracle's own words.
FT_TRIAL_END_NO_INTERRUPTION = (
    "After your trial ends, your account remains active. There is no interruption to the "
    "availability of the Always Free Resources you have provisioned. You can delete and "
    "provision Always Free resources as needed."
)

#: An eligibility limit, quoted rather than summarised.
FT_NOT_IN_GOV_REGIONS = (
    "The Free Tier and Always Free resources are not available in US Government Cloud regions."
)


#: THE perpetuity block for Oracle's flagship offer, describing Always Free services
#: ALONE: 'Always Free services are available for an unlimited time.' Identity, offer
#: type and availability are all pinned here, so a reworded definition rejects the
#: document instead of leaving a stale ``always_free`` behind.
AFS_UNLIMITED_TIME = (
    "Always Free services are part of Oracle Cloud Free Tier. Always Free services are "
    "available for an unlimited time. Some limitations apply. As new Always Free services "
    "become available, you will automatically be able to use those as well."
)

#: THE block that stops this perpetual offer from reaching Z0, and it is a QUOTATION on
#: the SAME document as the perpetuity block above -- no cross-document composition is
#: involved. It states that Oracle uses credit/debit card information for account setup
#: of FREE Oracle Cloud accounts, and that Oracle periodically checks the validity of
#: 'your card'. See the module docstring for why that reads to ``requires_card = True``.
AFS_CARD_IDENTITY = (
    "To provide free Oracle Cloud accounts to our valued customers, we need to ensure that you "
    "are who you say you are. We use your contact information and credit/debit card "
    "information for account setup and identity verification. Oracle may periodically check "
    "the validity of your card, resulting in a temporary “authorization” hold. These holds are "
    "removed by your bank, typically within three to five days, and do not result in actual "
    "charges to your account."
)

#: Carried whole because it narrows which instruments are accepted, which makes the
#: requirement stricter rather than softer.
AFS_CARD_TYPES = (
    "We accept credit cards and debit cards that function like credit cards. We do not accept "
    "debit cards with a PIN or virtual, single-use, or prepaid cards."
)

#: The post-trial guarantee for Always Free resources, quoted.
AFS_NOT_RECLAIMED = (
    "Resources identified as Always Free will not be reclaimed. After your Free Trial expires, "
    "you'll continue to be able to use and manage your existing Always Free resources and can "
    "create new Always Free resources according to tenancy limits."
)

#: The one-account-per-person eligibility rule, quoted.
AFS_ONE_ACCOUNT = (
    "One Oracle Cloud Free Trial or Always Free account is permitted per person. Please note:"
)

#: An UNFAVOURABLE block carried whole: no SLA, and an Always-Free-only customer is not
#: eligible for Oracle Support. A near-identical sentence appears in a second answer on
#: this page, so the needle used to resolve THIS block is its unique first sentence.
AFS_NO_SLA_NO_SUPPORT = (
    "Oracle Cloud Free Tier does not include SLAs. Community support through our forums is "
    "available to all customers. Customers using only Always Free resources are not eligible "
    "for Oracle Support. Limited support is available for Oracle Cloud Free Tier with Free "
    "Trial credits. After you use all of your credits or after your trial period ends "
    "(whichever comes first), you must upgrade to a paid account to access Oracle Support. If "
    "you choose not to upgrade and continue to use Always Free Services, you will not be "
    "eligible to raise a service request in My Oracle Support."
)

#: Carried whole as a note and deliberately NOT converted into ``exhaustion_behaviour``:
#: it describes a TRANSITION case -- more Ampere instances provisioned than an Always
#: Free tenancy allows, at trial end -- not the steady-state consequence of exhausting
#: an Always Free quota. Converting it would be inference.
AFS_ARM_OVER_LIMIT = (
    "However, if you have more Ampere A1 Compute instances provisioned than are available for "
    "an Always Free tenancy, all existing Ampere A1 instances are disabled and then deleted "
    "after 30 days unless you upgrade to a paid account. To continue using your existing "
    "Arm-based instances as an Always Free user, before your trial ends, ensure that your "
    "total use of OCPUs and memory across all the Ampere A1 Compute instances in your tenancy "
    "is within the Always Free limit."
)


#: Identity, offer type, credit amount and term pinned to one block whose SUBJECT is the
#: credit. The trailing clause names Always Free by contrast; it is not used to claim
#: anything about the Always Free offer.
CFT_CREDIT_30_DAYS = (
    "Start with a US$300 cloud credit.*You’ll have 30 days to use it to test your applications "
    "on all OCI services, in addition to Always Free Services in your Free Tier account."
)

#: Carried whole as a note. Its second sentence -- 'Pay only for services that exceed
#: the monthly free amounts' -- is about a PAY AS YOU GO account, so it is NOT converted
#: into an exhaustion behaviour for this free offer.
CFT_SWITCH_TO_PAYG = (
    "At any time during or after the 30-day period,* switch to a Pay As You Go account. Pay "
    "only for services that exceed the monthly free amounts from Always Free Services."
)

#: What happens if the customer does nothing after 30 days, quoted.
CFT_DO_NOTHING = (
    "If you do nothing after 30 days, you’ll continue to get Always Free Services in your Free "
    "Tier account."
)

#: The same payment-verification block Oracle publishes on the FAQ, here on its own
#: document. Pinning it separately per document is what keeps every card claim a
#: single-document quotation.
CFT_CARD_IDENTITY = (
    "To provide free Oracle Cloud accounts to our valued customers, we need to ensure that you "
    "are who you say you are. We use your contact information and credit/debit card "
    "information for account setup and identity verification. Oracle may periodically check "
    "the validity of your card, resulting in a temporary “authorization” hold. These holds are "
    "removed by your bank, typically within three to five days, and do not result in actual "
    "charges to your account."
)

#: The one-account-per-person eligibility rule, quoted.
CFT_ONE_ACCOUNT = (
    "One Oracle Cloud Free Trial or Always Free account is permitted per person. Please note:"
)

#: An UNFAVOURABLE condition carried whole: an idle account may be suspended or
#: terminated.
CFT_IDLE_ACCOUNTS = (
    "Accounts left idle for 30 days or more may be deemed abandoned and become eligible for "
    "suspension or termination."
)


#: Identity, offer type and exhaustion behaviour pinned to one block that describes the
#: promotion's end: warnings, a 30-day grace period, and reclamation of paid resources.
FCP_GRACE_AND_RECLAIM = (
    "In both cases, Oracle Cloud sends you warning messages that you are nearing the end of "
    "your promotion period or getting close to your free credit limit. Another email will let "
    "you know when the promotion actually expires. You will have a grace period of 30 days. "
    "You can continue to use paid resources during the grace period. However, you can't create "
    "new paid resources during the grace period unless you upgrade your account. If you don't "
    "upgrade your account during this period, then your paid resources will be reclaimed. Your "
    "Always Free resources will continue to be available."
)

#: One of the two stated expiry conditions.
FCP_EXPIRES_30_DAYS = "Thirty (30) days from the day you signed up."

#: The other stated expiry condition.
FCP_EXPIRES_CREDITS_USED = "When you use up the free credits available in your promotion offer."

#: A CROSS-REFERENCE carried whole. It describes the Always Free resources, NOT this
#: promotion, so it is not used to pin this offer's type or availability. It is
#: published because it carries the 60-day idle condition and the statement that a paid
#: account is not billed for Always Free resources.
FCP_ALWAYS_FREE_LIFE_OF_ACCOUNT = (
    "All Oracle Cloud Infrastructure accounts (whether free or paid) have a set of resources "
    "that are available free of charge for the life of the account. These resources are called "
    "Always Free resources. If you have subscribed to a free credit promotion, your account "
    "continues to be available to you after the trial period ends (or after you use all of "
    "your credits). You can continue to use the Always Free resources in your account for as "
    "long as your account remains active. Free accounts remain active and available to you as "
    "long as the account has been used within the past 60 days. If you have a paid account, "
    "you will not be billed for any Always Free resources you are using. See Oracle Cloud "
    "Infrastructure Free Tier for more information."
)

#: The document's own statement of what it is about.
FCP_UPGRADE_SCOPE = (
    "If you don't upgrade your free credit promotion to a paid subscription, then it's "
    "important to understand what happens to your cloud account."
)


#: Identity: the Always Free MySQL HeatWave system, quoted.
MHW_SYSTEM = (
    "A standalone Oracle MySQL HeatWave database system with a single-node MySQL HeatWave "
    "cluster in your home region"
)

#: The perpetuity block, published by Oracle on this document as well as on the FAQ. It
#: is pinned here from THIS document's own parse, never carried across from the FAQ.
MHW_UNLIMITED_TIME = (
    "Always Free services are part of Oracle Cloud Free Tier. Always Free services are "
    "available for an unlimited time. Some limitations apply. As new Always Free services "
    "become available, you will automatically be able to use those as well."
)

#: The lead-in that binds the allowance list below to 'unlimited time'.
MHW_UNLIMITED_LEADIN = "You can use the following for an unlimited time:"

#: Rule-1 allowance: 50 GB of storage.
MHW_STORAGE = "50 GB of storage"

#: Rule-1 allowance: 50 GB of backup storage.
MHW_BACKUP_STORAGE = "50 GB of backup storage"

#: What the Always Free HeatWave system includes, quoted.
MHW_CAPABILITIES = (
    "You get access to MySQL HeatWave, Oracle MySQL HeatWave Lakehouse, Oracle MySQL HeatWave "
    "AutoML, HeatWave Vector Store, and HeatWave Autopilot to build and run small-scale "
    "applications."
)

#: The payment-verification block again, on this document. It is what makes this
#: perpetual service offer ``Z1_BILLING_EXPOSURE`` rather than merely UNKNOWN.
MHW_CARD_IDENTITY = (
    "To provide free Oracle Cloud accounts to our valued customers, we need to ensure that you "
    "are who you say you are. We use your contact information and credit/debit card "
    "information for account setup and identity verification. Oracle may periodically check "
    "the validity of your card, resulting in a temporary “authorization” hold. These holds are "
    "removed by your bank, typically within three to five days, and do not result in actual "
    "charges to your account."
)


def _rows(*pairs: tuple[str, str]) -> dict[str, HtmlMatrixRow]:
    return {label: HtmlMatrixRow(field) for label, field in pairs}


#: The ONE Oracle document in this sweep with a header-selectable table, and the
#: authoritative technical statement of what Always Free contains. Matrix over
#: the live Resource Manager limits table, plus the allowance prose pinned whole.
#:
#: ``requires_card`` is deliberately ABSENT: this document states no payment
#: condition, and importing the requirement from another Oracle page would be
#: cross-document composition. The classifier therefore returns ``UNKNOWN`` here
#: -- a refusal that rests on absence, which is disclosed in the docstring above.
ORACLE_ALWAYS_FREE_RESOURCES = register_html_profile(
    HtmlExtractionProfile(
        name="oracle_always_free_resources",
        header_signature=("Resource", "Limit Name", "Always Free"),
        mode="matrix",
        matrix_metric_header="Resource",
        matrix_tier_header="Always Free",
        # All 6 live body rows, in live order. Completeness is the guard: a limit
        # Oracle adds later rejects the document (`unknown_matrix_rows`) instead
        # of disappearing from the published set. The `Limit Name` column is
        # deliberately NOT unpivoted -- it carries API limit identifiers rather
        # than allowances -- and the capture declares that omission explicitly.
        matrix_rows=_rows(
            ("Configuration source providers", "configuration_source_providers"),
            ("Jobs (concurrent) Job duration: 24 hours", "concurrent_jobs"),
            ("Private endpoints", "private_endpoints"),
            ("Private endpoint reachable IP addresses", "private_endpoint_reachable_ips"),
            ("Private templates", "private_templates"),
            (
                "Stacks Variables per stack: 250 Size per variable: 8192 bytes "
                "Zip file per stack: 11 MB",
                "stacks",
            ),
        ),
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text=AFR_TITLE,
                field="service",
                value="Oracle Cloud Infrastructure Always Free",
                scope="title",
            ),
            # Rule 1 of docs/DATA_MODEL.md, by quotation: an indefinitely
            # available zero-priced tier that the allowances belong to.
            HtmlTextAssertion(
                text=AFR_FREE_FOR_LIFE_OF_ACCOUNT, field="offer_type", value="always_free"
            ),
            HtmlTextAssertion(
                text=AFR_FREE_FOR_LIFE_OF_ACCOUNT,
                field="availability",
                value=AFR_FREE_FOR_LIFE_OF_ACCOUNT,
            ),
            # A SAFE stop, quoted: "the operation will fail with an error".
            HtmlTextAssertion(
                text=AFR_BACKUP_LIMIT_FAILS, field="exhaustion_behaviour", value="request_rejected"
            ),
            HtmlTextAssertion(
                text=AFR_BACKUP_LIMIT_FAILS, field="quota_basis", value=AFR_BACKUP_LIMIT_FAILS
            ),
            HtmlTextAssertion(
                text=AFR_COMPUTE_AMD, field="compute_amd_instances", value=AFR_COMPUTE_AMD
            ),
            HtmlTextAssertion(
                text=AFR_COMPUTE_ARM, field="compute_arm_monthly", value=AFR_COMPUTE_ARM
            ),
            HtmlTextAssertion(
                text=AFR_IDLE_RECLAIM, field="idle_reclamation_note", value=AFR_IDLE_RECLAIM
            ),
            HtmlTextAssertion(
                text=AFR_BLOCK_VOLUME, field="block_volume_storage", value=AFR_BLOCK_VOLUME
            ),
            HtmlTextAssertion(
                text=AFR_REGION_COST, field="regional_billing_note", value=AFR_REGION_COST
            ),
            HtmlTextAssertion(
                text=AFR_OBJECT_STORAGE, field="object_storage_data", value=AFR_OBJECT_STORAGE
            ),
            HtmlTextAssertion(text=AFR_VAULT, field="vault_keys_and_secrets", value=AFR_VAULT),
            HtmlTextAssertion(
                text=AFR_RESOURCE_MANAGER, field="resource_manager", value=AFR_RESOURCE_MANAGER
            ),
            HtmlTextAssertion(
                text=AFR_AUTONOMOUS_DB, field="autonomous_database", value=AFR_AUTONOMOUS_DB
            ),
            HtmlTextAssertion(text=AFR_NOSQL, field="nosql_database", value=AFR_NOSQL),
            HtmlTextAssertion(
                text=AFR_MYSQL_HEATWAVE, field="mysql_heatwave", value=AFR_MYSQL_HEATWAVE
            ),
            HtmlTextAssertion(
                text=AFR_LOAD_BALANCER, field="flexible_load_balancer", value=AFR_LOAD_BALANCER
            ),
            HtmlTextAssertion(
                text=AFR_NETWORK_LOAD_BALANCER,
                field="network_load_balancer",
                value=AFR_NETWORK_LOAD_BALANCER,
            ),
            HtmlTextAssertion(text=AFR_VCN, field="virtual_cloud_networks", value=AFR_VCN),
            HtmlTextAssertion(text=AFR_CERTIFICATES, field="certificates", value=AFR_CERTIFICATES),
            HtmlTextAssertion(
                text=AFR_APM, field="application_performance_monitoring", value=AFR_APM
            ),
            HtmlTextAssertion(text=AFR_MONITORING, field="monitoring", value=AFR_MONITORING),
            HtmlTextAssertion(
                text=AFR_NOTIFICATIONS, field="notifications", value=AFR_NOTIFICATIONS
            ),
            HtmlTextAssertion(
                text=AFR_EMAIL_DELIVERY, field="email_delivery", value=AFR_EMAIL_DELIVERY
            ),
            HtmlTextAssertion(
                text=AFR_OUTBOUND_DATA, field="outbound_data_transfer", value=AFR_OUTBOUND_DATA
            ),
            HtmlTextAssertion(text=AFR_BASTION, field="bastion", value=AFR_BASTION),
        ),
        required_fields=("service", "offer_type"),
    )
)


#: The Free TRIAL, from Oracle's own Free Tier documentation. Assertion-only: the
#: live document contains zero tables. This is the profile that carries the
#: clearest card QUOTATION Oracle publishes in prose.
ORACLE_FREE_TIER = register_html_profile(
    HtmlExtractionProfile(
        name="oracle_free_tier",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text=FT_TRIAL_CREDITS,
                field="service",
                value="Oracle Cloud Infrastructure Free Trial",
            ),
            HtmlTextAssertion(
                text=FT_TRIAL_CREDITS, field="offer_type", value="new_customer_credit"
            ),
            HtmlTextAssertion(text=FT_TRIAL_CREDITS, field="credit_amount", value="$300"),
            HtmlTextAssertion(text=FT_TRIAL_CREDITS, field="trial_length_days", value="30"),
            # THE decisive fact for this document, quoted rather than inferred.
            HtmlTextAssertion(text=FT_CARD_REQUIRED, field="requires_card", value=True),
            HtmlTextAssertion(
                text=FT_CARD_REQUIRED, field="payment_method_note", value=FT_CARD_REQUIRED
            ),
            HtmlTextAssertion(
                text=FT_TRIAL_END_RECLAIM,
                field="exhaustion_behaviour",
                value="resource_reclaimed",
            ),
            HtmlTextAssertion(text=FT_TRIAL_END_RECLAIM, field="notes", value=FT_TRIAL_END_RECLAIM),
            HtmlTextAssertion(
                text=FT_ALWAYS_FREE_NEVER_EXPIRE,
                field="always_free_cross_reference",
                value=FT_ALWAYS_FREE_NEVER_EXPIRE,
            ),
            HtmlTextAssertion(
                text=FT_TRIAL_END_NO_INTERRUPTION,
                field="post_trial_note",
                value=FT_TRIAL_END_NO_INTERRUPTION,
            ),
            HtmlTextAssertion(
                text=FT_NOT_IN_GOV_REGIONS, field="eligibility", value=FT_NOT_IN_GOV_REGIONS
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


#: THE FLAGSHIP RESULT of this slice. Oracle's Always Free services are genuinely
#: perpetual by quotation AND require a payment card by quotation, on the same
#: document, so this offer is ``Z1_BILLING_EXPOSURE``. Assertion-only: the live
#: document contains zero tables.
ORACLE_ALWAYS_FREE_SERVICES = register_html_profile(
    HtmlExtractionProfile(
        name="oracle_always_free_services",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text=AFS_UNLIMITED_TIME,
                field="service",
                value="Oracle Cloud Always Free services",
            ),
            HtmlTextAssertion(text=AFS_UNLIMITED_TIME, field="offer_type", value="always_free"),
            HtmlTextAssertion(
                text=AFS_UNLIMITED_TIME, field="availability", value=AFS_UNLIMITED_TIME
            ),
            # THE decisive fact. Deleting this block must REJECT the document
            # rather than leave a perpetual offer looking unconditionally free.
            HtmlTextAssertion(text=AFS_CARD_IDENTITY, field="requires_card", value=True),
            HtmlTextAssertion(
                text=AFS_CARD_IDENTITY,
                field="payment_verification_note",
                value=AFS_CARD_IDENTITY,
            ),
            HtmlTextAssertion(
                text=AFS_CARD_TYPES, field="accepted_payment_instruments", value=AFS_CARD_TYPES
            ),
            HtmlTextAssertion(
                text=AFS_NOT_RECLAIMED, field="post_trial_availability", value=AFS_NOT_RECLAIMED
            ),
            HtmlTextAssertion(text=AFS_ONE_ACCOUNT, field="eligibility", value=AFS_ONE_ACCOUNT),
            HtmlTextAssertion(
                text=AFS_NO_SLA_NO_SUPPORT, field="support_note", value=AFS_NO_SLA_NO_SUPPORT
            ),
            HtmlTextAssertion(
                text=AFS_ARM_OVER_LIMIT, field="arm_capacity_note", value=AFS_ARM_OVER_LIMIT
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


#: The public Free Tier hub page. Assertion-only: the live document contains zero
#: tables. Its subject is the US$300 credit, so that is what it extracts.
ORACLE_CLOUD_FREE_TIER = register_html_profile(
    HtmlExtractionProfile(
        name="oracle_cloud_free_tier",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text=CFT_CREDIT_30_DAYS, field="service", value="Oracle Cloud Free Tier"
            ),
            HtmlTextAssertion(
                text=CFT_CREDIT_30_DAYS, field="offer_type", value="new_customer_credit"
            ),
            HtmlTextAssertion(text=CFT_CREDIT_30_DAYS, field="credit_amount", value="US$300"),
            HtmlTextAssertion(text=CFT_CREDIT_30_DAYS, field="trial_length_days", value="30"),
            HtmlTextAssertion(text=CFT_SWITCH_TO_PAYG, field="notes", value=CFT_SWITCH_TO_PAYG),
            HtmlTextAssertion(text=CFT_DO_NOTHING, field="post_trial_note", value=CFT_DO_NOTHING),
            HtmlTextAssertion(text=CFT_CARD_IDENTITY, field="requires_card", value=True),
            HtmlTextAssertion(
                text=CFT_CARD_IDENTITY,
                field="payment_verification_note",
                value=CFT_CARD_IDENTITY,
            ),
            HtmlTextAssertion(text=CFT_ONE_ACCOUNT, field="eligibility", value=CFT_ONE_ACCOUNT),
            HtmlTextAssertion(
                text=CFT_IDLE_ACCOUNTS, field="account_idle_note", value=CFT_IDLE_ACCOUNTS
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


#: The authoritative statement of what happens when the free credit promotion
#: ends. Assertion-only: the live document contains zero tables.
#:
#: ``requires_card`` is ABSENT here, exactly as on the Always Free Resources
#: document, so this offer classifies ``UNKNOWN`` rather than Z1.
ORACLE_FREE_CREDIT_PROMOTION = register_html_profile(
    HtmlExtractionProfile(
        name="oracle_free_credit_promotion",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text=FCP_GRACE_AND_RECLAIM,
                field="service",
                value="Oracle Cloud Free Credit Promotion",
            ),
            HtmlTextAssertion(
                text=FCP_GRACE_AND_RECLAIM, field="offer_type", value="new_customer_credit"
            ),
            HtmlTextAssertion(
                text=FCP_GRACE_AND_RECLAIM,
                field="exhaustion_behaviour",
                value="resource_reclaimed",
            ),
            HtmlTextAssertion(
                text=FCP_GRACE_AND_RECLAIM, field="notes", value=FCP_GRACE_AND_RECLAIM
            ),
            HtmlTextAssertion(text=FCP_EXPIRES_30_DAYS, field="promotion_length_days", value="30"),
            HtmlTextAssertion(
                text=FCP_EXPIRES_CREDITS_USED,
                field="promotion_end_condition",
                value=FCP_EXPIRES_CREDITS_USED,
            ),
            HtmlTextAssertion(
                text=FCP_ALWAYS_FREE_LIFE_OF_ACCOUNT,
                field="always_free_cross_reference",
                value=FCP_ALWAYS_FREE_LIFE_OF_ACCOUNT,
            ),
            HtmlTextAssertion(text=FCP_UPGRADE_SCOPE, field="scope_note", value=FCP_UPGRADE_SCOPE),
        ),
        required_fields=("service", "offer_type"),
    )
)


#: One real SERVICE whose own page states both its perpetuity and the card
#: requirement. Assertion-only: the live document contains zero tables.
ORACLE_MYSQL_HEATWAVE_ALWAYS_FREE = register_html_profile(
    HtmlExtractionProfile(
        name="oracle_mysql_heatwave_always_free",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(text=MHW_SYSTEM, field="service", value="Oracle MySQL HeatWave"),
            HtmlTextAssertion(text=MHW_SYSTEM, field="heatwave_system", value=MHW_SYSTEM),
            HtmlTextAssertion(text=MHW_UNLIMITED_TIME, field="offer_type", value="always_free"),
            HtmlTextAssertion(
                text=MHW_UNLIMITED_TIME, field="availability", value=MHW_UNLIMITED_TIME
            ),
            HtmlTextAssertion(
                text=MHW_UNLIMITED_LEADIN, field="quota_basis", value=MHW_UNLIMITED_LEADIN
            ),
            HtmlTextAssertion(text=MHW_STORAGE, field="heatwave_storage", value="50 GB"),
            HtmlTextAssertion(
                text=MHW_BACKUP_STORAGE, field="heatwave_backup_storage", value="50 GB"
            ),
            HtmlTextAssertion(text=MHW_CAPABILITIES, field="capabilities", value=MHW_CAPABILITIES),
            HtmlTextAssertion(text=MHW_CARD_IDENTITY, field="requires_card", value=True),
            HtmlTextAssertion(
                text=MHW_CARD_IDENTITY,
                field="payment_verification_note",
                value=MHW_CARD_IDENTITY,
            ),
        ),
        required_fields=("service", "offer_type"),
    )
)


__all__: Sequence[str] = (
    "AFR_TITLE",
    "AFR_FREE_FOR_LIFE_OF_ACCOUNT",
    "AFR_BACKUP_LIMIT_FAILS",
    "AFR_COMPUTE_AMD",
    "AFR_COMPUTE_ARM",
    "AFR_IDLE_RECLAIM",
    "AFR_BLOCK_VOLUME",
    "AFR_REGION_COST",
    "AFR_OBJECT_STORAGE",
    "AFR_VAULT",
    "AFR_RESOURCE_MANAGER",
    "AFR_AUTONOMOUS_DB",
    "AFR_NOSQL",
    "AFR_MYSQL_HEATWAVE",
    "AFR_LOAD_BALANCER",
    "AFR_NETWORK_LOAD_BALANCER",
    "AFR_VCN",
    "AFR_CERTIFICATES",
    "AFR_APM",
    "AFR_MONITORING",
    "AFR_NOTIFICATIONS",
    "AFR_EMAIL_DELIVERY",
    "AFR_OUTBOUND_DATA",
    "AFR_BASTION",
    "FT_TRIAL_CREDITS",
    "FT_CARD_REQUIRED",
    "FT_TRIAL_END_RECLAIM",
    "FT_ALWAYS_FREE_NEVER_EXPIRE",
    "FT_TRIAL_END_NO_INTERRUPTION",
    "FT_NOT_IN_GOV_REGIONS",
    "AFS_UNLIMITED_TIME",
    "AFS_CARD_IDENTITY",
    "AFS_CARD_TYPES",
    "AFS_NOT_RECLAIMED",
    "AFS_ONE_ACCOUNT",
    "AFS_NO_SLA_NO_SUPPORT",
    "AFS_ARM_OVER_LIMIT",
    "CFT_CREDIT_30_DAYS",
    "CFT_SWITCH_TO_PAYG",
    "CFT_DO_NOTHING",
    "CFT_CARD_IDENTITY",
    "CFT_ONE_ACCOUNT",
    "CFT_IDLE_ACCOUNTS",
    "FCP_GRACE_AND_RECLAIM",
    "FCP_EXPIRES_30_DAYS",
    "FCP_EXPIRES_CREDITS_USED",
    "FCP_ALWAYS_FREE_LIFE_OF_ACCOUNT",
    "FCP_UPGRADE_SCOPE",
    "MHW_SYSTEM",
    "MHW_UNLIMITED_TIME",
    "MHW_UNLIMITED_LEADIN",
    "MHW_STORAGE",
    "MHW_BACKUP_STORAGE",
    "MHW_CAPABILITIES",
    "MHW_CARD_IDENTITY",
    "ORACLE_ALWAYS_FREE_RESOURCES",
    "ORACLE_FREE_TIER",
    "ORACLE_ALWAYS_FREE_SERVICES",
    "ORACLE_CLOUD_FREE_TIER",
    "ORACLE_FREE_CREDIT_PROMOTION",
    "ORACLE_MYSQL_HEATWAVE_ALWAYS_FREE",
)
