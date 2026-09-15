# ⚠️ Archived Level-2 evaluation probes — F001 `harden-secrets-route-test` (PR #69)

**Read this before you run anything under `probes/`.**

`probes/` holds **point-in-time probe scripts** from a specific Level-2 evaluation
of F001 (`harden-secrets-route-test`, PR #69). They are the instruments a
fresh-context evaluator used to *produce* a verdict — not general-purpose tooling
and not a regression suite. They are committed only so the verdict they produced
can be inspected and, if needed, contested against the exact tree they ran on.

The original evaluator-authored [`probes/README.md`](probes/README.md) describes what
each individual probe measures and which two are worth reusing. That file is kept
**verbatim** as a historical artefact; this file is the standing warning that must
frame it.

## What they were run against

- **Commit they were run against:** `30591feb5c7f0895856e68462746e19f57481c51`
- **Matching evaluation record:** [`F001-harden-secrets-route-test-30591feb.json`](F001-harden-secrets-route-test-30591feb.json) (disposition: passed)

Do not assume these scripts describe any later state of the repository.

## Why you must not trust them if you re-run them

- They are **not maintained**. Nobody updates them when the code they measure moves.
- They are **not run by CI**. No gate will tell you when they have gone stale.
- By design each probe **re-declares the route rules locally** rather than importing
  them from the subject, so a re-run against a newer tree measures *that tree against
  the old evaluation's assumptions*, not against today's intended behaviour.

**If a re-run here disagrees with the record, the tree moved — that is not evidence
that the original verdict was wrong.** A stale probe that returns a confident wrong
answer is worse than no probe at all. Treat any answer these produce today as a
question to investigate against the pinned commit above, never as a fact about `main`.
