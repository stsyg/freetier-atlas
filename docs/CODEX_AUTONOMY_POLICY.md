# Codex Autonomy Policy

This policy defines how Codex may work in FreeTier Atlas without asking for repeated approval. It complements `AGENTS.md`, `docs/AGENT_HARNESS.md`, and `CODEX_TASKS.md`.

## Approved operating principle

Codex may create branches, implement one approved feature, run tests, commit, push, and open pull requests.

Codex may not merge pull requests, alter feature acceptance criteria, mark features passing without evaluator evidence, add external services, or proceed to the next feature without approval.

## Default workflow

For one approved feature, Codex may:

1. create a feature branch from `main`
2. write or update `agent-state/current_contract.json`
3. implement only the approved feature scope
4. run required checks, smoke tests, and end-to-end tests that are available
5. commit focused builder work
6. produce or run a fresh evaluator workflow when required by the feature's evaluation level
7. update only allowed feature-status fields after evaluator approval
8. push the branch
9. open a pull request into `main`
10. stop and report results

Codex must not automatically continue to the next feature after opening the pull request.

## Allowed without asking each time

These actions are allowed inside the current approved feature scope:

- read repository files and git history
- create a new non-main feature branch
- run local tests, linters, formatters, type checks, and smoke checks
- create or update files required by the approved feature contract
- add focused tests for the approved feature
- update `agent-state/current_contract.json` for the active feature
- append builder or evaluator entries to `agent-state/progress.md`
- update `agent-state/evaluation.json` during independent evaluation
- update only `passes`, `last_verified_at`, and `verification_evidence` in `agent-state/feature_list.json` after required evaluation succeeds
- commit focused changes
- push the feature branch
- open a pull request into `main`

## Must stop for owner approval

Codex must stop and ask before:

- merging any pull request
- changing feature descriptions, priorities, or acceptance steps
- proceeding from one feature ID to another feature ID
- changing the scope of an approved contract
- adding a new runtime, framework, database, queue, or cloud provider dependency
- introducing network access from tests, scripts, or application code
- adding or changing authentication, authorization, secrets, or credential handling
- changing public Z0 classification rules or quota math
- adding provider adapters that can publish verified catalogue records
- changing generated deployment package semantics
- adding GitHub Actions or other automation that uses secrets
- force-pushing, resetting, rebasing shared history, deleting branches, or running destructive git commands
- deleting files that are not explicitly in the approved contract
- making external API calls or web requests from implementation or tests

## Forbidden unless explicitly requested in the current prompt

Codex must not:

- merge its own pull request
- mark a feature passing without independent evaluation evidence
- weaken or delete tests to pass a check
- rewrite requirements to fit the implementation
- make unsupported free-tier, Z0, quota, pricing, region, or data-residency claims
- read or print secrets, environment variables, tokens, or credential files
- install global tools without approval
- use paid cloud resources
- create accounts or configure external services
- make irreversible destructive changes

## Evaluation requirements

Level 0 documentation-only or mechanical changes may be self-checked when the contract allows it.

Level 1 changes require a fresh independent evaluator context before a feature is marked passing.

Level 2 changes require adversarial end-to-end evaluation using browser, API, database, negative, security, and regression checks where applicable.

Any required criterion failure keeps the feature failing.

## Cloud and local Codex usage

Local Codex is preferred for early repository setup because it can use the developer machine's Windows, WSL, Git Bash, PowerShell, Docker, and local editor environment.

Cloud Codex may be used for bounded tasks when:

- the task can run in the cloud sandbox without local-only tools
- required setup is documented in the repository
- no secrets are needed
- no external services or network access are required unless explicitly approved
- the task is one approved feature or one clearly bounded subtask
- Codex returns a reviewable branch or pull request rather than merging directly

Do not use Cloud Codex for tasks that require local Docker Desktop, local Windows/WSL behaviour, credentials, provider accounts, private local files, or unapproved internet access.

## Autonomy levels

### A0 — Read-only diagnosis

Codex reads, runs non-mutating checks, and reports. No file changes.

### A1 — Mechanical safe changes

Codex may modify docs, formatting, line endings, scripts, validation fixtures, and agent-state files within an approved feature. It may commit, push, and open a PR. No merge.

### A2 — Standard feature implementation

Codex may implement one approved feature, add tests, run checks, request or perform independent evaluation, push, and open a PR. No merge and no next feature.

### A3 — High-risk implementation

Codex may implement only after an explicit approved contract and must receive Level 2 independent evaluation. Applies to Z0 logic, provider adapters, auth, secrets, LLM routing, deployment generation, public hosting, and database migrations.

### A4 — Human-only decision

The owner controls PR merges, scope changes, feature acceptance rewrites, external services, credentials, paid resources, and release tags.

## Standard prompt suffix

For future approved features, prompts may end with:

> Follow `docs/CODEX_AUTONOMY_POLICY.md`. You may create a branch, implement this one approved feature, run tests, commit, push, and open a PR. You may not merge, alter acceptance criteria, mark the feature passing without evaluator evidence, add external services, or proceed to the next feature without approval.
