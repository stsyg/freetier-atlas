# Contributing

FreeTier Atlas accepts focused pull requests that preserve evidence-backed
claims and zero-cost safety.

## Ground Rules

- Work on one feature or tightly coupled atomic change at a time.
- Do not claim a service is free unless official evidence and deterministic
  validation support the claim.
- Community lists may create unverified candidates only.
- Do not add secrets, credentials, tokens, private URLs, or full environment
  dumps to source, logs, tests, examples, or generated artifacts.
- Keep provider-specific logic behind adapters once provider work begins.
- Do not add mandatory managed-cloud dependencies.

## Local Checks

Run the canonical repository checks before opening a pull request:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/test.ps1
```

```bash
./scripts/test.sh
```

The F001 foundation checks validate repository metadata, JSON syntax,
structural YAML syntax, line endings, secret patterns, dependency policy,
Node metadata, and Python unit tests. Full typed YAML validation begins in
F003.

## Pull Requests

- Use a short-lived branch from `main`.
- Keep commits focused and descriptive.
- Include the feature ID in the PR title when the change implements a
  tracked feature.
- Include exact commands and results in the PR description.
- Never merge your own PR when acting as an autonomous coding agent.

## Commit Style

Use conventional prefixes where they fit:

- `feat:` for user-visible or repository capability
- `fix:` for bug fixes
- `test:` for verification changes
- `docs:` for documentation
- `chore:` for maintenance

## Evidence Policy

Provider claims must cite official sources before publication. Unknown,
conflicting, temporary, billing-exposed, or unsupported material conditions
must not be classified as Z0.
