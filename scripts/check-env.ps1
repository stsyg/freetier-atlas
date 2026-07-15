#Requires -Version 5.1
<#
.SYNOPSIS
    Verify the local development runtimes required by FreeTier Atlas.
.DESCRIPTION
    Checks that Docker (with a running daemon), Node.js, npm, and Python are
    available and prints their versions. Exits non-zero with an actionable
    message when a required runtime is missing. Does not print secrets or full
    environment dumps. Resolves the repository root from this script's own path.
.NOTES
    Exit code 0 when all required runtimes are present; non-zero otherwise.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$missing = @()

function Test-Runtime {
    param(
        [string] $Name,
        [string] $Command,
        [string[]] $VersionArgs = @("--version"),
        [string] $Hint
    )
    $cmd = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Write-Host ("  [MISSING] {0}: '{1}' not found on PATH. {2}" -f $Name, $Command, $Hint) -ForegroundColor Red
        $script:missing += $Name
        return
    }
    try {
        $version = (& $Command @VersionArgs 2>&1 | Select-Object -First 1)
        Write-Host ("  [ok]      {0}: {1}" -f $Name, $version) -ForegroundColor Green
    }
    catch {
        Write-Host ("  [ok]      {0}: present ({1})" -f $Name, $cmd.Source) -ForegroundColor Green
    }
}

Write-Host "FreeTier Atlas environment check" -ForegroundColor Cyan
Write-Host "Repository root: $RepoRoot"
Write-Host ""

Test-Runtime -Name "Docker" -Command "docker" -Hint "Install Docker Desktop or the Docker Engine."
Test-Runtime -Name "Node.js" -Command "node" -Hint "Install Node.js 20 or newer."
Test-Runtime -Name "npm" -Command "npm" -Hint "npm ships with Node.js."
Test-Runtime -Name "Python" -Command "python" -Hint "Install Python 3.13 or newer."

# Verify the Docker daemon is actually reachable (not just the CLI).
if ($missing -notcontains "Docker") {
    try {
        & docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "daemon unreachable" }
        Write-Host "  [ok]      Docker daemon: reachable" -ForegroundColor Green
    }
    catch {
        Write-Host "  [MISSING] Docker daemon: not reachable. Start Docker and retry." -ForegroundColor Red
        $missing += "Docker daemon"
    }
}

Write-Host ""
if ($missing.Count -gt 0) {
    Write-Host ("ENVIRONMENT CHECK FAILED: missing {0}" -f ($missing -join ", ")) -ForegroundColor Red
    exit 1
}
Write-Host "ENVIRONMENT CHECK PASSED" -ForegroundColor Green
exit 0
