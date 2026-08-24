#Requires -Version 5.1
<#
.SYNOPSIS
    Run the FreeTier Atlas F001 repository checks locally, mirroring CI.
.DESCRIPTION
    Runs Ruff lint, Ruff format check, pytest, Prettier check, ESLint, a
    detect-secrets scan against the committed baseline, a URL host allowlist
    check, and Python dependency audits. With -NodeAudit, also audits the root
    Node tooling and the separate apps/web install. Resolves the repository root
    from this script's own path so it can be invoked from any working directory.
    Prefers tools from a local .venv when present and falls back to tools on PATH.
.NOTES
    Exit code 0 when all checks pass; non-zero when any check fails.

    REPORTING RULE, and it is why several branches below are longer than the
    work they do: a branch may assert only what it has ESTABLISHED. Turning an
    exit status into a specific stated cause is permitted only where this script
    has independently measured that cause. Everywhere else it reports the status
    it actually observed and surfaces the tool's own words instead.

    That rule was paid for. An earlier version mapped detect-secrets-hook exit 1
    to one sentence, "a secret that is not in the baseline was found". Exit 1 is
    also what that hook returns for an UNSTAGED baseline, which is the condition
    that actually occurred - so the summary announced a secret leak that did not
    exist while the hook's own output, one line above, read "Your baseline file
    (.secrets.baseline) is unstaged." A builder trusting the summary raises a
    false alarm; one who "fixes" it by editing the baseline does real damage.
    Two further false alarms on this project had the same shape: absent
    node_modules reported as a Prettier and an ESLint failure.

    The defect has a quieter, symmetric form that is easy to leave instrumented
    on one side only, so both are handled here: asserting a PASS that was never
    established either. See the empty-file-list branch of the secret scan.
#>
[CmdletBinding()]
param(
    [switch] $NodeAudit
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$venvScripts = Join-Path $RepoRoot ".venv/Scripts"

function Resolve-Tool {
    param([string] $Name)
    $venvExe = Join-Path $venvScripts "$Name.exe"
    if (Test-Path $venvExe) { return $venvExe }
    return $Name
}

# Was the name resolved to a real program, or is it still a hopeful bare word?
# Asking BEFORE running anything is what lets a missing toolchain report itself
# rather than surface as a lint, formatting or audit verdict.
function Test-ToolAvailable {
    param([string] $Resolved)
    if (Test-Path -LiteralPath $Resolved) { return $true }
    return [bool] (Get-Command -Name $Resolved -ErrorAction SilentlyContinue)
}

function Get-BaselineDigest {
    $path = Join-Path $RepoRoot ".secrets.baseline"
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
}

$script:Failures = @()

function Add-Failure {
    param([string] $Name, [string] $Reason)
    Write-Host "    FAIL: $Name ($Reason)" -ForegroundColor Red
    $script:Failures += [pscustomobject]@{ Name = $Name; Reason = $Reason }
}

# A sentinel no process can return, so "this action established no exit status"
# stays DISTINGUISHABLE from "this action exited 0". Without it, a check whose
# action runs no native command silently inherits whatever the PREVIOUS check
# left in $LASTEXITCODE and reports that stale number as its own result - a
# status this check never established. $LASTEXITCODE is ambient state whose
# propagation differs across PowerShell versions and which pipeline elements
# such as Select-Object corrupt, so it is always read bare and immediately.
$script:NoExitCode = -9999

function Invoke-Check {
    param(
        [string] $Name,
        [scriptblock] $Action,
        [string[]] $RequiresTool = @(),
        [string[]] $RequiresPath = @()
    )
    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan

    # Preconditions first. Each cause named here was MEASURED - the program was
    # looked for and not found, the directory was tested and is absent - rather
    # than inferred from a non-zero exit afterwards.
    foreach ($tool in $RequiresTool) {
        if (-not (Test-ToolAvailable $tool)) {
            Add-Failure $Name ("required tool '$tool' was not found in .venv/Scripts nor on PATH. " +
                "This is a missing toolchain, NOT a finding from this check")
            return
        }
    }
    foreach ($needed in $RequiresPath) {
        if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot $needed))) {
            Add-Failure $Name ("required path '$needed' is missing; run the install step that " +
                "creates it. This is a missing dependency, NOT a finding from this check")
            return
        }
    }

    $global:LASTEXITCODE = $script:NoExitCode
    try {
        & $Action
        $observed = $LASTEXITCODE
        if ($observed -eq $script:NoExitCode) {
            Add-Failure $Name "the check ran but established no exit status, so its result is unknown"
            return
        }
        if ($observed -ne 0) {
            Add-Failure $Name "exit code $observed"
            return
        }
        Write-Host "    PASS: $Name" -ForegroundColor Green
    }
    catch {
        # The tool's or the runtime's own words, quoted. Never replaced with an
        # interpretation of them.
        Add-Failure $Name $_.Exception.Message
    }
}

