# ADR 0006: Long-running agent harness

Status: Accepted

## Context

FreeTier Atlas cannot be implemented reliably in one model context. Agents can otherwise over-scope, lose state, approve incomplete work, or leave undocumented partial changes.

## Decision

Use a model-agnostic planner, builder, and evaluator workflow with:

- `agent-state/feature_list.json` as the canonical feature ledger
- `agent-state/progress.md` as the append-only handoff log
- `agent-state/current_contract.json` as the contract-before-code artifact
- `agent-state/evaluation.json` as the independent QA record
- git history as a required context and rollback mechanism
- one feature or tightly coupled atomic unit per implementation session
- independent evaluation based on risk
- end-to-end browser, API, and database testing for high-risk work
- features marked passing only after all acceptance steps and required evaluation succeed
- periodic simplification reviews so harness complexity does not become stale

## Consequences

This adds process and evaluator cost, but gives fresh agents reliable context, keeps changes reviewable and revertible, and reduces premature completion and self-approval failures.

Use the lightest evaluation level appropriate to the risk. Remove harness components only after realistic tests show they are no longer load-bearing.

## References

- Anthropic: Effective harnesses for long-running agents
- Anthropic: Harness design for long-running application development
