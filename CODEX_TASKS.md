# Codex Build Tasks

This task list is executed through the long-running agent harness in `docs/AGENT_HARNESS.md`.

## Rules for every task

1. Read `AGENTS.md`, `agent-state/feature_list.json`, `agent-state/progress.md`, and recent git history.
2. Start the repository through `scripts/init.sh` or `scripts/init.ps1` when present and verify the baseline before new work.
3. Select one highest-priority unblocked failing feature, not an entire epic.
4. Write `agent-state/current_contract.json` with scope, out-of-scope items, acceptance criteria, verification steps, risks, and evaluation level.
5. Implement the smallest coherent increment that satisfies the contract.
6. Run automated tests and real end-to-end workflows.
7. Use a fresh independent evaluator for Level 1 and Level 2 work.
8. Mark `passes: true` only after all acceptance steps and evaluator gates succeed.
9. Append a handoff to `agent-state/progress.md` and create a focused descriptive commit.
10. Never delete or weaken tests, feature descriptions, or acceptance steps to manufacture a pass.

Each task below is an epic. The builder implements its constituent feature records one at a time.

## 000 Agent harness foundation

**Outcome:** Future agents can resume safely from a fresh context without relying on conversation memory.

- Maintain `agent-state/feature_list.json` as the canonical feature ledger.
- Maintain `agent-state/progress.md`, `agent-state/current_contract.json`, and `agent-state/evaluation.json`.
- Implement and maintain `scripts/init.sh` and `scripts/init.ps1`.
- Add baseline smoke-test commands.
- Document planner, builder, evaluator, contract, and handoff protocols.

**Evaluation:** Level 1. A fresh agent must be able to follow the files and identify the next unblocked feature without extra explanation.

## 001 Repository foundation

**Outcome:** A clean, reproducible public monorepo baseline.

- Add AGPL-3.0, attribution, trademark, authors, and third-party notice files.
- Add Python and Node tooling.
- Add formatting, linting, pre-commit, dependency and secret scanning.
- Add CI and protected-main guidance.
- Establish focused PR and commit conventions.

**Evaluation:** Level 1. Clone into a clean environment and run all repository checks.

## 002 Application scaffold

**Outcome:** The complete development stack starts through one canonical command.

Before or inside the first F002 implementation slice, establish repository-owned development-environment documentation and commands so Codex environments stay thin and the repository remains the source of truth:

- `docs/LOCAL_DEVELOPMENT.md`
- `docs/CODEX_ENVIRONMENT.md`
- `scripts/check-env.ps1`
- `scripts/check-env.sh`
- `scripts/bootstrap-dev.ps1`
- `scripts/bootstrap-dev.sh`
- `scripts/test.ps1`
- `scripts/test.sh`

The environment scripts must work safely in both the current planning-only state and the scaffolded state. They must fail actionably when required runtimes are missing, avoid printing secrets or full environment dumps, avoid unapproved network calls, and delegate to the canonical init/smoke/test workflow rather than duplicating hard-coded Codex UI setup.

Then implement the application scaffold one bounded slice at a time:

- FastAPI API
- Python worker and scheduler
- Static-capable React frontend
- PostgreSQL
- Docker Compose
- Health endpoints
- Migrations and baseline smoke workflow

**Evaluation:** Level 2. Start from a clean checkout, verify environment checks, bootstrap, tests, health, API, frontend, database connectivity, shutdown, and restart.

## 003 Configuration system

**Outcome:** Declarative YAML configuration is typed, validated, documented, and safely overridden by environment secrets.

- Pydantic settings
- YAML loader
- JSON Schema export
- Environment-secret overrides
- Example configurations
- Validation command
- Invalid and unknown-field tests

**Evaluation:** Level 1, elevated to Level 2 for security-sensitive secret handling.

## 004 Domain model

**Outcome:** Core catalogue state is represented with immutable history and reproducible evidence.

- Provider, Service, Category
- Offer and Quota
- Region availability and residency
- Source, Evidence, Snapshot
- OfferVersion and ChangeEvent
- ScanRun and ReviewItem
- Alembic migrations and rollback tests

**Evaluation:** Level 2. Verify migration, constraints, immutable version behaviour, rollback, and representative queries.

## 005 Z0 classification engine

**Outcome:** Every offer receives an explainable Z0/Z1/Z2/Z3/unknown result without unsafe inference.

- Card, purchase, paid-dependency, and automatic-billing gates
- Exhaustion behaviour rules
- Temporary/conditional classification
- Self-hosted classification
- Explanation output
- Comprehensive truth-table tests

**Evaluation:** Level 2. A skeptical evaluator probes missing, contradictory, and boundary data; no unknown material condition may yield Z0.

## 006 Source-adapter SDK

**Outcome:** Provider sources can be fetched, normalized, evidenced, and health-checked safely behind a stable interface.

