#Requires -Version 5.1
<#
.SYNOPSIS
    Smoke-test the running FreeTier Atlas stack against ground truth.
.DESCRIPTION
    Verifies the live API liveness endpoint (200), the readiness endpoint (200
    with database reachable), and that the Alembic baseline migration created the
    scaffold app_meta table with its marker row. Resolves the repository root
    from this script's own path. Requires the stack to be running (stack-up).
.NOTES
    Exit code 0 when all checks pass; non-zero otherwise.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$apiPort = if ($env:API_PORT) { $env:API_PORT } else { "8000" }
$pgUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "atlas" }
$pgDb = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "atlas" }

$failures = @()

function Invoke-SmokeCheck {
    param([string] $Name, [scriptblock] $Action)
    Write-Host "==> $Name" -ForegroundColor Cyan
    try {
        & $Action
        Write-Host "    PASS: $Name" -ForegroundColor Green
    }
    catch {
        Write-Host "    FAIL: $Name ($_)" -ForegroundColor Red
        $script:failures += $Name
    }
}

Invoke-SmokeCheck "API liveness (/health = 200)" {
    $resp = Invoke-WebRequest -Uri "http://localhost:$apiPort/health" -UseBasicParsing -TimeoutSec 5
    if ($resp.StatusCode -ne 200) { throw "status $($resp.StatusCode)" }
    $body = $resp.Content | ConvertFrom-Json
    if ($body.status -ne "ok") { throw "unexpected status '$($body.status)'" }
}

Invoke-SmokeCheck "API readiness (/health/ready = 200, db ok)" {
    $resp = Invoke-WebRequest -Uri "http://localhost:$apiPort/health/ready" -UseBasicParsing -TimeoutSec 5
    if ($resp.StatusCode -ne 200) { throw "status $($resp.StatusCode)" }
    $body = $resp.Content | ConvertFrom-Json
    if ($body.status -ne "ready") { throw "unexpected status '$($body.status)'" }
    if ($body.checks.database -ne "ok") { throw "database check '$($body.checks.database)'" }
}

Invoke-SmokeCheck "Migration applied (app_meta table + marker row)" {
    $regclass = (& docker compose exec -T postgres psql -U $pgUser -d $pgDb -tAc "SELECT to_regclass('public.app_meta')").Trim()
    if ($regclass -ne "app_meta") { throw "app_meta table not found (got '$regclass')" }
    $marker = (& docker compose exec -T postgres psql -U $pgUser -d $pgDb -tAc "SELECT value FROM app_meta WHERE key='scaffold_initialized'").Trim()
    if ($marker -ne "true") { throw "scaffold marker not seeded (got '$marker')" }
}

Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host "STACK SMOKE FAILED: $($failures -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "STACK SMOKE PASSED" -ForegroundColor Green
exit 0
