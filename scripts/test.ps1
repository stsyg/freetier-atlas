[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSEdition -ne "Core") {
    $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwsh) {
        & $pwsh.Source -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath @args
        exit $LASTEXITCODE
    }
}

$scriptDir = Split-Path -Parent $PSCommandPath
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $scriptDir "..")).Path

function Invoke-Checked {
    param(
        [string] $FilePath,
        [string[]] $Arguments = @()
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

Push-Location $repoRoot
try {
    & (Join-Path $scriptDir "init.ps1")
    & (Join-Path $scriptDir "smoke.ps1")
    Invoke-Checked -FilePath "python" -Arguments @("tools/repo_checks.py", "all")
    Invoke-Checked -FilePath "python" -Arguments @("-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py")
    Invoke-Checked -FilePath "npm" -Arguments @("test")
    Invoke-Checked -FilePath "git" -Arguments @("diff", "--check")
}
finally {
    Pop-Location
}
