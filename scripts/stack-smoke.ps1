#Requires -Version 5.1
<#
.SYNOPSIS
    Smoke-test the running FreeTier Atlas stack against ground truth.
.DESCRIPTION
    Verifies the live API liveness endpoint (200), the readiness endpoint (200
    with database reachable), that the Alembic migrations created the scaffold
    app_meta table plus the worker job_queue and service_heartbeat tables, that
    the worker and scheduler containers are healthy, that at least one queued job
    reached 'done', and that both service heartbeats are fresh. Resolves the
    repository root from this script's own path. Requires the stack to be running
    (stack-up).
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

function Wait-Until {
    param([int] $TimeoutSeconds = 90, [scriptblock] $Condition)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try { if (& $Condition) { return } } catch { }
        Start-Sleep -Seconds 3
    }
    throw "timed out after ${TimeoutSeconds}s"
}

function Get-ContainerHealth {
    param([string] $Service)
    $cid = (& docker compose ps -q $Service).Trim()
    if (-not $cid) { return "" }
    return (& docker inspect -f '{{.State.Health.Status}}' $cid).Trim()
}

function Invoke-Psql {
    param([string] $Sql)
    return (& docker compose exec -T postgres psql -U $pgUser -d $pgDb -tAc $Sql).Trim()
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

Invoke-SmokeCheck "Worker migration applied (job_queue + service_heartbeat)" {
    $jq = Invoke-Psql "SELECT to_regclass('public.job_queue')"
    if ($jq -ne "job_queue") { throw "job_queue table not found (got '$jq')" }
    $sh = Invoke-Psql "SELECT to_regclass('public.service_heartbeat')"
    if ($sh -ne "service_heartbeat") { throw "service_heartbeat table not found (got '$sh')" }
}

Invoke-SmokeCheck "Worker container healthy" {
    Wait-Until -TimeoutSeconds 90 -Condition { (Get-ContainerHealth "worker") -eq "healthy" }
}

Invoke-SmokeCheck "Scheduler container healthy" {
    Wait-Until -TimeoutSeconds 90 -Condition { (Get-ContainerHealth "scheduler") -eq "healthy" }
}

Invoke-SmokeCheck "Queue processed (>=1 job reached done)" {
    Wait-Until -TimeoutSeconds 90 -Condition {
        [int](Invoke-Psql "SELECT count(*) FROM job_queue WHERE status='done'") -ge 1
    }
}

Invoke-SmokeCheck "Heartbeats fresh (worker + scheduler)" {
    $n = Invoke-Psql "SELECT count(*) FROM service_heartbeat WHERE service IN ('worker','scheduler') AND last_beat_at > now() - interval '60 seconds'"
    if ([int]$n -ne 2) { throw "expected 2 fresh heartbeats, got '$n'" }
}

Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host "STACK SMOKE FAILED: $($failures -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "STACK SMOKE PASSED" -ForegroundColor Green
exit 0
