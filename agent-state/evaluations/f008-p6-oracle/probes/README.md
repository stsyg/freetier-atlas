# ⚠️ Archived Level-2 evaluation probes — F008 P6 (Oracle Cloud provider)

**Read this before you run anything in this directory.**

These are **point-in-time probe scripts** from a specific Level-2 evaluation of the
F008 P6 Oracle Cloud provider work. They are the instruments a fresh-context
evaluator used to *produce* a verdict — not general-purpose tooling and not a
regression suite. They are committed only so the verdict they produced can be
inspected and, if needed, contested against the exact tree they ran on.

## What they were run against

Each probe generation was run against one specific commit. Do not assume they
describe any later state of the repository. The `evaluation_entry*.json` files here
are the *inputs* those runs appended to the record — not fresh measurements.

| generation | probes / helpers | commit it was run against | matching record | disposition |
|---|---|---|---|---|
| initial | `probe_blockwise.py`, `probe_card_counterfactual.py`, `probe_guard_mutations.py`, `probe_perpetual_table.py`, `probe_pincounts.py`, `probe_rationale_truth.py`, `probe_substring_bug.py`, `probe_title_loadbearing.py`, `probe_z0_recount.py`, `append_evaluation.py` | `85ef245dfce2df2aaac5a90db89f820dd6bbcf42` | [`../../F008-p6-oracle-provider-85ef245d.json`](../../F008-p6-oracle-provider-85ef245d.json) | failed |
| scoped re-check | `probe_recheck_pincounts.py` | `c4a7180f560594151e84a4c10f4eeba65ff805ff` | [`../../F008-p6-oracle-provider-recheck-c4a7180f.json`](../../F008-p6-oracle-provider-recheck-c4a7180f.json) | passed |

## Why you must not trust them if you re-run them

- They are **not maintained**. Nobody updates them when the code they measure moves.
- They are **not run by CI**. No gate will tell you when they have gone stale.
- They encode the evaluation's *own* assumptions about the subject, so a re-run
  against a newer tree measures *that tree against the old evaluation's assumptions*,
  not against today's intended behaviour.

**If a re-run here disagrees with the record, the tree moved — that is not evidence
that the original verdict was wrong.** A stale probe that returns a confident wrong
answer is worse than no probe at all. Treat any answer these produce today as a
question to investigate against the pinned commit above, never as a fact about `main`.
