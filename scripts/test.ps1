#Requires -Version 5.1
<#
.SYNOPSIS
    Run the FreeTier Atlas test suite.
.DESCRIPTION
    Runs pytest (preferring tools from .venv). With -Full, also runs the full
    repository check suite (scripts/check.ps1 -NodeAudit). Additional arguments
    after -- are passed through to pytest. Resolves the repository root from the
    script's own path.
.NOTES
    Exit code 0 when tests pass; non-zero otherwise.
#>
[CmdletBinding()]
param(
    [switch] $Full,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PytestArgs
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$venvPytest = Join-Path $RepoRoot ".venv/Scripts/pytest.exe"
if (Test-Path $venvPytest) {
    $pytest = $venvPytest
}
else {
    $pytest = "pytest"
    Write-Host "Note: .venv pytest not found; using pytest from PATH. Run scripts/bootstrap-dev.ps1 first for a pinned environment." -ForegroundColor Yellow
}

Write-Host "==> pytest" -ForegroundColor Cyan
& $pytest @PytestArgs
$testExit = $LASTEXITCODE
if ($testExit -ne 0) {
    Write-Host "TESTS FAILED (exit $testExit)" -ForegroundColor Red
    exit $testExit
}

if ($Full) {
    Write-Host "==> Full check suite (scripts/check.ps1 -NodeAudit)" -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "check.ps1") -NodeAudit
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host ""
Write-Host "TESTS PASSED" -ForegroundColor Green
exit 0
