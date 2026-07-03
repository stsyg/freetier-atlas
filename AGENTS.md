# Coding Agent Instructions

These instructions apply to Codex, Claude Code, GitHub Copilot coding agents, and any other autonomous or semi-autonomous agent working in this repository.

Read `docs/AGENT_HARNESS.md` before implementation work. Read `docs/CODEX_AUTONOMY_POLICY.md` before choosing an autonomy level or taking actions beyond read-only diagnosis.

## Product truth

Never publish an unsupported claim that a service is free. Official evidence and deterministic validation are mandatory.

## Required session startup

Before changing code:

1. Confirm the repository directory.
2. Run `git status --short --branch`.
3. Read `git log --oneline -20`.
4. Read this file, `agent-state/progress.md`, and `agent-state/feature_list.json`.
5. Read the active requirements, task, and relevant ADRs.
6. Run `scripts/init.sh` or `scripts/init.ps1` when present. Until the initializer feature creates them, document that bootstrap is incomplete.
7. Run the baseline smoke test and a core end-to-end workflow when the application scaffold exists.
8. Fix a broken baseline before starting new work.
9. Select one highest-priority unblocked failing feature.
10. Write `agent-state/current_contract.json` before implementation.

Do not rely on prior chat context or compaction as project memory.

## Incremental work

- Work on one feature or one tightly coupled atomic unit at a time.
- Do not one-shot an epic, provider, or the entire MVP.
- Define measurable acceptance criteria before coding.
- Keep unrelated refactoring out of the increment.
- Never silently reduce scope or declare incomplete functionality done.
- Display-only stubs, fake success paths, and TODO-backed core behaviours fail evaluation.

## Autonomy boundaries

Codex may create branches, implement one approved feature, run tests, commit, push, and open pull requests as defined in `docs/CODEX_AUTONOMY_POLICY.md`.

Codex may not merge pull requests, alter feature acceptance criteria, mark features passing without evaluator evidence, add external services, or proceed to the next feature without approval.

## Structured state

- `agent-state/feature_list.json` is the canonical end-to-end feature ledger.
- Normal coding agents may change only `passes`, `last_verified_at`, and `verification_evidence` for an existing feature.
- Do not delete, weaken, or rewrite acceptance steps to make implementation pass.
- `agent-state/progress.md` is append-only and must be updated at the end of every meaningful session.
- `agent-state/current_contract.json` defines the active increment.
- `agent-state/evaluation.json` records independent QA results.
- Git history is part of the handoff mechanism; commits must be focused and descriptive.

## Independent evaluation

Builder self-review is necessary but not sufficient for material functionality.

A fresh evaluator context is mandatory for:

- public UI and user workflows
- provider adapters and evidence extraction
- Z0 classification and quota calculations
- schemas and migrations
- authentication, privacy, rate limiting, and security boundaries
- LLM routing and tool permissions
- generated deployment files
- public hosting and cost-proof functionality
- automatic publication decisions

The evaluator must test against the contract, use browser automation for UI workflows, inspect API/database state where relevant, probe edge cases, and reject the increment when any required criterion fails.

## Engineering rules

- Preserve portability.
- Hide provider-specific logic behind adapters.
- Do not add a mandatory managed-cloud dependency.
- PostgreSQL is the only mandatory stateful service in the MVP.
- Do not add Redis without evidence.
- Use typed models and migrations.
- Validate all YAML.
- Never place secrets in source, logs, tests, examples, or ZIPs.
- No user-controlled URLs in public endpoints.
- No direct LLM-to-publication path.
- Every material change needs tests.
- All containers require health checks.
- Preserve amd64 and arm64 compatibility.

## Data rules

- Community lists create unverified candidates only.
- Official sources create evidence.
- Unknown is better than guessed.
- Offer versions are immutable.
- Region availability and residency are separate.
- Z0 requires no card, purchase, paid dependency, or automatic billing.
- A failed or unknown material Z0 condition prevents Z0 classification.

## UX rules

- Simple labels by default.
- Numeric confidence only in advanced evidence.
- Explain why an architecture is or is not Z0.
- Always retain deterministic fallback.
- Accessibility is part of done.
- Core workflows must be tested as a human user would perform them.

## End-of-session requirements

Before stopping:

1. Run contract tests and relevant regression tests.
2. Run the canonical smoke/end-to-end workflow when available.
3. Obtain the required evaluator disposition.
4. Mark a feature passing only after evidence-backed evaluation succeeds.
5. Append the handoff to `agent-state/progress.md`.
6. Create a focused descriptive commit.
7. Leave the repository clean and runnable.

A half-implemented feature remains failing. Do not leave undocumented broken state for the next session.

## Harness complexity

Use the simplest harness that reliably passes the required quality bar. Reassess planner/evaluator scaffolding when models improve, but remove components only one at a time and only after representative evidence shows they are no longer load-bearing.
