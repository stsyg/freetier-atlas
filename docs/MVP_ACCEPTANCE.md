# MVP Acceptance Checklist

## Mandatory functionality

- [ ] Seven providers configured.
- [ ] All fourteen categories investigated for each provider.
- [ ] At least 75–100 verified records.
- [ ] Every published record links to official evidence.
- [ ] Scheduled scans detect and classify changes.
- [ ] High-confidence changes publish automatically.
- [ ] Contradictions enter admin review.
- [ ] Search, filter, sort, and comparison work.
- [ ] Natural language converts to editable structured requirements.
- [ ] Adviser produces a credible Z0 architecture or explains why none exists.
- [ ] Adviser suggests reductions before self-hosting.
- [ ] Browser ZIP includes Docker Compose and deployment documentation.
- [ ] RSS works.
- [ ] Discord works.
- [ ] GitHub-authenticated admin works.
- [ ] Complete application runs with Docker Compose.
- [ ] Public multi-provider Z0 instance operates.
- [ ] Public deployment displays quota use, headroom, and $0 status.
- [ ] Local, free hosted, and commercial LLM routes are configurable.
- [ ] Deterministic adviser works with all LLM routes disabled.

## Data quality

- [ ] Every quota has amount, unit, period, scope, and exhaustion behaviour.
- [ ] Card requirement and paid dependencies are explicit.
- [ ] Commercial/personal-use conditions are explicit.
- [ ] Region availability and residency are separate.
- [ ] Evidence and hashes are retained.
- [ ] Versions and changes are permanent.
- [ ] Raw snapshot cleanup after 90 days is tested.
- [ ] Completeness/freshness scores are reproducible.
- [ ] Stale and withdrawn offers are visible correctly.

## Security and portability

- [ ] Public AI is not a general chatbot.
- [ ] CAPTCHA and rate limits are tested.
- [ ] External-model consent is explicit.
- [ ] Project descriptions are not stored or logged.
- [ ] Public endpoints cannot access credentials, shell, filesystem, or admin tools.
- [ ] Generated ZIP is path-safe and secret-free.
- [ ] Admin uses GitHub allowlist.
- [ ] amd64 and arm64 images build.
- [ ] Clean Docker Compose startup passes.
- [ ] No cloud provider is mandatory for local operation.
