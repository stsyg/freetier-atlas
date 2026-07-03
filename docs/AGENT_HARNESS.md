# Long-Running Agent Harness

FreeTier Atlas uses a model-agnostic coding harness adapted from Anthropic's guidance on long-running agents and application development.

The goal is not uninterrupted autonomy. The goal is that every fresh session can understand repository state quickly, complete one tractable increment, verify it rigorously, and leave a clean handoff.

## Core principles

1. **Persistent state beats conversational memory.** Git history and repository artifacts carry context between sessions.
2. **Work incrementally.** Default to one feature or one tightly coupled atomic change per session.
3. **Define done before coding.** Every implementation increment begins with a testable contract.
4. **Separate building from judging.** Material work is evaluated by a fresh, skeptical evaluator context.
5. **Test as a user.** Unit tests and API probes do not replace end-to-end workflows.
6. **Leave a clean handoff.** End with runnable code, tests, a focused commit, and progress notes.
7. **Keep the harness as simple as evidence allows.** Remove scaffolding only when realistic evaluations show it is no longer load-bearing.

## Roles

### Initializer / planner

The initializer runs at project foundation and after explicitly approved scope expansion.

It must:

- read product requirements, acceptance criteria, decisions, and ADRs
- expand epics into end-to-end feature records in `agent-state/feature_list.json`
- mark every new feature `passes: false`
- create or maintain initialization and smoke-test scripts
- focus the specification on outcomes and constraints rather than guessing premature implementation details
- establish a known git baseline

The initializer never declares the product complete and never marks new features passing.

### Builder / generator

The builder:

- follows the startup protocol
- selects one highest-priority unblocked failing feature
- writes `agent-state/current_contract.json` before implementation
- implements only the agreed scope
- adds tests without weakening existing coverage
- self-reviews and runs relevant checks
- requests independent evaluation at the required level
- records a focused commit and progress handoff

### Evaluator / QA

The evaluator uses a fresh context or distinct agent persona and assumes the builder may have missed defects.

It must:

- read the feature record and contract before reviewing code
- start the application through the canonical path
- exercise the feature as a real user
- inspect API and database state where relevant
- probe edge cases, failure paths, and regressions
- grade every required criterion with a hard pass threshold
- write evidence and failures to `agent-state/evaluation.json`
- reject any increment with a failed required criterion

The evaluator never rewrites acceptance criteria to fit the implementation and never excuses a failed core criterion because the rest of the work looks impressive.

## Persistent artifacts

### `agent-state/feature_list.json`

Canonical feature ledger.

Normal coding agents may modify only:

- `passes`
- `last_verified_at`
- `verification_evidence`

Changing feature descriptions, priorities, or acceptance steps requires an explicit specification change approved by the owner.

A feature becomes passing only when all acceptance steps succeed and the required evaluation passes.

### `agent-state/progress.md`

Append-only handoff log recording:

- feature and objective
- work completed
- files changed
- tests and exact results
- evaluator disposition
- commit SHA
- known issues
- recommended next action

### `agent-state/current_contract.json`

Defines the active increment before code changes:

- feature ID and user-visible outcome
- scope and explicit out-of-scope items
- acceptance criteria
- verification commands and end-to-end steps
- migration and data implications
- security/privacy, portability, Z0, and regression risks
- required evaluation level

For high-risk work, the evaluator reviews the contract before implementation.

### `agent-state/evaluation.json`

Stores the independent evaluator's applicable criteria, evidence, failures, commands, end-to-end steps, and disposition.

### Git history

Commits must be focused, understandable, and revertible. Future agents must be able to reconstruct recent work from git plus the progress log without guessing.

### Initialization scripts

Task 000 must create and maintain:

- `scripts/init.sh`
- `scripts/init.ps1`
- a canonical smoke-test command or script

The scripts must start dependencies, apply migrations, start services, and run a basic workflow once the application scaffold exists.

## Session startup protocol

Every builder or evaluator session must:

1. confirm the working directory
2. run `git status --short --branch`
3. read `git log --oneline -20`
4. read `AGENTS.md`
5. read `agent-state/progress.md` and `agent-state/feature_list.json`
6. read the active task, requirements, and relevant ADRs
7. run the canonical initialization script when present
8. run the baseline smoke test and a core end-to-end workflow when available
9. repair an existing broken baseline before new work
10. choose exactly one unblocked feature unless the contract justifies a tightly coupled atomic group

Compaction or conversation history does not replace this protocol.

## Contract-before-code protocol

Before changing implementation files, complete `agent-state/current_contract.json` so another agent can objectively decide pass or fail.

The contract must include:

- exact user-visible outcome
- measurable acceptance criteria
- verification commands and end-to-end steps
- negative and regression tests
- explicit out-of-scope items
- data, security, privacy, portability, and Z0 implications
- evaluation level

## Evaluation levels

### Level 0 — Mechanical review

For typo-only or documentation-only changes. Requires diff, lint, and link checks. Independent evaluation is optional.

### Level 1 — Standard independent review

For normal implementation changes. A fresh evaluator reviews contract compliance, tests, code, and regressions.

### Level 2 — End-to-end adversarial review

Mandatory for:

- public catalogue and adviser workflows
- provider adapters and evidence extraction
- Z0 classification, quota, or cost calculations
- schemas and migrations
- authentication, privacy, rate limits, and security boundaries
- LLM routing and tool permissions
- generated Compose or deployment packages
- public hosting and quota proof
- automatic publication decisions

Level 2 uses browser automation where a UI exists, API/database inspection, negative tests, and explicit edge-case probing.

## FreeTier Atlas evaluation rubric

Each applicable required criterion is pass/fail. There is no compensating average.

1. **Functional correctness** — contract behaviours work end to end.
2. **Product depth** — no core interaction is a display-only stub or fake success path.
3. **Data and evidence integrity** — claims, quotas, provenance, history, and confidence are reproducible.
4. **Zero-cost safety** — no Z1, Z2, Z3, or unknown material condition is mislabeled as Z0.
5. **Security and privacy** — boundaries, secrets, input handling, authorization, retention, and abuse controls work as designed.
6. **Portability** — no undocumented mandatory provider dependency is introduced.
7. **Code quality** — implementation is maintainable, typed where appropriate, tested, and architecture-consistent.
8. **User experience and accessibility** — workflows are understandable, usable, responsive, and accessible.
9. **Regression safety** — existing passing features and baseline workflows remain passing.

Any failed required criterion rejects the increment.

## End-of-session protocol

Before stopping, the builder must:

1. run contract and regression tests
2. run the canonical smoke/end-to-end workflow when available
3. ensure no accidental or unexplained files remain
4. obtain the required evaluator disposition
5. mark the feature passing only after evaluation succeeds
6. append a structured progress entry
7. create a focused descriptive commit
8. record the commit SHA and recommended next feature
9. leave the repository in merge-quality, runnable state

Half-implemented work remains failing. When partial work cannot be avoided, keep it isolated on a branch, document the exact state, and preserve a runnable baseline.

## Context resets and handoffs

Use a fresh session when:

- one feature is complete
- old investigation dominates the current context
- the agent starts rushing toward premature completion
- the next task is materially different
- independent evaluation begins

The repository artifacts must be enough to resume without hidden conversational context.

## Calibration and simplification

- use the lightest evaluation level appropriate to risk
- record failures caught and failures missed
- tune evaluator criteria from realistic examples
- re-run representative evaluations when models or tools change
- remove one harness component at a time only after evidence shows it is unnecessary
- add complexity only to address observed failure modes

## Source material

- https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- https://www.anthropic.com/engineering/harness-design-long-running-apps
