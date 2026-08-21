[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("setup", "preflight", "start", "stop", "status", "logs", "update")]
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
        @{ Name = "python3"; Prefix = @() },
        @{ Name = "$env:ProgramFiles\Python311\python.exe"; Prefix = @() }
    )
    foreach ($launcher in $launchers) {
        $name = [string]$launcher["Name"]
        $prefix = [string[]]$launcher["Prefix"]
        if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { continue }
        & $name @prefix -c "import sys; raise SystemExit(sys.version_info < (3, 11))" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @{ Name = $name; Prefix = $prefix }
        }
    }
    return $null
}

function Install-SetuoraPython {
    $winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw @"
Python 3.11 or newer is required. Install it from python.org with
'Add Python to PATH' enabled, then run this installer again.
"@
    }
    Write-Host "Installing Python 3.11..." -ForegroundColor Cyan
    & $winget.Source install `
        --id Python.Python.3.11 `
        --exact `
        --source winget `
        --scope machine `
        --accept-package-agreements `
        --accept-source-agreements `
        --disable-interactivity
    if ($LASTEXITCODE -ne 0) {
        throw "Windows Package Manager could not install Python 3.11."
    }
}

$python = Get-SetuoraPython
if (-not $python -and $Command -eq "setup") {
    Install-SetuoraPython
    $python = Get-SetuoraPython
}
if (-not $python) {
    throw "Python 3.11 or newer is required. Run the Setuora installer to repair it."
}

$pythonName = [string]$python["Name"]
$arguments = @($python["Prefix"]) + @("$PSScriptRoot\deploy.py", $Command)
if ($RemainingArguments) { $arguments += $RemainingArguments }

& $pythonName @arguments
exit $LASTEXITCODE
