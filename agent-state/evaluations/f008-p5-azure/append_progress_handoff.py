"""Append this evaluation's handoff to agent-state/progress.md as raw bytes.

progress.md is append-only and shared with concurrently running sibling sessions,
so this opens the file in binary append mode. It never reads, rewrites or
reformats a single pre-existing byte, which is what makes the strict
byte-prefix property hold by construction rather than by assertion.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PROGRESS = REPO / "agent-state" / "progress.md"

MARKER = "## 2026-08-14 - F008 P5 Azure - fresh-context Level-2 EVALUATION"

ENTRY = f"""

{MARKER}

- **Role:** independent fresh-context Level-2 evaluator. I did not build this slice and
  treated the builder's report as claims to re-derive rather than facts to confirm.
  Subject: PR #72, draft, head `59916656d7fa3e3615c08fa3116a20a54c980b4f`, base `main` =
  `3b382aa4f95a6abb02e6739637449302313dd618`. Both SHAs re-derived with `git ls-remote`
  at the start and confirmed unchanged at the end.
- **DISPOSITION: FAILED**, on one narrowly scoped defect. Everything else re-derived clean.
- **Instrument independence [M]:** evaluated in my OWN detached worktree at the PR head,
  with my OWN scratch PostgreSQL 16 databases `atlas_eval_l2_azure` (head) and
  `atlas_eval_l2_base` (baseline). The builder's `atlas_f008_p5_azure` was deliberately
  NOT reused: a shared database is a shared instrument, and reuse would mask
  state-dependent behaviour rather than reveal it.
- **THE BLOCKING FINDING - App Service is TWO unknowns from Z0, not one [M]:** the builder
  reports that `azure_app_service_quotas` "fails on the card gate ALONE" and that "One
  unknown separates it from Z0". MEASURED on the real classifier, the offer returns exactly
  TWO blocking conditions: `Whether a payment card is required is unknown.` AND `Whether
  the offer has paid dependencies is unknown.` `has_paid_dependencies` is set by no Azure
  profile, so it is `None`, and gate 4 reports it alongside the card. EXHAUSTIVE
  ENUMERATION over the tri-state fields, holding the document's own `offer_type='other'`
  and `exhaustion_behaviour='site_disabled_until_reset'`: exactly 1 of 9 combinations
  reaches Z0, and it requires BOTH `requires_card=False` AND `has_paid_dependencies=False`.
  Resolving the card alone yields UNKNOWN.
- **Where the wrong claim is committed [M]:**
  `apps/api/app/ingest/adapters/profiles/azure.py:90`, `docs/PROVIDER_ADAPTERS.md:323` and
  `:363`, `config/examples/providers/azure.example.yaml:70-71` and `:193-194`,
  `agent-state/current_contract.json:18` and `:41`, `agent-state/progress.md:3061`, and the
  docstring at `tests/unit/test_adapter_azure.py:468`. Unlike the Cosmos "same block"
  wording - which was PR-body-only and already corrected - this one is in the repository.
- **The test name asserts what the test does not verify [M]:**
  `test_the_safest_azure_offer_fails_only_on_the_card_gate` checks `all(...unknown...)` and
  `any(...card...)`, but never that the card is the ONLY blocker, so it passes while its
  own name is false. A later author reconciling the test to its name would find it fails.
- **The builder understands the mechanism, so this is a precision defect and not a
  misunderstanding [M]:** `test_pinning_the_students_card_claim_would_change_no_verdict`
  reasons correctly and explicitly that "With paid dependencies unknown, which is the real
  state of the evidence, the verdict is UNKNOWN either way." The same two-unknown logic was
  simply not applied to App Service.
