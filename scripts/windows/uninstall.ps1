[CmdletBinding()]
param([Parameter(Mandatory = $true)][ValidateSet('Master', 'Lite')][string]$Product)

$ErrorActionPreference = 'Stop'
$InstallRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..')).TrimEnd('\')
$ExpectedRoots = @(
    (Join-Path $env:ProgramData "Setuora\Setuora-$Product"),
    (Join-Path $env:ProgramData "Setuora\Setuora-$Product-windows")
) | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\') }
$RecoveryRoot = Join-Path $env:ProgramData 'Setuora\Recovery'
$SharedRoot = Join-Path $env:ProgramData 'Setuora'
$TaskName = "Setuora-$Product"
$CaddyTaskName = 'Setuora-Lite-Caddy'
$CaddyRoot = Join-Path $env:ProgramData 'Setuora\caddy-lite'

function Assert-PlainDirectory([string]$Path) {
    if ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "A linked folder cannot be used for recovery or removal: $Path"
    }
}

function Protect-RecoveryDirectory([string]$Path) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    Assert-PlainDirectory $Path
    $oldPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & icacls.exe $Path /inheritance:r /remove:g '*S-1-1-0' '*S-1-5-11' '*S-1-5-32-545' /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' /Q /L | Out-Null
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $oldPreference }
    if ($code -ne 0) { throw "Recovery folder permissions could not be secured: $Path" }
}

function Get-LiteCaddyPort {
    $envFile = Join-Path $InstallRoot '.env'
    if (-not (Test-Path -LiteralPath $envFile)) { return 8080 }
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*SETUORA_CADDY_PORT\s*=\s*["'']?([0-9]+)["'']?\s*$') {
            $port = [int]$Matches[1]
            if ($port -ge 1 -and $port -le 65535) { return $port }
        }
    }
    return 8080
}

function Find-Tailscale {
    $path = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
    if (Test-Path -LiteralPath $path) { return $path }
    if (${env:ProgramFiles(x86)}) {
        $path = Join-Path ${env:ProgramFiles(x86)} 'Tailscale\tailscale.exe'
        if (Test-Path -LiteralPath $path) { return $path }
    }
    $found = Get-Command tailscale.exe -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    return $null
}

function Read-TailscaleJson([string]$Executable, [string[]]$Arguments) {
    $oldPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $Executable @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $oldPreference }
    if ($code -ne 0) { throw "Tailscale settings could not be checked. Connect Tailscale, then retry uninstall." }
    $body = ($output -join [Environment]::NewLine).Trim()
    if (-not $body -or $body -eq 'null') { return [pscustomobject]@{} }
    try { return ($body | ConvertFrom-Json) }
    catch { throw 'Tailscale returned unreadable Serve settings; removal was cancelled.' }
}

function Find-ProductPython {
    $candidates = @(
        (Join-Path $InstallRoot '.venv\Scripts\python.exe'),
        (Join-Path $env:ProgramFiles 'Python313\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python311\python.exe')
    )
    $onPath = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $oldPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & $candidate -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } finally { $ErrorActionPreference = $oldPreference }
    }
    return $null
}

function Remove-LiteServeRoute {
    $tailscale = Find-Tailscale
    if (-not $tailscale) { throw 'Tailscale is missing; the private HTTPS route cannot be checked. Repair Tailscale, then retry uninstall.' }
    $state = Read-TailscaleJson $tailscale @('status', '--json')
    if (-not $state.Self.DNSName) { throw 'Tailscale has no private hostname. Retry uninstall after reconnecting.' }
    $tailnetHost = $state.Self.DNSName.TrimEnd('.').ToLowerInvariant()
    $serve = Read-TailscaleJson $tailscale @('serve', 'status', '--json')
    $siteKey = "${tailnetHost}:443"
    $site = if ($serve.Web) { $serve.Web.PSObject.Properties[$siteKey].Value } else { $null }
    $handler = if ($site -and $site.Handlers) { $site.Handlers.PSObject.Properties['/'].Value } else { $null }
    if (-not $handler) { return }
    $expectedProxy = "http://127.0.0.1:$(Get-LiteCaddyPort)"
    if ($handler.Proxy -ne $expectedProxy) {
        throw "HTTPS / points to another service. Uninstall left that route untouched: $($handler.Proxy)"
    }
    & $tailscale serve --https=443 --set-path=/ off | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'The Lite HTTPS route could not be removed. Retry uninstall.' }
    $after = Read-TailscaleJson $tailscale @('serve', 'status', '--json')
    $afterSite = if ($after.Web) { $after.Web.PSObject.Properties[$siteKey].Value } else { $null }
    if ($afterSite -and $afterSite.Handlers -and $afterSite.Handlers.PSObject.Properties['/']) {
        throw 'The Lite HTTPS route is still present; removal was cancelled.'
    }
}

function Assert-MasterServeRouteCleared {
    $tailscale = Find-Tailscale
    if (-not $tailscale) { throw 'Tailscale is missing; the private API route cannot be checked. Repair Tailscale, then retry uninstall.' }
    $serve = Read-TailscaleJson $tailscale @('serve', 'status', '--json')
    if (-not $serve.Web) { return }
    foreach ($site in $serve.Web.PSObject.Properties) {
        if ($site.Name -notmatch ':443$') { continue }
        $handlers = $site.Value.Handlers
        if ($handlers -and $handlers.PSObject.Properties['/api/v1/']) {
            throw 'The Master API route is still present in Tailscale Serve; removal was cancelled.'
        }
    }
}

