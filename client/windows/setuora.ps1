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

function Test-SetuoraAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

function Start-SetuoraElevated {
    $arguments = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"' + $PSCommandPath + '"'),
        $Command
    )
    if ($RemainingArguments) {
        $arguments += $RemainingArguments
    }
    $process = Start-Process `
        -FilePath "powershell.exe" `
        -Verb RunAs `
        -Wait `
        -PassThru `
        -ArgumentList $arguments
    exit $process.ExitCode
}

function Read-SetuoraYesNo {
    param(
        [string]$Prompt,
        [bool]$Default = $true
    )
    $suffix = if ($Default) { "Y/n" } else { "y/N" }
    while ($true) {
        $answer = Read-Host "$Prompt [$suffix]"
        if ([string]::IsNullOrWhiteSpace($answer)) {
            return $Default
        }
        switch ($answer.Trim().ToLowerInvariant()) {
            "y" { return $true }
            "yes" { return $true }
            "n" { return $false }
            "no" { return $false }
            default { Write-Host "Please answer yes or no." -ForegroundColor Yellow }
        }
    }
}

function Get-SetuoraWinget {
    $command = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    try {
        Add-AppxPackage `
            -RegisterByFamilyName `
            -MainPackage "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe" `
            -ErrorAction Stop
    }
    catch {
        # The registration command is unavailable on older unsupported Windows
        # builds. The actionable error is reported below.
    }
    $command = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    throw @"
Windows Package Manager (WinGet) is unavailable. Install/update Microsoft's
'App Installer' system component, then run this installer again.
"@
}