- **Severity, stated plainly:** the error runs in the CONSERVATIVE direction - the offer is
  FURTHER from Z0 than claimed - so NO false-free claim is published, every published
  verdict is correct, and the product's cardinal rule is intact. It fails because the brief
  named this exact claim as the one to attack hardest, because the builder itself argued
  this precision is material ("not Z0 for a good reason" versus "not Z0 for want of one
  fact"), and because a later slice acting on "one unknown" would supply the card fact,
  expect Z0, and get UNKNOWN.
- **Cosmos DB headline RE-DERIVED and it holds [M]:** both quotes verbatim and EXACTLY ONCE
  in the committed capture. They occupy DIFFERENT `<p>` blocks - perpetuity sha256(12)
  `370b8e4b3f8f`, allowance+overage `7554b62227b0`. The allowance-granting block DOES carry
  "billed at regular price" together with the 25 GB / 1000 RU/s grant, so the in-repo prose
  ("the block that grants the allowance also states") is ACCURATE; only the PR body's
  bolded "same block", sitting immediately after the perpetuity quote, read as false.
  Verdict chain re-derived end to end on the real classifier: `always_free` from one block,
  `automatic_billing` from the other, result `Z1_BILLING_EXPOSURE`. Independent of AWS -
  different provider, document and pinning structure.
- **Both Cosmos guards are non-vacuous AND fail for their own reasons [M]:** baseline GREEN
  (`error=None`) established before every mutation; `_block_containing` asserts the anchor
  matched EXACTLY ONE block. Deleting the overage block yields `assertion_not_found` with
  detail `assertion=3`; deleting the perpetuity block yields `assertion_not_found` with
  detail `assertion=1`. Because the two facts live in two blocks, deleting EITHER rejects
  the document.
- **App Service safe-stop guard is load-bearing [M]:** with the real profile, deleting
  `APP_SERVICE_QUOTA_STOP` REJECTS. With the guard disabled the candidate degrades to no
  `exhaustion_behaviour` at all and classifies UNKNOWN with three blockers - it does NOT
  become a perpetual-looking free plan and never reaches Z0.
- **Positive control REPRODUCED as load-bearing [M]:** healthy engine -> `Z0_TRUE_FREE`;
  with `engine.SAFE_EXHAUSTION` emptied (was 9) the control -> `UNKNOWN`, i.e. it FAILS,
  while all seven Azure sources still classify non-Z0, i.e. the sweep SURVIVES. Restored to
  9 afterwards.
- **Students card claim [M]:** `card_claim == "No credit card required"` IS extracted, so
  the favourable fact is published rather than suppressed; `requires_card` absent.
  Neutrality re-derived: as-extracted UNKNOWN, with `requires_card=False` still UNKNOWN,
  most-generous reaches Z2 and never Z0. Not evidence suppression.
- **Retained-versus-pinned block count [M]:**
  `test_every_capture_records_both_reconciliation_directions` RECOMPUTES the retained count
  from the committed bytes with `_DocumentCollector` rather than trusting the sidecar, and
  requires the literal "RETAINED is not the same as ASSERTED" disclaimer. CAVEAT: the
  generator lives outside the repository and could not be inspected, so "fixed at the
  generator" is corroborated by output uniformity plus independent recomputation, not
  directly observed.
- **Suite, lint, CI [M]:** head on my own DB **2372 passed, 2 skipped**; base `3b382aa` on
  a separate DB **2184 passed, 2 skipped**; delta **+188, zero broken**, and collected node
  IDs mentioning `azure` = **188** exactly (2374 collected = 2372 + 2). `ruff check .` and
  `ruff format --check .` both clean over 223 files. All 5 CI checks pass and the run's
  `headSha` is the exact head under test. Baseline re-derived rather than inherited: `main`
  at `3b382aa` is the ONLY success among the recent main runs, so main is at zero red and
  any red would be attributable to this PR - there is none.
- **Protected state [M]:** blob comparison via `git rev-parse <sha>:<path>`, NOT
  `git diff --numstat`. All 11 protected paths byte-identical between base and head,
  including `apps/api/app/classify/engine.py` - this slice changes no classifier behaviour.
  `agent-state/feature_list.json` identical at `154de1fef2ba`, F008 still `passes:false`
  with `last_verified_at` null. `agent-state/progress.md` is a STRICT BYTE-PREFIX append
  verified on RAW blob bytes: 616910 -> 638402, 21492 appended, zero deletions.
- **Things I got wrong myself, disclosed [M]:** (1) I first probed protected state with the
  path `apps/api/app/ingest/source_scan.py`, which does not exist - the file is
  `tests/support/source_scan.py` - and my comparison reported DIFFERS for a path neither
  commit had. Caught because `git diff --stat` and my own check disagreed; re-derived with
  path validation and all 11 came back IDENTICAL. (2) My first attempt to append to
  `evaluation.json` used Python `write_text`, which performed newline translation, rewrote
  all 495 line endings to CRLF and corrupted a U+2014 em-dash in a PRE-EXISTING entry - a
  wholesale rewrite of a shared ledger, the exact thing the brief forbids. Caught by
  comparing the parsed pre-existing entries before and after; restored with
  `git checkout --`, confirmed the worktree hash matched the HEAD blob exactly, and rewrote
  the appender to operate on raw BYTES with a refuse-to-write guard if any pre-existing
  entry would change.
- **Independence disclosure:** that the App Service card gate fails on ABSENCE rather than
  on quoted evidence was relayed to me by the orchestrator from the builder, so that
  specific property is CORROBORATION, not independent discovery. The TWO-versus-ONE
  blocking-condition count and the route enumeration are independent findings neither party
  had raised.
- **Additional observation, not blocking:** `offer_type='other'` is Z0-CAPABLE in this
  engine - it is absent from `TEMPORARY_CONDITIONAL_OFFER_TYPES` and gated nowhere else -
  so `DATA_MODEL` rule 3's "route for review until the structure is evidenced" is carried
  by the two unknowns alone, not by the offer type. Worth an explicit test in a later slice.
- **Remedy (text-only, no behaviour change):** correct the App Service characterisation in
  the six committed locations to say the offer clears the billing gate entirely and fails
  at the unknown-conditions gate on TWO unknowns - payment card and paid dependencies -
  neither of which its document states; rename the test accordingly; and strengthen it to
  assert the EXACT blocking-condition set so the claim becomes verified rather than
  asserted. No fixture, profile, verdict or classifier change is required.
- **Probes committed:**
  `agent-state/evaluations/f008-p5-azure/probes/probe_appservice_z0_routes.py` and
  `probe_guard_independence.py`, both `ruff`-clean and re-run after formatting.
- **Boundary respected:** I did not modify, rebase, merge or comment on PR #72, did not
  touch `agent-state/feature_list.json`, and did NOT open a pull request.
"""


def main() -> None:
    if MARKER in PROGRESS.read_text(encoding="utf-8"):
        print("handoff already appended; nothing written")
        return
    before = PROGRESS.stat().st_size
    with PROGRESS.open("ab") as handle:
        handle.write(ENTRY.encode("utf-8"))
    after = PROGRESS.stat().st_size
    print(f"appended {after - before} bytes ({before} -> {after}); zero pre-existing bytes touched")


if __name__ == "__main__":
    main()
