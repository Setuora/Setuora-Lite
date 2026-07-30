[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet(
        "setup",
        "preflight",
        "start",
        "stop",
        "status",
        "logs",
        "update",
        "verify-sync",
        "export-ca"
    )]
    [string]$Command,

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArguments
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Get-SetuoraPython {
    $launchers = @(
        @{ Name = "py"; Prefix = @("-3") },
        @{ Name = "python"; Prefix = @() },
        @{ Name = "python3"; Prefix = @() }
    )

    foreach ($launcher in $launchers) {
        $name = [string]$launcher["Name"]
        $prefix = [string[]]$launcher["Prefix"]
        if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
            continue
        }

        & $name @prefix -c `
            "import sys; raise SystemExit(sys.version_info < (3, 11))" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @{ Name = $name; Prefix = $prefix }
        }
    }

    throw @"
Python 3.11 or newer is required.
Install it from https://www.python.org/downloads/windows/ and select
'Add python.exe to PATH', then run this launcher again.
"@
}

if (-not (Get-Command "docker" -ErrorAction SilentlyContinue)) {
    throw @"
Docker Desktop is required.
Install it from https://docs.docker.com/desktop/setup/install/windows-install/,
start Docker Desktop, and then run this launcher again.
"@
}

& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose v2 is required. Start or update Docker Desktop, then try again."
}

$python = Get-SetuoraPython
$pythonName = [string]$python["Name"]
$arguments = @($python["Prefix"]) + @("$PSScriptRoot\deploy.py", $Command)
if ($RemainingArguments) {
    $arguments += $RemainingArguments
}

& $pythonName @arguments
exit $LASTEXITCODE
