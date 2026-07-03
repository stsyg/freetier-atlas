# Long-Running Agent Harness

This repository uses a model-agnostic long-running coding harness adapted from Anthropic's engineering guidance on effective harnesses and long-running application development.

The objective is not to make agents work continuously at any cost. It is to make every fresh session capable of understanding the current state quickly, selecting a tractable next increment, verifying it rigorously, and leaving the repository in a clean state for the next session.

## Core principles

1. **Persistent state beats conversational memory.** Git history and structured repository artifacts are the source of truth between sessions.
2. **Work incrementally.** Default to one feature or one tightly coupled atomic change per session. Do not attempt to one-shot an epic or the entire MVP.
3. **Define done before coding.** Every implementation increment begins with a testable contract.
4. **Separate building from judging.** Important changes are evaluated by a fresh, skeptical evaluator context rather than accepted solely through builder self-review.
5. **Test as a user.** Unit tests and API probes are necessary but insufficient for user-facing functionality. Use browser automation and real workflows.
6. **Leave a clean handoff.** A session ends with working code, tests, a focused commit, and an updated progress artifact.
7. **Use the simplest harness that works.** Harness components encode assumptions about model limitations. Re-evaluate them when models or tools improve.

## Agent roles

### Initializer / planner

The initializer runs once at project foundation and again only when an approved scope expansion requires re-planning.

Responsibilities:

- Read `PLAN.md`, `docs/PRODUCT_REQUIREMENTS.md`, `docs/MVP_ACCEPTANCE.md`, and accepted ADRs.
- Expand requirements into `.agent/feature_list.json` as end-to-end, independently verifiable behaviours.
- Mark every new feature `passes: false`.
- Create or update `scripts/init.sh`, `scripts/init.ps1`, smoke tests, and agent-state templates.
- Keep the plan focused on deliverables and product behaviour. Avoid prematurely fixing low-level implementation details that later agents should decide from evidence.
- Create an initial git commit establishing a known baseline.

The initializer must not declare the product complete and must not mark features as passing.

### Builder / generator

The builder implements one selected feature or tightly coupled atomic unit.

Responsibilities:

- Follow the session startup protocol.
- Select the highest-priority failing feature that is unblocked.
- Propose a contract in `.agent/current_contract.json` before coding.
- Implement only the agreed scope.
- Add or update tests without weakening existing tests.
- Self-review and run all relevant automated checks.
- Hand work to the evaluator when independent evaluation is required.
- End with a descriptive commit and progress update.

### Evaluator / QA

The evaluator operates in a fresh context or distinct agent persona and assumes the builder may have missed important defects.

Responsibilities:

- Read the feature contract and acceptance steps before inspecting the implementation.
- Start the application through the documented initialization path.
- Exercise the feature end to end as a real user.
- Inspect API responses and database state where relevant.
- Probe edge cases, failure paths, security boundaries, and regressions.
- Grade every required criterion against a hard pass threshold.
- Record specific evidence and actionable failures in `.agent/evaluation.json`.
- Never talk itself into approving a failed criterion because the implementation is otherwise impressive.

The evaluator does not rewrite the acceptance criteria to fit the implementation.

## Persistent artifacts

### `.agent/feature_list.json`

Canonical list of end-to-end behaviours. Feature descriptions and acceptance steps are controlled specification data.

Normal coding agents may modify only:

- `passes`
- `last_verified_at`
- `verification_evidence`

Changing descriptions, priorities, or acceptance steps requires an explicit planning/specification change in a separate commit approved by the owner.

A feature may be marked passing only after all acceptance steps succeed and required independent evaluation has passed.

### `.agent/progress.md`

Append-only human-readable handoff log. Each session records:

- feature ID and objective
- work completed
- tests run and exact results
- evaluator result
- commit SHA
- unresolved issues
- recommended next action

Do not use this file as a substitute for git history or feature status.

### `.agent/current_contract.json`

Defines the active increment before implementation:

- feature ID
- scope
- explicit out-of-scope items
- acceptance criteria
- verification plan
- risks
- required evaluator level

The builder proposes the contract. For high-risk work, the evaluator reviews the contract before implementation.

### `.agent/evaluation.json`

Stores the latest independent evaluation result, criterion scores, failures, evidence, and disposition.

### Git history

Commits must be small enough to understand and revert. A future agent should be able to reconstruct recent work using `git log`, the diff, and progress entries without guessing.

### Initialization scripts

- `scripts/init.sh`
- `scripts/init.ps1`

These scripts provide the canonical path to start dependencies, run migrations, start services, and execute a basic smoke test. As the application grows, update both scripts together.

