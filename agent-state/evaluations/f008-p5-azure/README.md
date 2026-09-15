# ⚠️ Archived Level-2 evaluation probes — F008 P5 (Microsoft Azure provider)

**Read this before you run anything in this directory.**

These are **point-in-time probe scripts** from a specific Level-2 evaluation of the
F008 P5 Microsoft Azure provider work. They are the instruments a fresh-context
evaluator used to *produce* a verdict — not general-purpose tooling and not a
regression suite. They are committed only so the verdict they produced can be
inspected and, if needed, contested against the exact tree they ran on.

## What they were run against

Each probe generation was run against one specific commit. Do not assume they
describe any later state of the repository.

| generation | probes / helpers | commit it was run against | matching record | disposition |
|---|---|---|---|---|
| initial | `append_evaluation_entry.py`, `append_progress_handoff.py`, `probes/*.py` | `59916656d7fa3e3615c08fa3116a20a54c980b4f` | [`../F008-f008-p5-microsoft-azure-provider-evidence-pinned-profiles-59916656.json`](../F008-f008-p5-microsoft-azure-provider-evidence-pinned-profiles-59916656.json) | failed |
| scoped re-check | `append_recheck_entry.py`, `append_recheck_handoff.py`, `probes/recheck_scoped_diff.py` | `979c5efc4ff6c8dc4171c950d20109b00e37703f` | [`../F008-f008-p5-microsoft-azure-provider-scoped-re-check-of-the-corr-979c5efc.json`](../F008-f008-p5-microsoft-azure-provider-scoped-re-check-of-the-corr-979c5efc.json) | passed |

## Why you must not trust them if you re-run them

- They are **not maintained**. Nobody updates them when the code they measure moves.
- They are **not run by CI**. No gate will tell you when they have gone stale.
- They **re-declare the subject's rules locally on purpose**, so a re-run against a
  newer tree measures *that tree against the old evaluation's assumptions*, not
  against today's intended behaviour.

**If a re-run here disagrees with the record, the tree moved — that is not evidence
that the original verdict was wrong.** A stale probe that returns a confident wrong
answer is worse than no probe at all. Treat any answer these produce today as a
question to investigate against the pinned commit above, never as a fact about `main`.
