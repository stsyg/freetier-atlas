#Requires -Version 5.1
<#
.SYNOPSIS
    Validate FreeTier Atlas declarative YAML configuration files.
.DESCRIPTION
    Runs the config validation CLI (app.config.cli) over the given files. With no
    arguments, validates every *.yaml under config/examples. Prefers the Python
    interpreter from a local .venv when present. Resolves the repository root from
    this script's own path. Never prints secrets.
.NOTES
    Exit code 0 when every file validates; non-zero otherwise.
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Paths
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$venvPython = Join-Path $RepoRoot ".venv/Scripts/python.exe"
if (Test-Path $venvPython) {
    $python = $venvPython
}
else {
    $python = "python"
    Write-Host "Note: .venv python not found; using python from PATH. Run scripts/bootstrap-dev.ps1 first for a pinned environment." -ForegroundColor Yellow
}

if (-not $Paths -or $Paths.Count -eq 0) {
    $examples = Join-Path $RepoRoot "config/examples"
    $Paths = Get-ChildItem -Path $examples -Recurse -Filter *.yaml | ForEach-Object { $_.FullName }
}

$env:PYTHONPATH = (Join-Path $RepoRoot "apps/api")
& $python -m app.config.cli validate @Paths
exit $LASTEXITCODE