function Get-SetuoraPython {
    $launchers = @(
        @{ Name = "py"; Prefix = @("-3") },
        @{ Name = "python"; Prefix = @() },
        @{ Name = "python3"; Prefix = @() },
        @{ Name = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"; Prefix = @() },
        @{ Name = "$env:ProgramFiles\Python311\python.exe"; Prefix = @() }
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

    return $null
}

function Install-SetuoraPython {
    if (-not (Read-SetuoraYesNo "Python 3.11 is missing. Install it now?" $true)) {
        throw "Python 3.11 is required by the Setuora deployment controller."
    }
    Write-Host "Installing Python 3.11 with Windows Package Manager..." -ForegroundColor Cyan
    $winget = Get-SetuoraWinget
    & $winget install `
        --id Python.Python.3.11 `
        --exact `
        --source winget `
        --scope user `
        --accept-package-agreements `
        --accept-source-agreements `
        --disable-interactivity
    if ($LASTEXITCODE -ne 0) {
        throw "Windows Package Manager could not install Python 3.11."
    }
    $python = Get-SetuoraPython
    if (-not $python) {
        throw "Python installation completed, but Python 3.11 could not be located. Restart Windows and run the installer again."
    }
    return $python
}

function Register-SetuoraSetupResume {
    $runOnce = "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce"
    New-Item -Path $runOnce -Force | Out-Null
    $commandLine = 'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + `
        $PSCommandPath + '" setup'
    New-ItemProperty `
        -Path $runOnce `
        -Name "SetuoraLiteSetup" `
        -Value $commandLine `
        -PropertyType String `
        -Force | Out-Null
}

function Stop-ForSetuoraRestart {
    Register-SetuoraSetupResume
    Write-Host ""
    Write-Host "Windows must restart once to finish WSL/Docker prerequisites." -ForegroundColor Yellow
    Write-Host "Setuora setup will resume automatically after your next sign-in."
    if (Read-SetuoraYesNo "Restart Windows now?" $true) {
        & shutdown.exe /r /t 15 /c "Restarting to continue Setuora Lite setup"
    }
    exit 3010
}

function Ensure-SetuoraWsl {
    $wsl = Get-Command "$env:WINDIR\System32\wsl.exe" -ErrorAction SilentlyContinue
    if (-not $wsl) {
        throw "This Windows version does not provide WSL 2. Setuora Lite requires a supported 64-bit Windows 10/11 system."
    }
    & $wsl.Source --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Updating the Windows Subsystem for Linux required by Docker Desktop..." -ForegroundColor Cyan
        & $wsl.Source --update
    }
    & $wsl.Source --version *> $null
    if ($LASTEXITCODE -eq 0) {
        & $wsl.Source --status *> $null
        if ($LASTEXITCODE -eq 0) {
            return
        }
    }
    Write-Host "Enabling WSL 2. Windows may require one restart..." -ForegroundColor Cyan
    & $wsl.Source --install --no-distribution
    $installExit = $LASTEXITCODE
    if ($installExit -ne 0 -and $installExit -ne 3010) {
        throw "WSL installation failed with exit code $installExit."
    }
    Stop-ForSetuoraRestart
}

function Assert-SetuoraWindowsSupport {
    if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
        throw "This package supports 64-bit x86 Windows only."
    }
    $operatingSystem = Get-CimInstance Win32_OperatingSystem
    $build = [int]$operatingSystem.BuildNumber
    if ($build -lt 19045 -or ($build -ge 22000 -and $build -lt 22631)) {
        throw "This Windows build ($build) is below Docker Desktop's supported Windows 10/11 versions. Install Windows updates and run this installer again."
    }
    $computer = Get-CimInstance Win32_ComputerSystem
    if ([int64]$computer.TotalPhysicalMemory -lt 7GB) {
        throw "Docker Desktop requires at least 8 GB of system RAM."
    }
}

function Find-SetuoraDocker {
    $command = Get-Command "docker.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $candidates = @(
        "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe",
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            $env:Path = (Split-Path -Parent $candidate) + ";" + $env:Path
            return $candidate
        }
    }
    return $null
}

function Install-SetuoraDockerDesktop {
    Write-Host ""
    Write-Host "Docker Desktop is required to run Setuora Lite and its isolated Tailscale service." -ForegroundColor Cyan
    Write-Host "Docker's subscription terms apply: https://www.docker.com/legal/docker-subscription-service-agreement/"
    if (-not (Read-SetuoraYesNo "Download Docker Desktop and accept its license terms?" $true)) {
        throw "Docker Desktop is required to run Setuora Lite."
    }
    Ensure-SetuoraWsl
    $installer = Join-Path $env:TEMP ("Setuora-Docker-" + [guid]::NewGuid().ToString("N") + ".exe")
    try {
        Write-Host "Downloading the official Docker Desktop installer. This is a large download..."
        Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe" `
            -OutFile $installer
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "Docker") {
            throw "The downloaded Docker Desktop installer does not have a valid Docker signature."
        }
        $process = Start-Process `
            -FilePath $installer `
            -Wait `
            -PassThru `
            -ArgumentList @(
                "install",
                "--accept-license",
                "--backend=wsl-2",
                "--always-run-service",
                "--no-windows-containers"
            )
        if ($process.ExitCode -eq 3010) {
            Stop-ForSetuoraRestart
        }
        if ($process.ExitCode -ne 0) {
            throw "Docker Desktop installation failed with exit code $($process.ExitCode)."
        }
    }
    finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
}

function Start-SetuoraDockerDesktop {
    param([string]$DockerCommand)
    & $DockerCommand info *> $null
    if ($LASTEXITCODE -eq 0) {
        return
    }
    $desktopCandidates = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
    )
    $desktop = $desktopCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $desktop) {
        throw "Docker Desktop is installed, but its application could not be located."
    }
    Write-Host "Starting Docker Desktop..." -ForegroundColor Cyan
    Start-Process -FilePath $desktop | Out-Null
    $deadline = [DateTime]::UtcNow.AddMinutes(6)
    while ([DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Seconds 3
        & $DockerCommand info *> $null
        if ($LASTEXITCODE -eq 0) {
            return
        }
    }
    throw "Docker Desktop did not become ready within six minutes. Confirm that hardware virtualization and WSL 2 are enabled, then run the installer again."
}

function Get-SetuoraEnvValue {
    param(
        [string]$Name,
        [string]$Default = ""
    )
    $envPath = Join-Path $PSScriptRoot ".env"
    if (-not (Test-Path -LiteralPath $envPath)) {
        return $Default
    }
    $prefix = $Name + "="
    $line = Get-Content -LiteralPath $envPath |
        Where-Object { $_.StartsWith($prefix, [StringComparison]::Ordinal) } |
        Select-Object -Last 1
    if (-not $line) {
        return $Default
    }
    $value = $line.Substring($prefix.Length).Trim()
    if ($value.Length -ge 2 -and (
        ($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))
    )) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    return $value
}

