"""Are the two prose tightenings TRUE, not merely pinned by a test?

``test_the_containers_rationale_says_which_document_carries_which_entry`` and
``test_the_serverless_rationale_names_and_dismisses_the_one_serverless_hit``
assert that certain sentences appear in the shipped YAML. Neither test checks
that those sentences are FACTUALLY correct. This probe measures the underlying
claims block-wise against the committed captures of the two documents the
rationale names.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO / "apps" / "api"))
sys.path.insert(0, str(REPO))

from app.ingest.adapters.html import _DocumentCollector  # noqa: E402

from tests.support.fixtures import load_case  # noqa: E402

FAQ = "oracle-always-free-services"  # www.oracle.com/cloud/free/faq/
OCI = "oracle-always-free-resources"  # docs.oracle.com .../Always_Free_Resources.htm


def blocks(case: str) -> list[str]:
    fixture = load_case("oracle", "html", case)
    collector = _DocumentCollector()
    collector.feed(fixture.content.decode("utf-8"))
    collector.close()
    return [" ".join(b.text.split()) for b in collector.text_blocks]


faq_blocks = blocks(FAQ)
oci_blocks = blocks(OCI)

print(f"parsed block counts: FAQ={len(faq_blocks)}  OCI={len(oci_blocks)}")
print()

for needle in (
    "APEX Application Development",
    "Content Management Starter Edition",
    "Exadata Infrastructure Type: Serverless",
):
    in_faq = sum(1 for b in faq_blocks if needle in b)
    in_oci = sum(1 for b in oci_blocks if needle in b)
    print(f"{needle!r:<42} FAQ={in_faq}  OCI={in_oci}")

print()
faq_sl = [b for b in faq_blocks if "serverless" in b.lower()]
oci_sl = [b for b in oci_blocks if "serverless" in b.lower()]
print(f"blocks containing 'serverless': FAQ={len(faq_sl)}  OCI={len(oci_sl)}")
for b in faq_sl + oci_sl:
    print(f"   -> {b[:120]}")

print()
for needle in ("Functions", "container", "Container"):
    in_faq = sum(1 for b in faq_blocks if needle in b)
    in_oci = sum(1 for b in oci_blocks if needle in b)
    print(f"{needle!r:<42} FAQ={in_faq}  OCI={in_oci}")

print("\n--- VERDICT on each rationale claim, re-derived from the committed captures ---")
apex_both = any("APEX Application Development" in b for b in faq_blocks) and any(
    "APEX Application Development" in b for b in oci_blocks
)
cms_faq_only = any("Content Management Starter Edition" in b for b in faq_blocks) and not any(
    "Content Management Starter Edition" in b for b in oci_blocks
)
serverless_once = (len(faq_sl) + len(oci_sl)) == 1
print(f"  'APEX ... appears on BOTH documents'          -> {apex_both}")
print(f"  'Content Management ... on the FAQ ONLY'      -> {cms_faq_only}")
print(f"  'serverless occurs exactly once'             -> {serverless_once}")
print(
    "\nNOTE: if these read False, the claim may still be true of the LIVE pages "
    "while being unverifiable from the committed captures."
)
