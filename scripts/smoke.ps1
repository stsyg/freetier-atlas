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

function Fail {
    param([string] $Message)
    Write-Error "ERROR: $Message"
    exit 1
}

function Validate-JsonFile {
    param(
        [string] $RepoRoot,
        [string] $RelativePath
    )

    $fullPath = Join-Path $RepoRoot $RelativePath
    try {
        Get-Content -Raw -LiteralPath $fullPath | ConvertFrom-Json | Out-Null
    }
    catch {
        Fail "invalid JSON in ${RelativePath}: $($_.Exception.Message)"
    }
}

$scriptDir = Split-Path -Parent $PSCommandPath
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $scriptDir "..")).Path

$requiredFiles = @(
    "AGENTS.md",
    "docs/AGENT_HARNESS.md",
    "PLAN.md",
    "CODEX_TASKS.md",
    "docs/MVP_ACCEPTANCE.md",
    "docs/DECISIONS.md",
    "agent-state/feature_list.json",
    "agent-state/progress.md",
    "agent-state/current_contract.json",
    "agent-state/evaluation.json"
)

$jsonFiles = @(
    "agent-state/feature_list.json",
    "agent-state/current_contract.json",
    "agent-state/evaluation.json"
)

Write-Output "FreeTier Atlas F000 smoke checks"
Write-Output "Repository root: $repoRoot"

foreach ($relativePath in $requiredFiles) {
    $fullPath = Join-Path $repoRoot $relativePath
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        Fail "required repository file is missing: $relativePath"
    }
}
Write-Output "Required repository files: ok"

foreach ($relativePath in $jsonFiles) {
    Validate-JsonFile -RepoRoot $repoRoot -RelativePath $relativePath
}
Write-Output "Agent-state JSON syntax: ok"

if ($PSVersionTable.PSEdition -eq "Core") {
    Write-Output "PowerShell verification available: PowerShell $($PSVersionTable.PSVersion)"
}
else {
    Write-Output "PowerShell verification available: Windows PowerShell $($PSVersionTable.PSVersion)"
}

Write-Output "Application scaffold checks: pending F002 - product application health was not checked because the app stack does not exist yet"
Write-Output "F000 smoke checks completed"
