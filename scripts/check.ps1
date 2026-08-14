#Requires -Version 5.1
<#
.SYNOPSIS
    Run the FreeTier Atlas F001 repository checks locally, mirroring CI.
.DESCRIPTION
    Runs Ruff lint, Ruff format check, pytest, Prettier check, ESLint, a
    detect-secrets scan against the committed baseline, a URL host allowlist
    check, and Python dependency audits. With -NodeAudit, also audits the root
    Node tooling and the separate apps/web install. Resolves the repository root
    from this script's own path so it can be invoked from any working directory.
    Prefers tools from a local .venv when present and falls back to tools on PATH.
.NOTES
    Exit code 0 when all checks pass; non-zero when any check fails.
#>
[CmdletBinding()]
param(
    [switch] $NodeAudit
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$venvScripts = Join-Path $RepoRoot ".venv/Scripts"

function Resolve-Tool {
    param([string] $Name)
    $venvExe = Join-Path $venvScripts "$Name.exe"
    if (Test-Path $venvExe) { return $venvExe }
    return $Name
}

$script:Failures = @()

function Invoke-Check {
    param(
        [string] $Name,
        [scriptblock] $Action
    )
    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    try {
        & $Action
        if ($LASTEXITCODE -ne 0) {
            throw "exit code $LASTEXITCODE"
        }
        Write-Host "    PASS: $Name" -ForegroundColor Green
    }
    catch {
        Write-Host "    FAIL: $Name ($_)" -ForegroundColor Red
        $script:Failures += $Name
    }
}

$ruff = Resolve-Tool "ruff"
$pytest = Resolve-Tool "pytest"
$detectHook = Resolve-Tool "detect-secrets-hook"
$pipAudit = Resolve-Tool "pip-audit"

$venvPython = Join-Path $venvScripts "python.exe"
if (Test-Path $venvPython) { $python = $venvPython } else { $python = "python" }

Invoke-Check "Ruff lint" { & $ruff check . }
Invoke-Check "Ruff format check" { & $ruff format --check . }
Invoke-Check "Pytest" { & $pytest -q }
Invoke-Check "Prettier check" { & npm run --silent format:check }
Invoke-Check "ESLint" { & npm run --silent lint }
Invoke-Check "Secrets baseline shape" { & $python scripts/check_secrets_baseline.py }
# Runs after the shape check on purpose: detect-secrets-hook REWRITES the baseline
# when it updates it, so scanning first would repair the file in place and hide the
# state that was actually committed.
Invoke-Check "Secret scan" {
    $files = git ls-files -co --exclude-standard
    & $detectHook --baseline .secrets.baseline @($files)
    $code = $LASTEXITCODE
    # 0 clean, 1 findings, 3 the baseline was REWRITTEN. Exit 3 is not a finding,
    # and a builder has already misread it as one.
    switch ($code) {
        0 { }
        1 { throw "a secret that is not in the baseline was found" }
        3 {
            throw ("detect-secrets REWROTE .secrets.baseline; this is NOT a secret finding. " +
                "Restore it with 'git checkout -- .secrets.baseline', then refresh with " +
                "'python scripts/refresh_secrets_baseline.py', which keeps keys posix.")
        }
        default { throw "detect-secrets exited $code" }
    }
}
Invoke-Check "URL host allowlist" { & $python scripts/check_urls.py }
Invoke-Check "Audit Python production dependencies (apps/api)" {
    & $pipAudit -r apps/api/requirements.txt
}
Invoke-Check "Audit Python production dependencies (apps/worker)" {
    & $pipAudit -r apps/worker/requirements.txt
}
Invoke-Check "Audit Python development dependencies" {
    & $pipAudit -r requirements-dev.txt
}

if ($NodeAudit) {
    Invoke-Check "Audit Node dependencies (repo root tooling)" {
        & npm audit --audit-level=high
    }
    Invoke-Check "Audit Node dependencies (apps/web)" {
        Push-Location (Join-Path $RepoRoot "apps/web")
        try {
            & npm audit --audit-level=high
            $auditExit = $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
        if ($auditExit -ne 0) {
            throw "exit code $auditExit"
        }
    }
}
else {
    Write-Host ""
    Write-Host "    SKIP: Audit Node dependencies (repo root tooling) - not run; pass -NodeAudit to include Node dependency audits." -ForegroundColor Yellow
    Write-Host "    SKIP: Audit Node dependencies (apps/web) - not run; pass -NodeAudit to include Node dependency audits." -ForegroundColor Yellow
}

Write-Host ""
if ($script:Failures.Count -gt 0) {
    Write-Host "CHECKS FAILED: $($script:Failures -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "ALL CHECKS PASSED" -ForegroundColor Green
exit 0
