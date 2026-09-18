[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("menu", "setup", "preflight", "start", "stop", "status", "open", "logs", "update", "update-runtime", "help")]
    [string]$Command = "menu",
    [switch]$Elevated,
    [switch]$PauseAfter,
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArguments
)

$ErrorActionPreference = "Stop"
$ApplicationRoot = $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $ApplicationRoot "deploy.py"))) {
    $ApplicationRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
}
$ControllerPath = $PSCommandPath
$ProductName = "Setuora Lite"
$BrowserUrl = "http://127.0.0.1:8000"
Set-Location -LiteralPath $ApplicationRoot

function Test-SetuoraAdministrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-SetuoraNative([string]$File, [string[]]$Arguments) {
    # Keep native stderr visible without cutting off the program's diagnostic output.
    $nativePreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $File @Arguments | Out-Host
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $nativePreference
    }
    return [int]$code
}

function Get-SetuoraPython {
    $launchers = @(
        @{ Name = "$ApplicationRoot\.venv\Scripts\python.exe"; Prefix = @() },
        @{ Name = "py"; Prefix = @("-3.11") },
        @{ Name = "py"; Prefix = @("-3") },
        @{ Name = "python"; Prefix = @() },
        @{ Name = "python3"; Prefix = @() },
        @{ Name = "$env:ProgramFiles\Python311\python.exe"; Prefix = @() }
    )
    foreach ($launcher in $launchers) {
        $name = [string]$launcher["Name"]
        $prefix = [string[]]$launcher["Prefix"]
        if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { continue }
        # A missing launcher version should try the next one on PowerShell 5.1 too.
        $probePreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & $name @prefix -c "import sys; raise SystemExit(sys.version_info < (3, 11))" 2>$null
            $supported = $LASTEXITCODE -eq 0
        } finally {
            $ErrorActionPreference = $probePreference
        }
        if ($supported) { return @{ Name = $name; Prefix = $prefix } }
    }
    return $null
}

function Install-SetuoraPython {
    $winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python 3.11 or newer is required. Install Python from python.org with 'Add Python to PATH' enabled, then choose Setup / repair again."
    }
    Write-Host "Installing Python 3.11. Keep this window open..." -ForegroundColor Cyan
    $code = Invoke-SetuoraNative $winget.Source @(
        "install", "--id", "Python.Python.3.11", "--exact", "--source", "winget",
        "--scope", "machine", "--accept-package-agreements", "--accept-source-agreements",
        "--disable-interactivity"
    )
    if ($code -ne 0) { throw "Python installation failed (exit $code). Install Python 3.11 from python.org, then retry Setup / repair." }
}

function Get-SetuoraTailscale {
    $installed = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
    if (Test-Path -LiteralPath $installed) { return $installed }
    if (${env:ProgramFiles(x86)}) {
        $installed = Join-Path ${env:ProgramFiles(x86)} "Tailscale\tailscale.exe"
        if (Test-Path -LiteralPath $installed) { return $installed }
    }
    $command = Get-Command "tailscale.exe" -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

function Install-SetuoraTailscale {
    $winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "Installing Tailscale with Windows Package Manager..." -ForegroundColor Cyan
        $code = Invoke-SetuoraNative $winget.Source @(
            "install", "--id", "Tailscale.Tailscale", "--exact", "--source", "winget",
            "--scope", "machine", "--accept-package-agreements", "--accept-source-agreements",
            "--disable-interactivity"
        )
        if ($code -eq 0 -and (Get-SetuoraTailscale)) { return }
        Write-Host "Windows Package Manager did not install Tailscale. Trying the official signed installer..." -ForegroundColor Yellow
    }
    $architecture = if ([Environment]::Is64BitOperatingSystem -and
        $env:PROCESSOR_ARCHITECTURE -ne "ARM64" -and
        $env:PROCESSOR_ARCHITEW6432 -ne "ARM64") { "amd64" } else { "x86" }
    $installer = Join-Path ([IO.Path]::GetTempPath()) ("setuora-tailscale-" + [guid]::NewGuid().ToString("N") + ".msi")
    try {
        $url = "https://pkgs.tailscale.com/stable/tailscale-setup-latest-$architecture.msi"
        Write-Host "Downloading Tailscale from pkgs.tailscale.com..." -ForegroundColor Cyan
        Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($signature.Status -ne "Valid" -or -not $signature.SignerCertificate -or
            $signature.SignerCertificate.Subject -notmatch "(^|,)\s*CN=Tailscale Inc\.?($|,)") {
            throw "The downloaded Tailscale installer does not have a valid Tailscale signature. Installation stopped."
        }
        $process = Start-Process -FilePath "msiexec.exe" -ArgumentList @(
            "/i", ('"' + $installer + '"'), "/qn", "/norestart"
        ) -Wait -PassThru
        if ($process.ExitCode -eq 3010) {
            throw "Tailscale installed but Windows requires a restart. Restart this computer, then run Setup / repair again."
        }
        if ($process.ExitCode -ne 0) { throw "Tailscale installation failed (exit $($process.ExitCode))." }
    } finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
    if (-not (Get-SetuoraTailscale)) { throw "Tailscale installation finished, but tailscale.exe was not found. Restart Windows and retry Setup / repair." }
}

