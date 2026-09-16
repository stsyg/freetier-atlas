#Requires -Version 5.1
<#
.SYNOPSIS
    Export the read-only catalogue to a deterministic static JSON snapshot.
.DESCRIPTION
    Thin wrapper around `python -m app.export` (see apps/api/app/export). It puts
    apps/api on PYTHONPATH so the `app` package imports without an editable
    install, prefers the pinned .venv interpreter, and passes every argument
    through to the CLI. Requires DATABASE_URL to point at the catalogue database.

    The snapshot is a pure function of (database state, -as-of): the same state
    and --as-of yield byte-identical output, so a published snapshot can be
    diffed and re-verified.
.EXAMPLE
    $env:DATABASE_URL = "postgresql+psycopg://atlas:atlas@localhost:5432/atlas"
    scripts/export-catalogue.ps1 --out dist/catalogue --as-of 2026-06-01T12:00:00+00:00
.NOTES
    Exit code 0 on success; 2 when DATABASE_URL is unset.
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ExportArgs
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

if (-not $env:DATABASE_URL) {
    Write-Error "DATABASE_URL is not set; the export reads the catalogue from the database."
    exit 2
}

$apiPath = Join-Path $RepoRoot "apps/api"
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$apiPath$([IO.Path]::PathSeparator)$env:PYTHONPATH"
}
else {
    $env:PYTHONPATH = $apiPath
}

& $python -m app.export @ExportArgs
exit $LASTEXITCODE
