#Requires -Version 5.1
<#
.SYNOPSIS
    Run the FreeTier Atlas F001 repository checks locally, mirroring CI.
.DESCRIPTION
    Runs Ruff lint, Ruff format check, pytest, Prettier check, ESLint, a
    detect-secrets scan against the committed baseline, and a Python dependency
    audit. Resolves the repository root from this script's own path so it can be
    invoked from any working directory. Prefers tools from a local .venv when
    present and falls back to tools on PATH.
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

Invoke-Check "Ruff lint" { & $ruff check . }
Invoke-Check "Ruff format check" { & $ruff format --check . }
Invoke-Check "Pytest" { & $pytest -q }
Invoke-Check "Prettier check" { & npm run --silent format:check }
Invoke-Check "ESLint" { & npm run --silent lint }
Invoke-Check "Secret scan" {
    $files = git ls-files -co --exclude-standard
    & $detectHook --baseline .secrets.baseline @($files)
}
Invoke-Check "Python dependency audit" { & $pipAudit -r requirements-dev.txt }

if ($NodeAudit) {
    Invoke-Check "Node dependency audit" { & npm audit --omit=dev --audit-level=high }
}

Write-Host ""
if ($script:Failures.Count -gt 0) {
    Write-Host "CHECKS FAILED: $($script:Failures -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "ALL CHECKS PASSED" -ForegroundColor Green
exit 0