function Get-SetuoraTailnetState([string]$Tailscale) {
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & $Tailscale status --json 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
    if ($code -ne 0) { throw "Tailscale status failed (exit $code): $($output -join ' ')" }
    try { return (($output -join [Environment]::NewLine) | ConvertFrom-Json) }
    catch { throw "Tailscale returned an unreadable status. Restart its Windows service and retry Setup / repair." }
}

function Initialize-SetuoraTailnet {
    $tailscale = Get-SetuoraTailscale
    if (-not $tailscale) {
        Install-SetuoraTailscale
        $tailscale = Get-SetuoraTailscale
    }
    $service = Get-Service -Name "Tailscale" -ErrorAction SilentlyContinue
    if (-not $service) { throw "The Tailscale Windows service is missing. Repair Tailscale, then retry Setup / repair." }
    Set-Service -Name "Tailscale" -StartupType Automatic
    if ($service.Status -ne "Running") { Start-Service -Name "Tailscale" }
    # A just-installed service may not answer status yet; `up` can initialize it.
    try { $state = Get-SetuoraTailnetState $tailscale } catch { $state = $null }
    if ($state.BackendState -eq "Running") {
        # `set` preserves any existing Tailscale options on an already joined PC.
        $code = Invoke-SetuoraNative $tailscale @("set", "--unattended=true")
    } else {
        Write-Host "Connecting this Lite computer to Tailscale. If shown a sign-in link, open it and sign in to the same tailnet as Master." -ForegroundColor Cyan
        $code = Invoke-SetuoraNative $tailscale @("up", "--unattended=true", "--timeout=10m")
    }
    if ($code -ne 0) { throw "Tailscale sign-in or unattended mode failed (exit $code). Complete the sign-in and retry Setup / repair." }
    $code = Invoke-SetuoraNative $tailscale @("set", "--accept-dns=true")
    if ($code -ne 0) { throw "Tailscale could not enable tailnet DNS (exit $code). Retry Setup / repair." }
    $state = Get-SetuoraTailnetState $tailscale
    if ($state.BackendState -ne "Running" -or -not $state.Self -or -not $state.Self.Online) {
        throw "Tailscale is not online. Check the sign-in or device approval, then retry Setup / repair."
    }
    Write-Host "Tailscale is online and will stay connected after logout."
    if ($state.Self.DNSName) { Write-Host "Lite tailnet name: $($state.Self.DNSName.TrimEnd('.'))" }
}

function Test-SetuoraTailnetReady {
    $tailscale = Get-SetuoraTailscale
    if (-not $tailscale) {
        throw "Setuora Lite is running locally, but Tailscale is missing. Run Setup / repair to enable Master sync."
    }
    $state = Get-SetuoraTailnetState $tailscale
    if ($state.BackendState -ne "Running" -or -not $state.Self -or -not $state.Self.Online) {
        throw "Setuora Lite is running locally, but Tailscale is offline. Master sync will wait. Check the network or run Setup / repair."
    }
    Write-Host "Tailscale is online for Master sync."
    $code = Invoke-SetuoraDeployment "master-check"
    if ($code -ne 0) { throw "Lite is running locally, but its Master HTTPS route check failed. Review the message above and retry after fixing the connection." }
}

function Invoke-SetuoraDeployment([string]$Action, [string[]]$ExtraArguments = @()) {
    $python = Get-SetuoraPython
    if (-not $python -and $Action -eq "setup") {
        Install-SetuoraPython
        $python = Get-SetuoraPython
    }
    if (-not $python) { throw "Python 3.11 or newer was not found. Choose Setup / repair first." }
    $arguments = @($python["Prefix"]) + @((Join-Path $ApplicationRoot "deploy.py"), $Action) + $ExtraArguments
    return Invoke-SetuoraNative ([string]$python["Name"]) $arguments
}

