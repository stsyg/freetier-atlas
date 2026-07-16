#Requires -Version 5.1
<#
.SYNOPSIS
    Stop and remove the FreeTier Atlas development stack.
.DESCRIPTION
    Runs `docker compose down`. With -Volumes, also removes the PostgreSQL data
    volume (destroys local database data). Resolves the repository root from the
    script's own path.
.NOTES
    Exit code 0 on success; non-zero otherwise.
#>
[CmdletBinding()]
param(
    [switch] $Volumes
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if ($Volumes) {
    Write-Host "==> docker compose down --volumes (removes database data)" -ForegroundColor Yellow
    & docker compose down --volumes
}
else {
    Write-Host "==> docker compose down" -ForegroundColor Cyan
    & docker compose down
}
if ($LASTEXITCODE -ne 0) { Write-Error "docker compose down failed"; exit 1 }

Write-Host "STACK DOWN" -ForegroundColor Green
exit 0