- Common adapter interface
- Safe HTTP fetcher
- RSS/Atom adapter
- Static HTML adapter
- Browser-rendered adapter
- MCP client wrapper
- Official-domain allowlists and SSRF controls
- Hashing, compression, fixtures, and failure handling

**Evaluation:** Level 2. Include malicious URL, timeout, oversized response, malformed data, no-change, changed, and contradictory fixtures.

## 007 Cloudflare vertical slice

**Outcome:** One provider works end to end from official discovery to public catalogue and review queue.

- Provider YAML and official source registry
- Candidate extraction and evidence
- Verification and publication gates
- Offer/version persistence
- Public API and provider page
- Completeness/freshness score
- Conflict review flow

**Evaluation:** Level 2 with browser, API, database, and source-evidence testing. No community source may establish a verified claim.

## 008 Catalogue experience

**Outcome:** Users can discover, understand, and compare verified offers efficiently.

- Hybrid adviser/catalogue homepage
- Provider and category cards
- Search, filtering, sorting, and comparison
- Evidence and confidence panels
- Change history and recently detected stream
- Responsive and accessible interaction

**Evaluation:** Level 2 with Playwright. Grade functionality, product depth, visual coherence, usability, accessibility, and regression safety separately; every required criterion must pass.

## 009 RSS and Discord

**Outcome:** Users can receive accurate, deduplicated catalogue-change notifications.

- Global, verified, new, withdrawn, provider, and category feeds
- Stable identifiers and timestamps
- Discord webhook formatting
- Retry, deduplication, and failure handling

**Evaluation:** Level 1, plus end-to-end delivery test for Discord configuration.

## 010 Deterministic adviser

**Outcome:** Architecture recommendations remain useful with every LLM disabled.

- Structured requirements schema and guided form
- Catalogue matching and score explanations
- Quota arithmetic
- Requirement-reduction suggestions
- Self-hosted fallback
- Portable and simplest-free alternatives
- Evidence references and exit plan

**Evaluation:** Level 2 using the adviser evaluation corpus. Reject hidden Z1 components, insufficient quota math, unsupported assumptions, or unexplainable scores.

## 011 LLM routing

**Outcome:** Local, free hosted, and commercial models improve extraction or explanation without becoming required or unsafe.

- Ollama adapter
- Free-hosted adapters
- Commercial adapters
- Explicit external-processing consent
- Prompt minimization and no-secret boundaries
- Per-provider budgets, rate limits, and circuit breakers
- Deterministic fallback

**Evaluation:** Level 2. Test provider exhaustion, timeout, malformed output, consent denial, attempted prompt abuse, and complete LLM outage.

## 012 Browser-side deployment ZIP

**Outcome:** A user downloads a validated, secret-free project package without server-side persistence.

- Fixed application-controlled templates and paths
- Docker Compose validation
- `.env.example` placeholders
- Architecture, deployment, quota, and portability docs
- Generation manifest and catalogue version
- Browser-side ZIP creation and size limits

**Evaluation:** Level 2. Test path traversal attempts, secrets, malformed templates, deterministic output, download workflow, and clean Compose startup.

## 013 Private administration

**Outcome:** The owner can operate the catalogue without creating public account-management scope.

- GitHub OAuth
- Explicit `stsyg` allowlist
- Source-health and scan dashboards
- Evidence/conflict review
- YAML diff and validation
- LLM usage controls
- Audit trail

**Evaluation:** Level 2. Test unauthorized, non-allowlisted, CSRF, stale-session, validation-failure, and audit scenarios.

## 014 Remaining MVP providers

**Outcome:** GitHub, AWS, Google Cloud, Azure, Vercel, and Oracle reach all-category investigated status.

Implement one provider and one independently verifiable feature group at a time. Each provider needs official sources, fixtures, category coverage states, evidence rules, health checks, and documentation.

**AWS constraint:** Official AWS Free Tier APIs/docs/MCP are authoritative; CostGoat is a secondary regression checklist only.

**Evaluation:** Level 2 per provider before its records can auto-publish.

## 015 Public Z0 reference deployment

**Outcome:** A documented multi-provider deployment operates at $0 and publicly proves its status.

- Cloudflare Pages candidate frontend and GitHub Pages mirror
- Verified Z0 dynamic services
- Usage, allowance, headroom, and estimated-bill reporting
- Reproducible manifests
- Monthly Z0 re-verification
- Failure/degradation documentation

**Evaluation:** Level 2 using real onboarding and quota-exhaustion evidence. Do not award Z0 from pricing prose alone.

## 016 MVP acceptance and release

**Outcome:** `v0.1.0` satisfies the entire acceptance checklist without feature-count theatre.

- Re-run every passing feature's acceptance steps
- Run full adviser evaluation corpus
- Run security, portability, accessibility, and multi-architecture checks
- Conduct independent data-quality review
- Verify public Z0 deployment and documentation
- Resolve or explicitly defer every failure

**Evaluation:** Independent release evaluator. No mandatory checklist item may be waived by the implementation agent.