function Complete-SetuoraWindowsSetup {
    $certificate = Join-Path $PSScriptRoot "setuora-lite-root-ca.crt"
    if (Test-Path -LiteralPath $certificate) {
        Write-Host "Trusting this installation's private LAN certificate authority on this server..." -ForegroundColor Cyan
        Import-Certificate `
            -FilePath $certificate `
            -CertStoreLocation "Cert:\LocalMachine\Root" | Out-Null
    }

    $bindAddress = Get-SetuoraEnvValue "SETUORA_LAN_BIND_ADDRESS"
    $httpPort = Get-SetuoraEnvValue "SETUORA_HTTP_PORT" "80"
    $httpsPort = Get-SetuoraEnvValue "SETUORA_HTTPS_PORT" "443"
    if ($bindAddress) {
        $ruleName = "Setuora Lite LAN HTTPS"
        Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue |
            Remove-NetFirewallRule
        New-NetFirewallRule `
            -DisplayName $ruleName `
            -Description "Allow Setuora Lite from Domain/Private LAN networks only" `
            -Direction Inbound `
            -Action Allow `
            -Protocol TCP `
            -LocalAddress $bindAddress `
            -LocalPort @($httpPort, $httpsPort) `
            -Profile Domain,Private | Out-Null
    }

    $desktopCandidates = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
    )
    $desktop = $desktopCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($desktop) {
        $runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
        New-Item -Path $runKey -Force | Out-Null
        New-ItemProperty `
            -Path $runKey `
            -Name "SetuoraDockerDesktop" `
            -Value ('"' + $desktop + '"') `
            -PropertyType String `
            -Force | Out-Null
    }

    Remove-ItemProperty `
        -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce" `
        -Name "SetuoraLiteSetup" `
        -ErrorAction SilentlyContinue
    $hostname = Get-SetuoraEnvValue "SETUORA_LAN_HOSTNAME" $bindAddress
    if ($hostname) {
        $authority = if ($httpsPort -eq "443") { $hostname } else { "${hostname}:$httpsPort" }
        $url = "https://$authority"
        Write-Host ""
        Write-Host "Setuora Lite is fully installed: $url" -ForegroundColor Green
        Write-Host "Install this public CA on each approved phone/workstation: $certificate"
        Start-Process $url | Out-Null
    }
}

if ($Command -eq "setup" -and -not (Test-SetuoraAdministrator)) {
    Write-Host "Windows will request administrator approval to install prerequisites and LAN HTTPS." -ForegroundColor Cyan
    Start-SetuoraElevated
}
if ($Command -eq "setup") {
    Assert-SetuoraWindowsSupport
}

$python = Get-SetuoraPython
if (-not $python) {
    if ($Command -ne "setup") {
        throw "Python 3.11 is missing. Run the Setuora installer again to repair prerequisites."
    }
    $python = Install-SetuoraPython
}

$docker = Find-SetuoraDocker
if (-not $docker) {
    if ($Command -ne "setup") {
        throw "Docker Desktop is missing. Run the Setuora installer again to repair prerequisites."
    }
    Install-SetuoraDockerDesktop
    $docker = Find-SetuoraDocker
}
if (-not $docker) {
    throw "Docker Desktop installation completed, but docker.exe could not be located."
}
Start-SetuoraDockerDesktop -DockerCommand $docker

& $docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose v2 is required. Update Docker Desktop, then try again."
}

$pythonName = [string]$python["Name"]
$arguments = @($python["Prefix"]) + @("$PSScriptRoot\deploy.py", $Command)
if ($RemainingArguments) {
    $arguments += $RemainingArguments
}

& $pythonName @arguments
$controllerExit = $LASTEXITCODE
if ($controllerExit -eq 0 -and $Command -eq "setup") {
    Complete-SetuoraWindowsSetup
}
exit $controllerExit