$ruff = Resolve-Tool "ruff"
$pytest = Resolve-Tool "pytest"
$detectHook = Resolve-Tool "detect-secrets-hook"
$pipAudit = Resolve-Tool "pip-audit"

$venvPython = Join-Path $venvScripts "python.exe"
if (Test-Path $venvPython) { $python = $venvPython } else { $python = "python" }

Invoke-Check "Ruff lint" { & $ruff check . } -RequiresTool $ruff
Invoke-Check "Ruff format check" { & $ruff format --check . } -RequiresTool $ruff
Invoke-Check "Pytest" { & $pytest -q } -RequiresTool $pytest
Invoke-Check "Prettier check" { & npm run --silent format:check } -RequiresTool "npm" -RequiresPath "node_modules"
Invoke-Check "ESLint" { & npm run --silent lint } -RequiresTool "npm" -RequiresPath "node_modules"
Invoke-Check "Secrets baseline shape" { & $python scripts/check_secrets_baseline.py } -RequiresTool $python
# Runs after the shape check on purpose: detect-secrets-hook REWRITES the baseline
# when it updates it, so scanning first would repair the file in place and hide the
# state that was actually committed.
Invoke-Check "Secret scan" -RequiresTool $detectHook -Action {
    $files = @(git ls-files -co --exclude-standard)
    $listExit = $LASTEXITCODE
    if ($listExit -ne 0) {
        throw "git ls-files exited $listExit, so the set of files to scan was never established"
    }
    if ($files.Count -eq 0) {
        # MEASURED: this hook exits 0 when handed no filenames. Passing here
        # would report a clean scan of nothing at all - the same defect as the
        # exit-1 branch below, pointing the reassuring way instead of the
        # alarming one, and therefore far likelier to go unnoticed.
        throw "git ls-files listed no files, so nothing would have been scanned"
    }

    $digestBefore = Get-BaselineDigest
    $stderrFile = [System.IO.Path]::GetTempFileName()
    $stdout = @()
    $code = $script:NoExitCode
    $stderrText = ""
    try {
        try {
            # stderr goes to a FILE rather than through 2>&1: merging it into
            # the success stream yields ErrorRecords whose handling differs by
            # host, and on a host where a non-zero native exit is itself
            # terminating, this call throws. Either way the status lands in
            # $LASTEXITCODE, so it is read there, bare, right after the call.
            $stdout = @(& $detectHook --baseline .secrets.baseline @($files) 2> $stderrFile)
        }
        catch {
            $stdout = @()
        }
        $code = $LASTEXITCODE
        if (Test-Path -LiteralPath $stderrFile) {
            $stderrText = [string] (Get-Content -LiteralPath $stderrFile -Raw)
        }
    }
    finally {
        Remove-Item -LiteralPath $stderrFile -Force -ErrorAction SilentlyContinue
    }

    # ESTABLISHED rather than inferred: the bytes on disk changed. Measured
    # independently of the exit status, so it can be named whatever was returned.
    $digestAfter = Get-BaselineDigest
    $rewritten = ($null -ne $digestBefore) -and ($digestBefore -ne $digestAfter)

    if ($code -eq 0 -and -not $rewritten) { return }

    $said = @()
    foreach ($line in $stdout) {
        if ($null -ne $line -and "$line".Trim() -ne "") { $said += "$line" }
    }
    if ($null -ne $stderrText -and $stderrText.Trim() -ne "") {
        foreach ($line in ($stderrText -split "`r?`n")) {
            if ($line.Trim() -ne "") { $said += $line }
        }
    }
    if ($said.Count -gt 0) {
        Write-Host "    detect-secrets-hook said:" -ForegroundColor Yellow
        foreach ($line in $said) { Write-Host "      $line" }
    }
    else {
        Write-Host "    detect-secrets-hook produced no output." -ForegroundColor Yellow
    }

    if ($rewritten) {
        throw ("detect-secrets REWROTE .secrets.baseline (SHA256 $digestBefore -> $digestAfter). " +
            "A rewrite is NOT a secret finding. Restore it with " +
            "'git checkout -- .secrets.baseline', then refresh with " +
            "'python scripts/refresh_secrets_baseline.py', which keeps keys posix.")
    }

    # Deliberately NOT a cause. This hook returns 1 for a secret outside the
    # baseline AND for an unstaged baseline AND for other argument errors, and
    # this script cannot tell those apart - so it does not pretend to. The
    # hook's own output, printed immediately above, is the authoritative
    # statement of what happened.
    throw ("detect-secrets-hook exited $code. Its own output above is the reason; " +
        "this script does not infer one from the exit code")
}
Invoke-Check "URL host allowlist" { & $python scripts/check_urls.py } -RequiresTool $python
Invoke-Check "Audit Python production dependencies (apps/api)" -RequiresTool $pipAudit -Action {
    & $pipAudit -r apps/api/requirements.txt
}
Invoke-Check "Audit Python production dependencies (apps/worker)" -RequiresTool $pipAudit -Action {
    & $pipAudit -r apps/worker/requirements.txt
}
Invoke-Check "Audit Python development dependencies" -RequiresTool $pipAudit -Action {
    & $pipAudit -r requirements-dev.txt
}

