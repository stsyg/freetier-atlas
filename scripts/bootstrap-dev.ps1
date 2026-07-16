#Requires -Version 5.1
<#
.SYNOPSIS
    Bootstrap the local development environment for FreeTier Atlas.
.DESCRIPTION
    Creates the Python virtual environment in .venv (if absent), upgrades pip,
    installs the project with its runtime and dev dependencies, and installs the
    Node dev dependencies. Resolves the repository root from this script's path.
.NOTES
    Exit code 0 on success; non-zero when a step fails.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "Python is required but was not found on PATH. Install Python 3.13+ and retry."
    exit 1
}

$venv = Join-Path $RepoRoot ".venv"
$venvPython = Join-Path $venv "Scripts/python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "==> Creating virtual environment (.venv)" -ForegroundColor Cyan
    & python -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-Error "Failed to create .venv"; exit 1 }
}
else {
    Write-Host "==> Reusing existing virtual environment (.venv)" -ForegroundColor Cyan
}

Write-Host "==> Upgrading pip" -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Write-Error "pip upgrade failed"; exit 1 }

Write-Host "==> Installing Python project with dev dependencies" -ForegroundColor Cyan
& $venvPython -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { Write-Error "Python dependency install failed"; exit 1 }

$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npm) {
    Write-Error "npm is required but was not found on PATH. Install Node.js 20+ and retry."
    exit 1
}

Write-Host "==> Installing Node dev dependencies (npm install)" -ForegroundColor Cyan
& npm install
if ($LASTEXITCODE -ne 0) { Write-Error "npm install failed"; exit 1 }

Write-Host ""
Write-Host "BOOTSTRAP COMPLETE" -ForegroundColor Green
Write-Host "Next: scripts/test.ps1 to run tests, or scripts/stack-up.ps1 to start the stack." -ForegroundColor Green
exit 0
