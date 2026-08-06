#Requires -Version 5.1
<#
.SYNOPSIS
    Resolve the development stack's host-facing settings from Docker Compose.
.DESCRIPTION
    `docker compose` automatically reads the project's .env file; the helper
    scripts historically read only the process environment, so ports set solely
    in .env made the scripts probe the wrong host ports against a perfectly
    healthy stack (false failures).

    Rather than reimplement .env parsing and Compose's precedence rules, these
    helpers ask Compose for its own resolved model (`docker compose config
    --format json`). Compose stays the single source of truth: the .env file,
    the process environment (which wins over .env), and the `${VAR:-default}`
    defaults declared in docker-compose.yml are all applied by Compose itself.

    If Compose cannot be consulted at all, the helpers fall back to the process
    environment and then to the documented default, which is exactly the old
    behaviour.
.NOTES
    Dot-source this file; it defines functions and does not execute checks.
#>

$script:StackComposeConfig = $null
$script:StackComposeConfigLoaded = $false

function Get-StackComposeConfig {
    <#
    .SYNOPSIS
        Return Compose's resolved model as an object, or $null when unavailable.
    #>
    if ($script:StackComposeConfigLoaded) { return $script:StackComposeConfig }
    $script:StackComposeConfigLoaded = $true

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $raw = & docker compose config --format json 2>$null
        if ($LASTEXITCODE -eq 0 -and $raw) {
            $script:StackComposeConfig = (($raw -join "`n") | ConvertFrom-Json)
        }
    }
    catch {
        $script:StackComposeConfig = $null
    }
    finally {
        $ErrorActionPreference = $previous
    }

    return $script:StackComposeConfig
}

function Get-StackPort {
    <#
    .SYNOPSIS
        Published host port for a service's container port, per Compose.
    #>
    param(
        [Parameter(Mandatory = $true)] [string] $Service,
        [Parameter(Mandatory = $true)] [int]    $ContainerPort,
        [Parameter(Mandatory = $true)] [string] $EnvVar,
        [Parameter(Mandatory = $true)] [string] $Default
    )

    $config = Get-StackComposeConfig
    if ($config -and $config.services -and $config.services.$Service) {
        foreach ($mapping in @($config.services.$Service.ports)) {
            if ($null -eq $mapping) { continue }
            if ([int]$mapping.target -eq $ContainerPort -and $mapping.published) {
                return [string]$mapping.published
            }
        }
    }

    $fromEnv = [Environment]::GetEnvironmentVariable($EnvVar)
    if ($fromEnv) { return $fromEnv }
    return $Default
}

function Get-StackServiceSetting {
    <#
    .SYNOPSIS
        Resolved environment value for a service, per Compose.
    #>
    param(
        [Parameter(Mandatory = $true)] [string] $Service,
        [Parameter(Mandatory = $true)] [string] $Name,
        [Parameter(Mandatory = $true)] [string] $Default
    )

    $config = Get-StackComposeConfig
    if ($config -and $config.services -and $config.services.$Service) {
        $environment = $config.services.$Service.environment
        if ($environment -and ($environment.PSObject.Properties.Name -contains $Name)) {
            $value = $environment.$Name
            if ($value) { return [string]$value }
        }
    }

    $fromEnv = [Environment]::GetEnvironmentVariable($Name)
    if ($fromEnv) { return $fromEnv }
    return $Default
}