function Remove-TaskIfPresent([string]$Name) {
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if (-not $task) { return }
    Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction Stop
}

$StopAttempted = $false
$CleanupStarted = $false
try {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Approve the Administrator prompt before removing Setuora.'
    }
    if ($InstallRoot -notin $ExpectedRoots) {
        throw "Uninstall is available only from a ProgramData installation, not $InstallRoot"
    }
    Assert-PlainDirectory $SharedRoot
    Assert-PlainDirectory $InstallRoot
    Write-Host "This removes the $Product application and its startup task from $InstallRoot." -ForegroundColor Yellow
    Write-Host 'A verified database backup and recovery settings will be kept outside that folder.'
    $answer = Read-Host "Type REMOVE $Product to continue"
    if ($answer -cne "REMOVE $Product") { Write-Host 'Removal cancelled.'; exit 1 }

    $python = Find-ProductPython
    $deploy = Join-Path $InstallRoot 'deploy.py'
    $preparer = Join-Path $PSScriptRoot 'prepare-recovery.py'
    $finisher = Join-Path $PSScriptRoot 'finish-uninstall.ps1'
    if (-not $python -or -not (Test-Path -LiteralPath $deploy) -or
        -not (Test-Path -LiteralPath $preparer) -or -not (Test-Path -LiteralPath $finisher)) {
        throw 'Python or the installed controls are missing. Run Setup / repair, then retry removal so a verified backup can be kept.'
    }

    Write-Host "Stopping Setuora $Product..."
    Push-Location -LiteralPath $InstallRoot
    try {
        $StopAttempted = $true
        & $python $deploy stop
        if ($LASTEXITCODE -ne 0) { throw 'The application could not be stopped safely. Nothing was removed.' }
    } finally { Pop-Location }

    $productRecovery = Join-Path $RecoveryRoot $Product
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')
    $bundle = Join-Path $productRecovery ("$stamp-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    foreach ($path in @($RecoveryRoot, $productRecovery, $bundle)) { Protect-RecoveryDirectory $path }
    Push-Location -LiteralPath $InstallRoot
    try {
        & $python $preparer --root $InstallRoot --destination $bundle --product $Product
        if ($LASTEXITCODE -ne 0) { throw 'A verified recovery bundle could not be created. Application files were left in place.' }
    } finally { Pop-Location }
    Protect-RecoveryDirectory $bundle

    if ($Product -eq 'Lite') {
        Remove-LiteServeRoute
        Remove-TaskIfPresent $CaddyTaskName
        if (Test-Path -LiteralPath $CaddyRoot) {
            Assert-PlainDirectory $CaddyRoot
            for ($attempt = 0; $attempt -lt 30; $attempt++) {
                $owned = @(Get-CimInstance Win32_Process -Filter "Name='caddy.exe'" -ErrorAction SilentlyContinue |
                    Where-Object { $_.ExecutablePath -eq (Join-Path $CaddyRoot 'caddy.exe') })
                if ($owned.Count -eq 0) { break }
                if ($attempt -eq 29) { throw 'Lite Caddy did not stop. Application files were left in place.' }
                Start-Sleep -Seconds 1
            }
        }
        Get-NetFirewallRule -Name 'Setuora-Lite-LAN' -ErrorAction SilentlyContinue |
            Remove-NetFirewallRule -ErrorAction Stop
    } else {
        Assert-MasterServeRouteCleared
    }
    Remove-TaskIfPresent $TaskName

    $cleanup = Join-Path $bundle 'finish-uninstall.ps1'
    Copy-Item -LiteralPath $finisher -Destination $cleanup
    Protect-RecoveryDirectory $bundle
    Set-Location -LiteralPath $bundle
    $arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + $cleanup +
        '" -Product ' + $Product + ' -InstallRoot "' + $InstallRoot +
        '" -WaitForPid ' + $PID
    Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -WorkingDirectory $bundle -WindowStyle Hidden
    $CleanupStarted = $true
    Write-Host "Recovery saved to $bundle" -ForegroundColor Green
    Write-Host 'The control window will close, then application files will be removed.'
    Write-Host "Removal result will be written to $(Join-Path $bundle 'removal-status.txt')."
    exit 0
} catch {
    $failure = $_.Exception.Message
    Write-Host $failure -ForegroundColor Red
    if ($StopAttempted -and -not $CleanupStarted -and (Test-Path -LiteralPath $InstallRoot)) {
        $controller = Join-Path $InstallRoot 'client\windows\setuora.ps1'
        if (-not (Test-Path -LiteralPath $controller)) {
            $controller = Join-Path $InstallRoot 'setuora.ps1'
        }
        if (Test-Path -LiteralPath $controller) {
            Write-Host "Attempting to restore Setuora $Product after cancelled removal..." -ForegroundColor Yellow
            try {
                Set-Location -LiteralPath $env:ProgramData
                $oldPreference = $ErrorActionPreference
                try {
                    $ErrorActionPreference = 'Continue'
                    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $controller start
                    $restored = $LASTEXITCODE -eq 0
                    if (-not $restored) {
                        & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $controller setup
                        $restored = $LASTEXITCODE -eq 0
                    }
                } finally { $ErrorActionPreference = $oldPreference }
                if (-not $restored) {
                    Write-Host 'Automatic restart failed. Open setuora.bat and choose Setup / repair.' -ForegroundColor Yellow
                }
            } catch {
                Write-Host 'Automatic restart failed. Open setuora.bat and choose Setup / repair.' -ForegroundColor Yellow
            }
        }
    }
    Write-Host 'Application files remain available. Check the message above before retrying removal.'
    exit 1
}
