[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("menu", "setup", "preflight", "start", "stop", "status", "open", "logs", "update", "update-runtime", "uninstall", "help")]
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
$BrowserUrl = "https://<lite-name>.<tailnet>.ts.net"
$CaddyRoot = Join-Path $env:ProgramData "Setuora\caddy-lite"
$CaddyTaskName = "Setuora-Lite-Caddy"
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
        @{ Name = "$env:ProgramFiles\Python313\python.exe"; Prefix = @() },
        @{ Name = "$env:ProgramFiles\Python312\python.exe"; Prefix = @() },
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
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq "ARM64") {
        throw "An x64 Windows 10 or 11 computer is required by the current locked runtime."
    }
    $winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "Installing Python through Windows Package Manager..." -ForegroundColor Cyan
        $code = Invoke-SetuoraNative $winget.Source @(
            "install", "--id", "Python.Python.3.13", "--exact", "--source", "winget",
            "--scope", "machine", "--accept-package-agreements", "--accept-source-agreements",
            "--disable-interactivity"
        )
        if ($code -eq 0 -and (Get-SetuoraPython)) { return }
        Write-Host "Windows Package Manager did not provide Python. Trying the signed python.org installer..." -ForegroundColor Yellow
    }

    # Keep this fallback on an actively supported Python release with Windows installers.
    # The runtime accepts Python 3.11+ and the locked wheels support Python 3.13.
    $architecture = "amd64"
    $version = "3.13.15"
    $url = "https://www.python.org/ftp/python/$version/python-$version-$architecture.exe"
    $installer = Join-Path ([IO.Path]::GetTempPath()) ("setuora-python-" + [guid]::NewGuid().ToString("N") + ".exe")
    try {
        Write-Host "Downloading the signed Python installer from python.org..." -ForegroundColor Cyan
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($signature.Status -ne [Management.Automation.SignatureStatus]::Valid -or
            $signature.SignerCertificate.Subject -notmatch "Python Software Foundation") {
            throw "The downloaded Python installer does not have a valid Python Software Foundation signature."
        }
        $process = Start-Process -FilePath $installer -ArgumentList @(
            "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_test=0"
        ) -Wait -PassThru -WindowStyle Hidden
        if ($process.ExitCode -notin @(0, 3010)) { throw "Python installation failed (exit $($process.ExitCode))." }
        if ($process.ExitCode -eq 3010) { throw "Python installed. Restart Windows, then run Setup / repair again." }
    } finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
    if (-not (Get-SetuoraPython)) { throw "Python installed but was not found. Restart Windows and run Setup / repair again." }
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
    $architecture = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64" -or $env:PROCESSOR_ARCHITEW6432 -eq "ARM64") { "arm64" } elseif ([Environment]::Is64BitOperatingSystem) { "amd64" } else { "x86" }
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