function Invoke-SetuoraElevated([string]$Action, [string[]]$ExtraArguments = @()) {
    if ($Elevated) { throw "Administrator access was not granted. Right-click setuora.bat and choose Run as administrator." }
    Write-Host "Approve the Windows security prompt. Complete this action in the new Administrator window." -ForegroundColor Cyan
    # Keep the child console interactive: setup asks for the first administrator password.
    $arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + $ControllerPath + '" ' + $Action + ' -Elevated -PauseAfter'
    foreach ($argument in $ExtraArguments) {
        if ($argument -notmatch '^[A-Za-z0-9_-]+$') { throw "Unsupported elevated command argument." }
        $arguments += ' ' + $argument
    }
    try {
        $process = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -Verb RunAs -Wait -PassThru
        return [int]$process.ExitCode
    } catch {
        Write-Host "Administrator approval was cancelled or the window could not be opened. No action was completed." -ForegroundColor Yellow
        return 1
    }
}

function Read-SetuoraGit([string[]]$Arguments) {
    $gitPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & git.exe -C $ApplicationRoot @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $gitPreference
    }
    if ($code -ne 0) { throw ("Git failed (exit $code): " + ($output -join [Environment]::NewLine)) }
    return ($output -join [Environment]::NewLine).Trim()
}

function Update-SetuoraSource {
    if (-not (Get-Command "git.exe" -ErrorAction SilentlyContinue)) { throw "Git for Windows is required to update this source checkout." }
    $code = Invoke-SetuoraDeployment "preflight"
    if ($code -ne 0) { return $code }
    $null = Read-SetuoraGit @("rev-parse", "--is-inside-work-tree")
    if (Read-SetuoraGit @("status", "--porcelain", "--untracked-files=all")) {
        throw "The source checkout has local changes. Save or commit them before updating. Nothing has been stopped or overwritten."
    }
    $branch = Read-SetuoraGit @("branch", "--show-current")
    if (-not $branch) { throw "Check out a Git branch before updating; detached HEAD cannot be updated automatically." }
    Write-Host "Checking origin/$branch for updates..." -ForegroundColor Cyan
    $null = Read-SetuoraGit @("fetch", "--quiet", "origin")
    $null = Read-SetuoraGit @("rev-parse", "--verify", "refs/remotes/origin/$branch")
    $null = Read-SetuoraGit @("merge-base", "--is-ancestor", "HEAD", "origin/$branch")
    $code = Invoke-SetuoraDeployment "stop"
    if ($code -ne 0) { return $code }
    try {
        $null = Read-SetuoraGit @("merge", "--ff-only", "origin/$branch")
    } catch {
        Write-Host "The source update failed. Attempting to restart the previous installation..." -ForegroundColor Yellow
        $null = Invoke-SetuoraDeployment "start"
        throw
    }
    try {
        $code = Invoke-SetuoraDeployment "update"
    } catch {
        Write-Host "The updated installation failed. Attempting a restart..." -ForegroundColor Yellow
        $null = Invoke-SetuoraDeployment "start"
        throw
    }
    if ($code -ne 0) {
        Write-Host "The updated installation did not start cleanly (exit $code). Attempting a restart..." -ForegroundColor Yellow
        $null = Invoke-SetuoraDeployment "start"
        return $code
    }
    Test-SetuoraTailnetReady
    return 0
}

function Install-SetuoraUpdate {
    Add-Type -AssemblyName System.Windows.Forms
    $picker = New-Object System.Windows.Forms.OpenFileDialog
    $picker.Title = "Choose the downloaded $ProductName installer"
    $picker.Filter = "$ProductName installer (Setuora-Lite-*-windows.cmd)|Setuora-Lite-*-windows.cmd"
    $picker.CheckFileExists = $true
    try {
        if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
            Write-Host "Update cancelled. The running application was left unchanged."
            return 0
        }
        $installer = $picker.FileName
    } finally {
        $picker.Dispose()
    }
    if ([IO.Path]::GetFileName($installer) -notmatch '^Setuora-Lite-[A-Za-z0-9._-]+-windows\.cmd$') {
        throw "Choose an official $ProductName Windows installer for this edition."
    }
    $process = Start-Process -FilePath $installer -Wait -PassThru
    return [int]$process.ExitCode
}