if ($NodeAudit) {
    Invoke-Check "Audit Node dependencies (repo root tooling)" -RequiresTool "npm" -RequiresPath "node_modules" -Action {
        & npm audit --audit-level=high
    }
    Invoke-Check "Audit Node dependencies (apps/web)" -RequiresTool "npm" -RequiresPath "apps/web/node_modules" -Action {
        Push-Location (Join-Path $RepoRoot "apps/web")
        try {
            & npm audit --audit-level=high
            $auditExit = $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
        if ($auditExit -ne 0) {
            throw "exit code $auditExit"
        }
    }
}
else {
    Write-Host ""
    Write-Host "    SKIP: Audit Node dependencies (repo root tooling) - not run; pass -NodeAudit to include Node dependency audits." -ForegroundColor Yellow
    Write-Host "    SKIP: Audit Node dependencies (apps/web) - not run; pass -NodeAudit to include Node dependency audits." -ForegroundColor Yellow
}

Write-Host ""
if ($script:Failures.Count -gt 0) {
    # Each line carries the reason established for that check. The old summary
    # named only the checks, so a reader had to scroll back for the reason and
    # in practice supplied one from imagination instead.
    Write-Host "CHECKS FAILED:" -ForegroundColor Red
    foreach ($failure in $script:Failures) {
        Write-Host "  - $($failure.Name): $($failure.Reason)" -ForegroundColor Red
    }
    exit 1
}
Write-Host "ALL CHECKS PASSED" -ForegroundColor Green
exit 0
