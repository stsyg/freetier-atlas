#Requires -Version 5.1
<#
.SYNOPSIS
    Build and start the FreeTier Atlas development stack.
.DESCRIPTION
    Runs `docker compose up -d --build` for the postgres and api services and
    waits until the API liveness endpoint responds. Resolves the repository root
    from this script's own path.
.NOTES
    Exit code 0 when the stack is up and the API is live; non-zero otherwise.
#>
[CmdletBinding()]
param(
    [int] $TimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$apiPort = if ($env:API_PORT) { $env:API_PORT } else { "8000" }

Write-Host "==> docker compose up -d --build" -ForegroundColor Cyan
& docker compose up -d --build
if ($LASTEXITCODE -ne 0) { Write-Error "docker compose up failed"; exit 1 }

$healthUrl = "http://localhost:$apiPort/health"
Write-Host "==> Waiting for API liveness at $healthUrl (timeout ${TimeoutSeconds}s)" -ForegroundColor Cyan

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$live = $false
while ((Get-Date) -lt $deadline) {
    try {
        $resp = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 5
        if ($resp.StatusCode -eq 200) { $live = $true; break }
    }
    catch {
        Start-Sleep -Seconds 3
    }
}

if (-not $live) {
    Write-Host "STACK UP FAILED: API did not become live within ${TimeoutSeconds}s." -ForegroundColor Red
    Write-Host "Recent api logs:" -ForegroundColor Yellow
    & docker compose logs --tail 40 api
    exit 1
}

Write-Host ""
Write-Host "STACK UP: API is live at $healthUrl" -ForegroundColor Green
Write-Host "Next: scripts/stack-smoke.ps1 to verify readiness and migrations." -ForegroundColor Green
exit 0
