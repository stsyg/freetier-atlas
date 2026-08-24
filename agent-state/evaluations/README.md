# Level-2 evaluation records

One file per evaluation. **Do not consolidate these into a single array.**

## What lives here

Each file is the complete record of one fresh-context Level-2 evaluation:
what was evaluated, at which commit, the disposition, the acceptance steps
re-derived, findings the builder did not disclose, and the errors the evaluator
made itself.

Filenames are `<feature_id>-<increment-slug>-<evaluated_commit_short>.json`.

## Why one file per evaluation

`agent-state/evaluation.json` is a single JSON array. Every evaluator appending
to it edits the same closing bracket, so **any two concurrent evaluators
conflict by construction**. On 2026-08-14 three ran at once. That file is
retained as the historical record for evaluations up to and including
`F008 p2-vercel-provider-rebuild`; new records go here instead.

A second defect made the problem worse: one evaluator appended with Python
`write_text`, which performed newline translation, silently rewrote **all 495
line endings to CRLF** and corrupted a character in a pre-existing entry. It
caught this by comparing the parsed pre-existing entries before and after, and
restored via `git checkout --`. Append on **raw bytes** with a refuse-to-write
guard; never rewrite a shared ledger wholesale.

## Why these records must reach `main` at all

Evaluators are read-only with respect to the branch under test and do not open
pull requests, so their records were landing only on throwaway evaluator
branches. Seven records — including **two FAILED verdicts** — were stranded that
way, while the slices they authorised were merged.

The sharpest statement of the problem came from the AWS evaluator:

> PR #70 merged on the strength of a Level-2 PASS **whose record is not in the
> repository it certifies**. The artefact and the decision it authorised have
> diverged.

That is an integrity problem rather than records hygiene. The requirement it
implies, and the one this directory has to satisfy:

> A reader arriving at a merged slice must be able to distinguish *"evaluated
> and passed"* from *"never evaluated"*, **without knowing a branch name.**

Records are therefore landed by the orchestrator after merge, keyed by the
evaluated commit, which is reachable from the merge itself.

## The FAILED records are not noise — keep them

Two of the seven are `failed`, each paired with the `passed` re-check of the
corrected head. They are the most valuable records here, because they show what
the process actually caught:

- **`F008-...-azure-...-59916656`** — a test named
  `test_the_safest_azure_offer_fails_only_on_the_card_gate` that checked
  `all(...unknown...)` and `any(...card...)` but **never that the card was the
  only blocker**. It passed while its own name was false, and was *provably
  incapable* of detecting the thing it claimed: the probe showed it stayed green
  both when the blocking set was shrunk to the world its name described and when
  a spurious blocker was added.
- **`F008-p6-oracle-provider-85ef245d`** — a false universal shipped in
  documentation. The repository's own data refuted it: 14 perpetual offers
  across 7 provider configurations classify 5 `Z0_TRUE_FREE`, 5
  `Z1_BILLING_EXPOSURE`, 4 `UNKNOWN`. It could only be found by executing across
  **all** provider modules, because the refuting evidence lived entirely outside
  the slice under review.

Deleting a `failed` record would leave a history in which evaluation never
caught anything, which is precisely the impression an evaluation record exists
to prevent.