## Session startup protocol

Every coding or evaluation session must:

1. Confirm location with `pwd` or `Get-Location`.
2. Run `git status --short --branch`.
3. Read `git log --oneline -20`.
4. Read `AGENTS.md`.
5. Read `.agent/progress.md` and `.agent/feature_list.json`.
6. Read the active task, relevant requirements, ADRs, and provider documentation.
7. Run `scripts/init.sh` or `scripts/init.ps1`.
8. Run the baseline smoke test and at least one core end-to-end workflow.
9. Fix an existing broken baseline before starting new feature work.
10. Select exactly one unblocked feature unless the contract explicitly justifies a tightly coupled atomic group.

Compaction or a prior conversation summary does not replace this protocol.

## Contract-before-code protocol

Before changing implementation files, write `.agent/current_contract.json` with:

- the selected feature ID
- user-visible outcome
- exact acceptance criteria
- verification commands and end-to-end steps
- migration/data implications
- security and privacy considerations
- portability and Z0 implications
- out-of-scope items
- evaluator requirement

A contract must be specific enough that another agent can determine pass or fail without interpreting the builder's intent.

## Evaluation levels

### Level 0 — Mechanical review

For typo-only or documentation-only changes. Requires lint/link checks and diff review. Independent evaluator optional.

### Level 1 — Standard independent review

Required for normal code changes. A fresh evaluator reviews tests, implementation, regressions, and contract evidence.

### Level 2 — End-to-end adversarial review

Mandatory for:

- public catalogue behaviour
- provider adapters and evidence extraction
- Z0 classification or cost calculations
- database schemas and migrations
- authentication, authorization, privacy, or rate limiting
- LLM routing and tool boundaries
- generated Docker Compose or deployment packages
- public hosting and quota-proof functionality
- any change that can publish a free-tier claim automatically

Level 2 uses browser automation where a UI exists, API/database inspection, negative tests, and explicit edge-case probing.

## FreeTier Atlas evaluation rubric

Every independently evaluated feature is graded on applicable criteria. Each required criterion is pass/fail; there is no compensating average.

1. **Functional correctness** — all contract behaviours work end to end.
2. **Product depth** — no core interaction is a display-only stub or fake success path.
3. **Data and evidence integrity** — claims, quotas, provenance, versions, and confidence are accurate and reproducible.
4. **Zero-cost safety** — no Z1/Z2/Z3 component is mislabeled as Z0; exhaustion behaviour and paid dependencies are explicit.
5. **Security and privacy** — boundaries, secrets, input handling, authorization, retention, and abuse controls behave as designed.
6. **Portability** — the change does not introduce an undocumented mandatory provider dependency.
7. **Code quality** — implementation is maintainable, typed where appropriate, tested, and consistent with architecture.
8. **User experience and accessibility** — workflows are understandable, usable, responsive, and accessible.
9. **Regression safety** — existing passing features and baseline workflows remain passing.

Any required criterion failure rejects the increment.

## End-of-session protocol

Before stopping, the builder must:

1. Run all contract tests and relevant regression tests.
2. Run the canonical smoke/end-to-end workflow.
3. Ensure `git status` contains no accidental or unexplained files.
4. Obtain required evaluator disposition.
5. Mark the feature passing only after evaluation succeeds.
6. Append a structured entry to `.agent/progress.md`.
7. Create a focused descriptive commit.
8. Record the commit SHA and recommended next feature.
9. Leave the application in a state appropriate for merging to `main`.

Half-implemented features must not be committed as successful work. When unavoidable, keep them isolated on a branch, mark the feature failing, document the exact state, and ensure the baseline remains runnable.

## Context resets and handoffs

Use a fresh session when:

- one feature is complete
- the context has become dominated by old investigation
- the agent begins rushing toward premature completion
- the next task is materially different
- independent evaluation is required

The handoff artifacts must be sufficient for a new agent to resume without relying on hidden conversational context.

## Harness calibration and simplification

Do not assume every task permanently needs the maximum three-agent workflow.

- Start with the lightest evaluation level appropriate to the risk.
- Record failures the harness catches and failures it misses.
- Tune evaluator prompts and criteria using real examples.
- When a new model or tool is adopted, rerun representative tasks and tests.
- Remove one harness component at a time only after evidence shows it is no longer load-bearing.
- Add complexity only to address observed failure modes.

## Source material

- Anthropic, “Effective harnesses for long-running agents,” November 26, 2025.
- Anthropic, “Harness design for long-running application development,” March 24, 2026.
