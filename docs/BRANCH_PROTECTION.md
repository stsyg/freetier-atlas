# Protected Main Guidance

`main` should be protected before material product work begins.

Recommended branch protection settings:

- Require pull requests before merging.
- Require at least one approving review.
- Require the `repository-foundation` CI job to pass.
- Require conversations to be resolved.
- Require branches to be up to date before merge once CI becomes stable.
- Block force pushes and branch deletion.
- Require signed or verified commits only if the owner decides the added
  contributor friction is worthwhile.

Autonomous agents may open pull requests but may not merge them.

## Controlled-Failure Verification

For F001, reviewers can verify that local checks and CI reject a controlled
failure by adding a temporary ignored fixture such as
`.tmp/controlled-secret.txt` containing a fake high-entropy token, running:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/test.ps1
```

The secret scanner should fail. Remove the temporary file and rerun the same
command; the full check suite should pass. Do not commit the fixture.
