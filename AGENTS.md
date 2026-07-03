# Coding Agent Instructions

## Product truth

Never publish an unsupported claim that a service is free. Official evidence and deterministic validation are mandatory.

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
- Z0 requires no card, purchase, or automatic billing.

## UX rules

- Simple labels by default.
- Numeric confidence only in advanced evidence.
- Explain why an architecture is or is not Z0.
- Always retain deterministic fallback.
- Accessibility is part of done.

## Work style

Implement small vertical slices, update docs/ADRs, run tests, and do not silently expand scope.