function Show-SetuoraHelp {
    Write-Host "$ProductName controls"
    Write-Host "Double-click setuora.bat to open the menu."
    Write-Host "Commands: setup, start, stop, status, open, logs, preflight, update, help"
    Write-Host "Setup, Start, Stop, Check configuration and Update request Administrator access."
    Write-Host "Source updates use Git. Installed copies ask you to choose a downloaded installer."
    Write-Host "Browser: $BrowserUrl"
}

function Invoke-SetuoraCommand([string]$Action, [string[]]$ExtraArguments = @()) {
    if ($Action -in @("setup", "start", "stop", "preflight", "update", "update-runtime")) {
        if (-not (Test-SetuoraAdministrator)) { return Invoke-SetuoraElevated $Action $ExtraArguments }
    }
    switch ($Action) {
        "help" { Show-SetuoraHelp; return 0 }
        "setup" {
            $code = Invoke-SetuoraDeployment "setup" $ExtraArguments
            if ($code -ne 0) { return $code }
            Initialize-SetuoraTailnet
            Test-SetuoraTailnetReady
            return 0
        }
        "start" {
            $code = Invoke-SetuoraDeployment "start" $ExtraArguments
            if ($code -ne 0) { return $code }
            Test-SetuoraTailnetReady
            return 0
        }
        "status" {
            $code = Invoke-SetuoraDeployment "status" $ExtraArguments
            if ($code -ne 0) { return $code }
            Test-SetuoraTailnetReady
            return 0
        }
        "open" {
            $code = Invoke-SetuoraDeployment "status"
            if ($code -ne 0) { return $code }
            Start-Process -FilePath $BrowserUrl
            return 0
        }
        "update" {
            if (Test-Path -LiteralPath (Join-Path $ApplicationRoot ".git")) { return Update-SetuoraSource }
            return Install-SetuoraUpdate
        }
        "update-runtime" {
            $code = Invoke-SetuoraDeployment "update"
            if ($code -ne 0) { return $code }
            Test-SetuoraTailnetReady
            return 0
        }

        default { return Invoke-SetuoraDeployment $Action $ExtraArguments }
    }
}

function Show-SetuoraMenu {
    while ($true) {
        Clear-Host
        Write-Host ""
        Write-Host "  $ProductName" -ForegroundColor Cyan
        Write-Host "  $ApplicationRoot"
        Write-Host ""
        Write-Host "  [1] Open in browser"
        Write-Host "  [2] Start"
        Write-Host "  [3] Stop"
        Write-Host "  [4] Check status"
        Write-Host "  [5] Setup / repair"
        if (Test-Path -LiteralPath (Join-Path $ApplicationRoot ".git")) {
            Write-Host "  [6] Update from Git"
        } else {
            Write-Host "  [6] Install downloaded update"
        }
        Write-Host "  [7] View recent logs"
        Write-Host "  [8] Check configuration"
        Write-Host "  [0] Exit"
        Write-Host ""
        Write-Host "  Closing this menu leaves Setuora running."
        $selection = Read-Host "Choose an option (0-8)"
        $action = switch ($selection) {
            "1" { "open" }; "2" { "start" }; "3" { "stop" }; "4" { "status" }
            "5" { "setup" }; "6" { "update" }; "7" { "logs" }; "8" { "preflight" }
            "0" { return 0 }
            default { "" }
        }
        if (-not $action) {
            Write-Host "Choose a number from 0 to 8." -ForegroundColor Yellow
        } else {
            try {
                $code = Invoke-SetuoraCommand $action
                if ($code -ne 0) { Write-Host "The action did not complete (exit $code). Review the message above; use View recent logs for server errors." -ForegroundColor Yellow }
            } catch {
                Write-Host $_.Exception.Message -ForegroundColor Red
            }
        }
        $null = Read-Host "Press Enter to return to the menu"
    }
}

$exitCode = 1
try {
    if (-not (Test-Path -LiteralPath (Join-Path $ApplicationRoot "deploy.py"))) { throw "This Setuora folder is incomplete. Run the installer again or use a complete source checkout." }
    if ($Command -eq "menu") {
        $exitCode = Show-SetuoraMenu
    } else {
        $exitCode = Invoke-SetuoraCommand $Command $RemainingArguments
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    $exitCode = 1
}
if ($PauseAfter) { $null = Read-Host "Press Enter to close this Administrator window" }
exit $exitCode
