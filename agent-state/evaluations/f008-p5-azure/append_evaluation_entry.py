"""Append this evaluation's entry to agent-state/evaluation.json without rewriting it.

agent-state is shared and concurrently written by sibling sessions, so this
splices the new object in before the closing ``]`` and leaves every pre-existing
byte identical rather than reserialising the whole array.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
LEDGER = REPO / "agent-state" / "evaluation.json"

ENTRY = {
    "schema_version": 1,
    "feature_id": "F008",
    "increment": "F008 P5 - Microsoft Azure provider (evidence-pinned profiles)",
    "evaluated_commit": "59916656d7fa3e3615c08fa3116a20a54c980b4f",
    "commit_sha_evaluated": "59916656d7fa3e3615c08fa3116a20a54c980b4f",
    "evaluator": "fresh-context Level-2 evaluator (independent session)",
    "evaluated_at": "2026-08-14T18:05:00-06:00",
    "harness": "Copilot CLI, own detached worktree at the PR head, own scratch "
    "PostgreSQL 16 database atlas_eval_l2_azure (the builder's "
    "atlas_f008_p5_azure was deliberately NOT reused)",
    "required_evaluation_level": 2,
    "evaluation_level": 2,
    "disposition": "failed",
    "overall_disposition": "failed",
    "blocking_issues": [
        "MEASURED: Azure App Service returns TWO blocking conditions, not one. "
        "The real classifier returns exactly ['Whether a payment card is required "
        "is unknown.', 'Whether the offer has paid dependencies is unknown.']. "
        "has_paid_dependencies is absent from every Azure profile, so it is None "
        "and gate 4 reports it alongside the card.",
        "The claim 'One unknown separates it from Z0' is therefore FALSE and is "
        "committed in apps/api/app/ingest/adapters/profiles/azure.py:90, "
        "docs/PROVIDER_ADAPTERS.md:363, config/examples/providers/azure.example.yaml:193, "
        "and agent-state/progress.md:3061.",
        "The claim that the offer fails ONLY for want of a card fact is likewise "
        "FALSE and is committed in config/examples/providers/azure.example.yaml:70-71, "
        "docs/PROVIDER_ADAPTERS.md:323 ('card gate alone'), "
        "agent-state/current_contract.json:18 and :41, and in the docstring of "
        "tests/unit/test_adapter_azure.py:468.",
        "The test NAME test_the_safest_azure_offer_fails_only_on_the_card_gate "
        "asserts something the test does not verify: it checks all(...unknown...) "
        "and any(...card...) but never that the card is the ONLY blocker, so it "
        "passes while its own name is false.",
        "EXHAUSTIVE ENUMERATION: holding the document's own offer_type ('other') "
        "and exhaustion_behaviour ('site_disabled_until_reset'), exactly 1 of 9 "
        "tri-state combinations reaches Z0, and it requires BOTH requires_card=False "
        "AND has_paid_dependencies=False. Resolving the card alone yields UNKNOWN.",
    ],
    "summary": "The evidence engineering, guards, controls, fixtures, protected state and "
    "every published verdict are sound and were independently re-derived. The "
    "increment fails on a single defect: the characterisation of the App Service "
    "near-miss - the one object the brief named as highest-consequence - is "
    "factually wrong about how far the offer sits from Z0, and that wrong "
    "characterisation is committed to code, config, docs, a test name and the "
    "agent-state ledger. The error runs in the CONSERVATIVE direction (the offer "
    "is further from Z0 than claimed), so NO false-free claim is published and "
    "the product's cardinal rule is not violated. It is a precision defect, not a "
    "safety defect - but it is the same defect class that failed an earlier slice: "
    "a description asserting something the artefact does not exhibit. The builder "
    "demonstrably understands the mechanism, having applied the correct two-unknown "
    "reasoning to the Students offer in "
    "test_pinning_the_students_card_claim_would_change_no_verdict.",
    "acceptance_steps": [
        {
            "step": 1,
            "name": "cosmos_db_headline_perpetual_is_not_free",
            "passed": True,
            "evidence": [
                "Both quotes present verbatim and EXACTLY ONCE in the committed capture.",
                "MEASURED: they occupy DIFFERENT <p> blocks - perpetuity sha256(12)=370b8e4b3f8f, "
                "allowance+overage sha256(12)=7554b62227b0.",
                "The allowance-granting block DOES contain 'billed at regular price' together "
                "with the 25 GB / 1000 RU/s grant, so the in-repo prose ('the block that grants "
                "the allowance also states') is ACCURATE. Only the PR body's bolded 'same block', "
                "placed immediately after the perpetuity quote, read as false; the builder "
                "corrected it without a new commit and head is unchanged at 5991665.",
                "Verdict chain re-derived on the real classifier: offer_type=always_free from the "
                "perpetuity block, exhaustion_behaviour=automatic_billing from the allowance "
                "block, result Z1_BILLING_EXPOSURE. Not a hand-set field.",
                "Independent of AWS: different provider, document and pinning structure; the "
                "merged aws.py carries no equivalent text.",
            ],
        },
        {
            "step": 2,
            "name": "positive_control_is_load_bearing",
            "passed": True,
            "evidence": [
                "Reproduced independently. Healthy engine: control -> Z0_TRUE_FREE.",
                "PATCHED engine.SAFE_EXHAUSTION = frozenset() (was 9): control -> UNKNOWN, "
                "i.e. the control FAILS.",
                "All seven Azure sources still classify non-Z0 under the broken engine, so the "
                "sweep SURVIVES. The asymmetry is real and the zero count is carried by the "
                "control rather than by an incapable classifier.",
                "SAFE_EXHAUSTION restored to 9 members after the probe.",
            ],
        },
        {
            "step": 3,
            "name": "students_card_claim_published_not_gated",
            "passed": True,
            "evidence": [
                "card_claim == 'No credit card required' IS extracted, so the favourable fact is "
                "published rather than suppressed; requires_card is absent.",
                "Neutrality re-derived: as-extracted UNKNOWN; with requires_card=False UNKNOWN; "
                "most generous (no card AND no paid deps) reaches Z2 and never Z0.",
                "The capture discloses the two-offer page and the cross-scope duplicate.",
            ],
        },
        {
            "step": 4,
            "name": "guards_non_vacuous_and_independent",
            "passed": True,
            "evidence": [
                "Baseline GREEN first (error=None) before every mutation.",
                "_block_containing asserts the anchor matched EXACTLY ONE block, so a mutation "
                "that does not apply fails loudly instead of silently.",
                "Deleting the overage block and deleting the perpetuity block both yield "
                "assertion_not_found but with DIFFERENT detail (assertion=3 vs assertion=1), so "
                "each guard fails for its OWN reason. Because the facts live in two blocks, "
                "deleting either rejects the document.",
                "App Service: with the real profile, deleting APP_SERVICE_QUOTA_STOP REJECTS "
                "(assertion_not_found). With the guard disabled the candidate degrades to no "
                "exhaustion_behaviour at all and classifies UNKNOWN with three blockers - it "
                "does NOT become a perpetual-looking free plan and never reaches Z0.",
                "Mutation battery is 11 cases: 8 predicted errors, 3 controls that must not fire.",
            ],
        },
        {
            "step": 5,
            "name": "retained_versus_pinned_block_count_fix",
            "passed": True,
            "evidence": [
                "test_every_capture_records_both_reconciliation_directions RECOMPUTES the "
                "retained count from the committed bytes with _DocumentCollector and compares it "
                "to retained_live_block_count and to the prose, so the sidecar is not trusted.",
                "It also requires the literal disclaimer 'RETAINED is not the same as ASSERTED' "
                "and checks every asserted block is one of the retained ones.",
                "Consistent across all committed captures. CAVEAT: the generator itself lives "
                "outside the repository and could not be inspected, so 'fixed at the generator' "
                "is corroborated by the uniformity of the output and by the independent "
                "recomputation, not directly observed.",
            ],
        },
        {
            "step": 6,
            "name": "suite_lint_ci_and_baseline",
            "passed": True,
            "evidence": [
                "PR head on MY OWN scratch DB atlas_eval_l2_azure: 2372 passed, 2 skipped.",
                "Base 3b382aa on a separate DB atlas_eval_l2_base: 2184 passed, 2 skipped.",
                "Delta +188, zero broken; collected node IDs mentioning 'azure' = 188 exactly; "
                "2374 collected = 2372 + 2 skipped.",
                "ruff check . and ruff format --check . both clean (223 files).",
                "All 5 CI checks pass, and the run's headSha is 59916656d7fa3e... i.e. the exact "
                "head under test.",
                "Baseline re-derived rather than inherited: main at 3b382aa is 'success' and is "
                "the only success among the last several main runs, so main is at zero red and "
                "any red would be attributable to this PR. There is none.",
            ],
        },
        {
            "step": 7,
            "name": "protected_state",
            "passed": True,
            "evidence": [
                "Blob comparison (git rev-parse <sha>:<path>), NOT git diff --numstat: all 11 "
                "protected paths byte-identical between base and head, including "
                "apps/api/app/classify/engine.py - this slice changes no classifier behaviour.",
                "agent-state/feature_list.json blob identical (154de1fef2ba); F008 still "
                "passes:false with last_verified_at null.",
                "agent-state/progress.md is a STRICT BYTE-PREFIX append verified on raw blob "
                "bytes: 616910 -> 638402, first divergence only at end-of-base, 21492 bytes "
                "appended, zero deletions.",
            ],
        },
        {
            "step": 8,
            "name": "app_service_near_miss_characterisation",
            "passed": False,
            "evidence": [
                "MEASURED blocking conditions = 2, not 1 (card AND paid dependencies).",
                "The safe-exhaustion finding itself is REAL and correctly handled: "
                "exhaustion_behaviour='site_disabled_until_reset' is in SAFE_EXHAUSTION, gate 3 "
                "is cleared entirely, and the published verdict UNKNOWN is correct.",
                "'fails ONLY at the unknown-conditions gate' is TRUE; 'one unknown separates it "
                "from Z0' and 'card gate alone' are FALSE.",
                "Corroboration note: that this gate fails on ABSENCE rather than on quoted "
                "evidence was disclosed to me by the orchestrator relaying the builder, so that "
                "specific property is corroboration rather than independent discovery. The "
                "TWO-versus-ONE blocking-condition count and the route enumeration are "
                "independent findings neither party had raised.",
            ],
        },
    ],
    "known_issues_or_risks": [
        "The App Service verdict rests on an ABSENCE (no payment block on the document) rather "
        "than on a quotation, which is structurally weaker than the free-account Z1: it would "
        "flip the moment anything supplies those fields from any source. Two fields must be "
        "supplied, not one, which makes it slightly more robust than the builder claimed.",
        "offer_type 'other' is Z0-CAPABLE in this engine: it is not in "
        "TEMPORARY_CONDITIONAL_OFFER_TYPES and is not otherwise gated, so DATA_MODEL rule 3 "
        "('route for review until the structure is evidenced') is carried by the two unknowns "
        "alone, not by the offer type. Worth an explicit test in a later slice.",
        "The reconciling generator is outside the repository, so its refuse-to-write behaviour "
        "is attested by its output and by in-repo recomputation rather than directly evaluated.",
        "No live fetching was performed by this evaluation; CI performs zero socket operations "
        "by design and tests/unit/test_no_live_fetcher_in_tests.py enforces it.",
    ],
    "recommendation": "Do NOT merge as-is. The remedy is text-only and cheap: correct the "
    "App Service characterisation in the six committed locations to say that the offer clears "
    "the billing gate entirely and fails at the unknown-conditions gate on TWO unknowns - the "
    "payment card and paid dependencies - neither of which its document states; rename "
    "test_the_safest_azure_offer_fails_only_on_the_card_gate accordingly; and strengthen that "
    "test to assert the EXACT blocking-condition set so the claim becomes verified rather than "
    "asserted. No fixture, profile, verdict or classifier change is required, and no published "
    "verdict is wrong today. Re-run the suite and re-evaluate step 8 only.",
}


def main() -> None:
    # Operate on BYTES end to end. Reading and writing text on Windows performs
    # newline translation, which silently rewrites every line of a shared ledger
    # instead of appending to it -- this evaluation caught itself doing exactly
    # that on the first attempt.
    raw = LEDGER.read_bytes()
    existing = json.loads(raw.decode("utf-8"))
    if any(
        entry.get("commit_sha_evaluated") == ENTRY["commit_sha_evaluated"] for entry in existing
    ):
        print("entry for this commit already present; nothing appended")
        return

    stripped = raw.rstrip()
    if not stripped.endswith(b"]"):
        raise SystemExit("evaluation.json does not end with a JSON array close")
    head = stripped[: stripped.rindex(b"]")].rstrip()

    block = json.dumps(ENTRY, indent=2, ensure_ascii=False)
    block = "\n".join("  " + line for line in block.splitlines())
    updated = head + b",\n" + block.encode("utf-8") + b"\n]\n"

    parsed = json.loads(updated.decode("utf-8"))
    if parsed[: len(existing)] != existing:
        raise SystemExit("append would alter a pre-existing entry; refusing to write")
    if not updated.startswith(head):
        raise SystemExit("append would alter the leading bytes; refusing to write")

    LEDGER.write_bytes(updated)
    print(f"appended 1 entry; {len(existing)} -> {len(parsed)}")


if __name__ == "__main__":
    main()