function Install-SetuoraCaddy([switch]$Upgrade) {
    $binary = Join-Path $CaddyRoot "caddy.exe"
    $versionFile = Join-Path $CaddyRoot "VERSION"
    [IO.Directory]::CreateDirectory($CaddyRoot) | Out-Null
    if ((Get-Item -LiteralPath $CaddyRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "The Caddy installation folder is a linked path. Setup stopped before changing permissions."
    }
    $code = Invoke-SetuoraNative "icacls.exe" @(
        $CaddyRoot, "/inheritance:r", "/grant:r",
        "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "/T", "/Q", "/L"
    )
    if ($code -ne 0) { throw "Caddy files could not be secured for the Windows SYSTEM task." }
    $code = Invoke-SetuoraNative "icacls.exe" @(
        $CaddyRoot, "/remove:g", "*S-1-5-32-545", "*S-1-5-11", "*S-1-1-0", "/T", "/Q", "/L"
    )
    if ($code -ne 0) { throw "Broad access to Caddy files could not be removed." }
    if (-not $Upgrade -and (Test-Path -LiteralPath $binary) -and (Test-Path -LiteralPath $versionFile)) { return $binary }
    $architecture = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64" -or $env:PROCESSOR_ARCHITEW6432 -eq "ARM64") { "arm64" } elseif ([Environment]::Is64BitOperatingSystem) { "amd64" } else { throw "Caddy requires a 64-bit Windows 10 or 11 computer." }
    Write-Host "Checking the current Caddy release..." -ForegroundColor Cyan
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/caddyserver/caddy/releases/latest" -Headers @{ "User-Agent" = "Setuora-Lite-Installer" }
    if ($release.tag_name -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+$') { throw "The Caddy release version could not be verified." }
    $version = $release.tag_name.Substring(1)
    $assetName = "caddy_${version}_windows_${architecture}.zip"
    $asset = @($release.assets | Where-Object { $_.name -eq $assetName })
    if ($asset.Count -ne 1 -or $asset[0].digest -notmatch '^sha256:[0-9a-fA-F]{64}$' -or $asset[0].browser_download_url -notlike "https://github.com/caddyserver/caddy/releases/download/$($release.tag_name)/*") {
        throw "The official Caddy release asset or SHA-256 digest is missing."
    }
    $expectedHash = $asset[0].digest.Substring(7)
    if ((Test-Path -LiteralPath $binary) -and (Test-Path -LiteralPath $versionFile) -and
        (Get-Content -LiteralPath $versionFile -Raw).Trim() -eq $version) { return $binary }
    $stage = Join-Path ([IO.Path]::GetTempPath()) ("setuora-caddy-" + [guid]::NewGuid().ToString("N"))
    [IO.Directory]::CreateDirectory($stage) | Out-Null
    try {
        $zip = Join-Path $stage $assetName
        Invoke-WebRequest -Uri $asset[0].browser_download_url -OutFile $zip -UseBasicParsing
        if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ine $expectedHash) { throw "The Caddy download failed its release SHA-256 check." }
        Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force
        $extracted = Join-Path $stage "caddy.exe"
        if (-not (Test-Path -LiteralPath $extracted)) { throw "The Caddy release did not contain caddy.exe." }
        Stop-SetuoraCaddy
        Copy-Item -LiteralPath $extracted -Destination $binary -Force
        Set-Content -LiteralPath $versionFile -Value $version -NoNewline
    } finally {
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $binary
}

function Stop-SetuoraCaddy {
    if (Get-ScheduledTask -TaskName $CaddyTaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $CaddyTaskName -ErrorAction SilentlyContinue
    }
    $binary = Join-Path $CaddyRoot "caddy.exe"
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $owned = @(Get-CimInstance Win32_Process -Filter "Name='caddy.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.ExecutablePath -eq $binary })
        if ($owned.Count -eq 0) { return }
        Start-Sleep -Seconds 1
    }
    throw "The Setuora Caddy process did not stop. Close it before replacing or restarting Caddy."
}

function Get-SetuoraSavedPorts {
    $path = Join-Path $ApplicationRoot '.runtime-ports.json'
    if (-not (Test-Path -LiteralPath $path)) { return [pscustomobject]@{ web_port = 8000; caddy_port = 8080 } }
    try {
        $ports = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        foreach ($port in @($ports.web_port, $ports.caddy_port)) {
            $parsed = 0
            if (-not [int]::TryParse([string]$port, [ref]$parsed) -or $parsed -lt 1024 -or $parsed -gt 65535) { throw 'Invalid port' }
        }
        return $ports
    } catch { throw 'The saved Lite ports are unreadable. Run Setup / repair.' }
}

function Initialize-SetuoraPrivateWeb([switch]$Upgrade) {
    $tailscale = Get-SetuoraTailscale
    if (-not $tailscale) { throw "Tailscale is missing. Run Setup / repair again." }
    $state = Get-SetuoraTailnetState $tailscale
    if ($state.BackendState -ne "Running" -or -not $state.Self.Online -or -not $state.Self.DNSName) {
        throw "Tailscale must be online with a MagicDNS name before private HTTPS can be configured."
    }
    $tailnetHost = $state.Self.DNSName.TrimEnd('.').ToLowerInvariant()
    $code = Invoke-SetuoraDeployment "trust-tailnet-host" @("--host", $tailnetHost)
    if ($code -ne 0) { throw "The private HTTPS hostname could not be added to TRUSTED_HOSTS." }
    $code = Invoke-SetuoraDeployment "start"
    if ($code -ne 0) { throw "Lite could not restart with its private HTTPS hostname." }
    $previousPorts = Get-SetuoraSavedPorts
    $binary = Install-SetuoraCaddy -Upgrade:$Upgrade
    Stop-SetuoraCaddy
    $code = Invoke-SetuoraDeployment "configure-caddy-port"
    if ($code -ne 0) { throw "A free Caddy localhost port could not be selected." }
    $ports = Get-SetuoraSavedPorts
    $config = Join-Path $CaddyRoot "Caddyfile"
    $template = Get-Content -LiteralPath (Join-Path $ApplicationRoot "scripts\windows\Caddyfile.lite") -Raw
    $content = $template.Replace('__CADDY_PORT__', [string]$ports.caddy_port).Replace('__APP_PORT__', [string]$ports.web_port)
    [IO.File]::WriteAllText($config, $content, (New-Object System.Text.UTF8Encoding($false)))
    $code = Invoke-SetuoraNative $binary @("validate", "--config", $config, "--adapter", "caddyfile")
    if ($code -ne 0) { throw "The Caddy configuration is invalid." }
    $action = New-ScheduledTaskAction -Execute $binary -Argument ('run --config "' + $config + '" --adapter caddyfile')
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
    Register-ScheduledTask -TaskName $CaddyTaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $CaddyTaskName
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:$($ports.caddy_port)/health" -TimeoutSec 2
            if ($response.status -eq "ok" -and $response.role -eq "lite") { $ready = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw "Caddy did not proxy Lite on localhost:$($ports.caddy_port). Check the $CaddyTaskName task status and run Setup / repair." }
    Write-Host "Enabling private HTTPS through Tailscale Serve..." -ForegroundColor Cyan
    $serve = Get-SetuoraServeConfig $tailscale
    Assert-SetuoraServeRoute $serve $tailnetHost $false $ports.caddy_port $previousPorts.caddy_port
    $routeKey = "$($tailnetHost):443"
    $rootRoute = if ($serve.Web -and $serve.Web.PSObject.Properties[$routeKey]) { $serve.Web.PSObject.Properties[$routeKey].Value.Handlers.PSObject.Properties['/'] } else { $null }
    if (-not $rootRoute -or $rootRoute.Value.Proxy -ne "http://127.0.0.1:$($ports.caddy_port)") {
        $code = Invoke-SetuoraNative $tailscale @("serve", "--bg", "--https=443", "--set-path=/", "http://127.0.0.1:$($ports.caddy_port)")
        if ($code -ne 0) { throw "Tailscale Serve could not enable HTTPS. Complete any HTTPS approval shown above, then run Setup / repair again." }
    }
    Assert-SetuoraServeRoute (Get-SetuoraServeConfig $tailscale) $tailnetHost $true $ports.caddy_port
    Write-Host "Staff HTTPS address: https://$tailnetHost" -ForegroundColor Green
    Write-Host "Join each staff PC to this tailnet, then open this address for camera scanning."
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

function Get-SetuoraPrivateUrl {
    $tailscale = Get-SetuoraTailscale
    if (-not $tailscale) { throw "Tailscale is missing. Run Setup / repair." }
    $state = Get-SetuoraTailnetState $tailscale
    if ($state.BackendState -ne "Running" -or -not $state.Self.Online -or -not $state.Self.DNSName) {
        throw "Tailscale is offline. Connect this computer to the tailnet."
    }
    return "https://$($state.Self.DNSName.TrimEnd('.').ToLowerInvariant())"
}

function Get-SetuoraServeConfig([string]$Tailscale) {
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & $Tailscale serve status --json 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
    if ($code -ne 0) { throw "Tailscale Serve status failed (exit $code): $($output -join ' ')" }
    try {
        $json = ($output -join [Environment]::NewLine).Trim()
        if (-not $json -or $json -eq "null") { return [pscustomobject]@{} }
        return ($json | ConvertFrom-Json)
    } catch { throw "Tailscale returned unreadable Serve settings. Setup stopped to avoid replacing another route." }
}

function Assert-SetuoraServeRoute([object]$Config, [string]$TailnetName, [bool]$MustExist, [int]$CaddyPort = 0, [int]$PreviousPort = 0) {
    if ($CaddyPort -eq 0) { $CaddyPort = (Get-SetuoraSavedPorts).caddy_port }
    $routeKey = "$($TailnetName):443"
    if ($Config.AllowFunnel) {
        foreach ($entry in $Config.AllowFunnel.PSObject.Properties) {
            if ($entry.Name -match ':443$' -and $entry.Value) {
                throw "Tailscale Funnel is enabled on HTTPS port 443. Turn Funnel off before serving private Lite data."
            }
        }
    }
    if ($Config.TCP) {
        foreach ($entry in $Config.TCP.PSObject.Properties) {
            if ($entry.Name -match '(^|:)443$') { throw "Tailscale port 443 is already used by a TCP route." }
        }
    }
    $route = $null
    if ($Config.Web) {
        foreach ($entry in $Config.Web.PSObject.Properties) {
            if ($entry.Name -match ':443$') {
                if ($entry.Name -ne $routeKey) { throw "Tailscale HTTPS port 443 is already used by another route." }
                $route = $entry.Value
            }
        }
    }
    if (-not $route) {
        if ($MustExist) { throw "The Setuora Lite Tailscale Serve route is missing. Run Setup / repair." }
        return
    }
    $rootHandler = if ($route.Handlers) { $route.Handlers.PSObject.Properties['/'] } else { $null }
    if (-not $rootHandler) {
        if ($MustExist) { throw "The Setuora Lite Tailscale Serve root route is missing. Run Setup / repair." }
        return
    }
    $allowed = @("http://127.0.0.1:$CaddyPort")
    if ($PreviousPort -gt 0) { $allowed += "http://127.0.0.1:$PreviousPort" }
    if ($rootHandler.Value.Proxy -notin $allowed) {
        throw "Tailscale HTTPS root has a different Serve route. Setup stopped without overwriting it."
    }
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

function Test-SetuoraTailnetReady([switch]$SkipMasterCheck) {
    $tailscale = Get-SetuoraTailscale
    if (-not $tailscale) {
        throw "Setuora Lite is running locally, but Tailscale is missing. Run Setup / repair to enable Master sync."
    }
    $state = Get-SetuoraTailnetState $tailscale
    if ($state.BackendState -ne "Running" -or -not $state.Self -or -not $state.Self.Online -or -not $state.Self.DNSName) {
        throw "Setuora Lite is running locally, but Tailscale is offline. Master sync will wait. Check the network or run Setup / repair."
    }
    Write-Host "Tailscale is online for Master sync."
    $tailnetName = $state.Self.DNSName.TrimEnd('.').ToLowerInvariant()
    if (-not $SkipMasterCheck) {
        Assert-SetuoraServeRoute (Get-SetuoraServeConfig $tailscale) $tailnetName $true
    }
    try {
        $health = Invoke-RestMethod -Uri "https://$tailnetName/health" -TimeoutSec 10
        if ($health.status -ne "ok" -or $health.role -ne "lite") { throw "Wrong service answered." }
    } catch { throw "Private HTTPS is not healthy. Check the Caddy task and Tailscale Serve; run Setup / repair." }
    Write-Host "Lite private HTTPS: https://$tailnetName"
    if ($SkipMasterCheck) { return }
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
    $code = Invoke-SetuoraDeployment "backup"
    if ($code -ne 0) { throw "A verified SQLite backup could not be created. The update was not started." }
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
    Initialize-SetuoraPrivateWeb -Upgrade
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
    Write-Host "Commands: setup, start, stop, status, open, logs, preflight, update, uninstall, help"
    Write-Host "Setup, Start, Stop, Check configuration and Update request Administrator access."
    Write-Host "Source updates use Git. Installed copies ask you to choose a downloaded installer."
    Write-Host "Browser: $BrowserUrl"
}

function Invoke-SetuoraCommand([string]$Action, [string[]]$ExtraArguments = @()) {
    if ($Action -in @("setup", "start", "stop", "preflight", "update", "update-runtime", "uninstall", "logs")) {
        if (-not (Test-SetuoraAdministrator)) { return Invoke-SetuoraElevated $Action $ExtraArguments }
    }
    switch ($Action) {
        "help" { Show-SetuoraHelp; return 0 }
        "uninstall" {
            $scriptPath = Join-Path $ApplicationRoot 'scripts\windows\uninstall.ps1'
            if (-not (Test-Path -LiteralPath $scriptPath)) { throw 'The removal script is missing. Update this installation before removing it.' }
            Set-Location -LiteralPath $env:ProgramData
            $code = Invoke-SetuoraNative 'powershell.exe' @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $scriptPath, '-Product', 'Lite')
            if ($code -eq 0) { $script:Uninstalled = $true }
            return $code
        }
        "setup" {
            $code = Invoke-SetuoraDeployment "setup" $ExtraArguments
            if ($code -ne 0) { return $code }
            Initialize-SetuoraTailnet
            Initialize-SetuoraPrivateWeb
            Test-SetuoraTailnetReady
            return 0
        }
        "start" {
            $code = Invoke-SetuoraDeployment "start" $ExtraArguments
            if ($code -ne 0) { return $code }
            Initialize-SetuoraPrivateWeb
            Test-SetuoraTailnetReady
            return 0
        }
        "status" {
            $code = Invoke-SetuoraDeployment "status" $ExtraArguments
            if ($code -ne 0) { return $code }
            Test-SetuoraTailnetReady -SkipMasterCheck
            return 0
        }
        "open" {
            $code = Invoke-SetuoraDeployment "status"
            if ($code -ne 0) { return $code }
            Test-SetuoraTailnetReady -SkipMasterCheck
            Start-Process -FilePath (Get-SetuoraPrivateUrl)
            return 0
        }
        "update" {
            if (Test-Path -LiteralPath (Join-Path $ApplicationRoot ".git")) { return Update-SetuoraSource }
            return Install-SetuoraUpdate
        }
        "update-runtime" {
            $code = Invoke-SetuoraDeployment "update"
            if ($code -ne 0) { return $code }
            Initialize-SetuoraPrivateWeb -Upgrade
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
        Write-Host "  [9] Remove this installation (preserve recovery backup)"
        Write-Host "  [0] Exit"
        Write-Host ""
        Write-Host "  Closing this menu leaves Setuora running."
        $selection = Read-Host "Choose an option (0-9)"
        $action = switch ($selection) {
            "1" { "open" }; "2" { "start" }; "3" { "stop" }; "4" { "status" }
            "5" { "setup" }; "6" { "update" }; "7" { "logs" }; "8" { "preflight" }; "9" { "uninstall" }
            "0" { return 0 }
            default { "" }
        }
        if (-not $action) {
            Write-Host "Choose a number from 0 to 9." -ForegroundColor Yellow
        } else {
            try {
                $code = Invoke-SetuoraCommand $action
                if ($action -eq 'uninstall' -and $code -eq 0) { return 0 }
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
if ($PauseAfter -and -not $script:Uninstalled) { $null = Read-Host "Press Enter to close this Administrator window" }
exit $exitCode
