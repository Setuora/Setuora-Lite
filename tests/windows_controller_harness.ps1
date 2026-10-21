param(
    [Parameter(Mandatory = $true)][string]$Controller,
    [Parameter(Mandatory = $true)][string]$Case
)
$ErrorActionPreference = 'Stop'
$parseErrors = $null
$tokens = $null
$tree = [System.Management.Automation.Language.Parser]::ParseFile($Controller, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw ($parseErrors -join [Environment]::NewLine) }
$definitions = $tree.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)
foreach ($definition in $definitions) { Invoke-Expression $definition.Extent.Text }
$originalElevate = (Get-Command Invoke-SetuoraElevated).ScriptBlock
$originalUpdate = (Get-Command Update-SetuoraCheckout).ScriptBlock
$originalCommand = (Get-Command Invoke-SetuoraCommand).ScriptBlock
$originalTailnet = (Get-Command Initialize-SetuoraTailnet).ScriptBlock
$originalTailnetCheck = (Get-Command Test-SetuoraTailnetReady).ScriptBlock
$originalServeRoute = (Get-Command Assert-SetuoraServeRoute).ScriptBlock
$ApplicationRoot = [IO.Path]::GetTempPath()
$ControllerPath = "C:\Warehouse & Team\O'Neil\setuora.ps1"
$ProductName = 'Setuora test'
$BrowserUrl = 'https://lite.tailnet.ts.net'
$Command = 'menu'
$Elevated = $false
$script:Calls = New-Object System.Collections.Generic.List[string]
$script:Admin = $false
$script:IsSource = $false
$script:DeployExit = 0
$script:FailStartProcess = $false
$script:DirtySource = $false
$script:FailStop = $false
$script:FailMerge = $false
$script:FailUpdate = $false
$script:FailFetch = $false
$script:Diverged = $false
$script:Latest = 'bbb'
$script:ElevateExit = 17
$script:InstalledRoot = $ApplicationRoot

function Assert-Equal($Actual, $Expected, [string]$Message) {
    if (($Actual -join '|') -ne ($Expected -join '|')) { throw "$Message. Expected [$($Expected -join '|')], got [$($Actual -join '|')]" }
}
function Reset-Calls { $script:Calls.Clear() }
function Test-SetuoraAdministrator { return $script:Admin }
function Test-Path([string]$LiteralPath) { return $script:IsSource }
function Invoke-SetuoraDeployment([string]$Action, [string[]]$ExtraArguments = @()) {
    $script:Calls.Add("deploy:$Action")
    if ($Action -eq 'stop' -and $script:FailStop) { return 5 }
    if ($Action -eq 'update' -and $script:FailUpdate) { return 8 }
    return $script:DeployExit
}
function Initialize-SetuoraTailnet { $script:Calls.Add('tailnet:setup') }
function Initialize-SetuoraPrivateWeb { $script:Calls.Add('private:setup') }
function Test-SetuoraTailnetReady([switch]$SkipMasterCheck) { $script:Calls.Add('tailnet:check') }
function Get-SetuoraPrivateUrl { return $BrowserUrl }
function Invoke-SetuoraElevated([string]$Action, [string[]]$ExtraArguments = @()) {
    $script:Calls.Add("elevate:$Action")
    return $script:ElevateExit
}
function Update-SetuoraCheckout { $script:Calls.Add('source-update'); return $false }
function Get-SetuoraInstalledRoot { return $script:InstalledRoot }
function Invoke-SetuoraInteractive([string]$Script, [string[]]$Arguments = @()) {
    $script:Calls.Add("run:" + (Split-Path -Leaf $Script) + ":" + ($Arguments -join ' '))
    return 0
}
function Disable-SetuoraServeRoute { $script:Calls.Add('serve:off') }
function Stop-SetuoraCaddy { $script:Calls.Add('caddy:stop') }
function Get-ScheduledTask([string]$TaskName, $ErrorAction) { return $null }
function Install-SetuoraUpdate { $script:Calls.Add('choose-installer'); return 22 }
function Start-Process([string]$FilePath, [string]$ArgumentList, [string]$Verb, [switch]$Wait, [switch]$PassThru) {
    $script:Calls.Add("process:$FilePath")
    $script:LastArguments = $ArgumentList
    $script:LastVerb = $Verb
    if ($script:FailStartProcess) { throw 'Mock UAC cancellation' }
    if ($PassThru) { return [pscustomobject]@{ ExitCode = 27 } }
}
function Clear-Host {}
function Read-Host([string]$Prompt) {
    if ($script:Answers.Count -eq 0) { throw "Unexpected prompt: $Prompt" }
    return $script:Answers.Dequeue()
}

switch ($Case) {
    'parse' { if ($definitions.Count -lt 10) { throw 'Expected all shared-controller function definitions' } }
    'dispatch' {
        foreach ($action in @('setup', 'start', 'stop', 'preflight', 'update', 'update-runtime', 'uninstall', 'logs')) {
            Reset-Calls
            Assert-Equal (Invoke-SetuoraCommand $action) 17 "$action forwards elevation exit code"
            Assert-Equal $script:Calls @("elevate:$action") "$action requests elevation without deploying locally"
        }
        foreach ($action in @('status')) {
            Reset-Calls
            Assert-Equal (Invoke-SetuoraCommand $action) 0 "$action succeeds without elevation"
            $expected = if ($action -eq 'status') { @('deploy:status', 'tailnet:check') } else { @("deploy:$action") }
            Assert-Equal $script:Calls $expected "$action remains read-only"
        }
        $script:ElevateExit = 0
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'start') 0 'Elevated Start succeeds'
        Assert-Equal $script:Calls @('elevate:start', "process:$BrowserUrl") 'The original user opens the browser after an elevated Start'
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'stop') 0 'Elevated Stop succeeds'
        Assert-Equal $script:Calls @('elevate:stop') 'Stop does not open the browser'
        $script:ElevateExit = 17
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'help') 0 'Help succeeds without Python/admin'
        Assert-Equal $script:Calls @() 'Help does not invoke deployment'
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'open') 0 'Open succeeds on healthy service'
        Assert-Equal $script:Calls @('deploy:status', 'tailnet:check', "process:$BrowserUrl") 'Open checks private HTTPS first'
        $script:DeployExit = 9
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'open') 9 'Open forwards failed health status'
        Assert-Equal $script:Calls @('deploy:status') 'Open does not launch browser when stopped'
    }
    'elevation' {
        Set-Item Function:Invoke-SetuoraElevated -Value $originalElevate
        Assert-Equal (Invoke-SetuoraElevated 'setup') 27 'Elevated child exit code is preserved'
        Assert-Equal $script:LastVerb 'RunAs' 'UAC is requested'
        if (-not $script:LastArguments.Contains(('"' + $ControllerPath + '"'))) { throw 'Controller path must remain quoted' }
        if (-not $script:LastArguments.Contains('setup -Elevated -PauseAfter')) { throw 'Interactive elevated setup console must remain visible' }
        if ($script:LastArguments.Contains('>')) { throw 'Password prompt must not be redirected' }
        $script:FailStartProcess = $true
        Assert-Equal (Invoke-SetuoraElevated 'start') 1 'Cancelled UAC returns failure'
        $Elevated = $true
        $caught = $false
        try { $null = Invoke-SetuoraElevated 'start' } catch { $caught = $true }
        Assert-Equal $caught $true 'Missing admin rights after elevation cannot loop'
    }
    'routing' {
        $script:Admin = $true
        $setup = @('deploy:setup', 'tailnet:setup', 'private:setup', 'tailnet:check')
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'setup') 0 'Setup repairs in place'
        Assert-Equal $script:Calls $setup 'Setup runs the full Lite pipeline'
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'preflight') 0 'An administrator deploys directly'
        Assert-Equal $script:Calls @('deploy:preflight') 'No nested elevation'
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'start') 0 'Start installs or repairs in place'
        Assert-Equal $script:Calls ($setup + @("process:$BrowserUrl")) 'Installed Start repairs, runs and opens the browser'
        $Elevated = $true
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'start') 0 'Elevated child Start succeeds'
        Assert-Equal $script:Calls $setup 'The Administrator window never opens the browser'
        $Elevated = $false
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'stop') 0 'Stop succeeds'
        Assert-Equal $script:Calls @('serve:off', 'caddy:stop', 'deploy:stop') 'Stop removes the route and stops Caddy and Lite'
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'update') 22 'Installed update opens picker'
        Assert-Equal $script:Calls @('choose-installer') 'Installed update does not require Git'
        $script:IsSource = $true
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'start') 0 'Source Start succeeds'
        Assert-Equal $script:Calls (@('source-update') + $setup + @("process:$BrowserUrl")) 'Source Start updates the checkout before setup'
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'update') 0 'Source update is Start'
        Assert-Equal $script:Calls (@('source-update') + $setup) 'Source update does not use package picker'
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'update-runtime') 0 'Installer internal update succeeds'
        Assert-Equal $script:Calls @('deploy:update', 'private:setup', 'tailnet:check') 'Internal update cannot open a second installer'
    }
    'checkout-update' {
        Set-Item Function:Update-SetuoraCheckout -Value $originalUpdate
        function Get-Command([string]$Name, $ErrorAction) { return [pscustomobject]@{ Source = 'git.exe' } }
        function Write-Warning([string]$Message) { $script:Calls.Add('warn') }
        function Write-Host {}
        function Read-SetuoraGit([string[]]$Arguments) {
            $script:Calls.Add("git:$($Arguments[0])")
            if ($Arguments[0] -eq 'status' -and $script:DirtySource) { return ' M app/main.py' }
            if ($Arguments[0] -eq 'branch') { return 'main' }
            if ($Arguments[0] -eq 'fetch' -and $script:FailFetch) { throw 'Mock offline' }
            if ($Arguments[0] -eq 'rev-parse' -and $Arguments[1] -eq 'HEAD') { return 'aaa' }
            if ($Arguments[0] -eq 'rev-parse' -and $Arguments[1] -eq 'origin/main') { return $script:Latest }
            if ($Arguments[0] -eq 'merge-base' -and $script:Diverged) { throw 'Mock diverged' }
            if ($Arguments[0] -eq 'merge' -and $script:FailMerge) { throw 'Mock fast-forward failure' }
            return ''
        }
        $checked = @('git:status', 'git:branch', 'git:fetch', 'git:rev-parse', 'git:rev-parse', 'git:rev-parse')
        $script:IsSource = $true
        Assert-Equal (Update-SetuoraCheckout) $true 'A clean installed checkout updates'
        Assert-Equal $script:Calls ($checked + @('git:merge-base', 'deploy:preflight', 'deploy:backup', 'deploy:stop', 'git:merge')) 'Installed checkout backs up and stops before fast-forward'
        $script:DirtySource = $true
        Reset-Calls
        Assert-Equal (Update-SetuoraCheckout) $false 'Local changes keep the current commit'
        Assert-Equal $script:Calls @('git:status', 'warn') 'Local changes only warn'
        $script:DirtySource = $false
        $script:FailFetch = $true
        Reset-Calls
        Assert-Equal (Update-SetuoraCheckout) $false 'Unreachable Git keeps the current commit'
        Assert-Equal $script:Calls @('git:status', 'git:branch', 'git:fetch', 'warn') 'Unreachable Git only warns'
        $script:FailFetch = $false
        $script:Latest = 'aaa'
        Reset-Calls
        Assert-Equal (Update-SetuoraCheckout) $false 'An up-to-date checkout is not changed'
        Assert-Equal $script:Calls $checked 'An up-to-date checkout is left running until setup'
        $script:Latest = 'bbb'
        $script:Diverged = $true
        Reset-Calls
        Assert-Equal (Update-SetuoraCheckout) $false 'Diverged history keeps the current commit'
        Assert-Equal $script:Calls ($checked + @('git:merge-base', 'warn')) 'Diverged history only warns'
        $script:Diverged = $false
        $script:FailStop = $true
        Reset-Calls
        $caught = $false
        try { $null = Update-SetuoraCheckout } catch { $caught = $true }
        Assert-Equal $caught $true 'Failed stop fails Start'
        if ($script:Calls.Contains('git:merge')) { throw 'Cannot change source after a failed stop' }
        $script:FailStop = $false
        $script:FailMerge = $true
        Reset-Calls
        Assert-Equal (Update-SetuoraCheckout) $false 'Failed fast-forward lets setup restart the current version'
        Assert-Equal $script:Calls[$script:Calls.Count - 1] 'warn' 'Failed fast-forward warns'
        $script:FailMerge = $false
        $script:IsSource = $false
        Reset-Calls
        Assert-Equal (Update-SetuoraCheckout) $true 'A first install updates without a backup'
        Assert-Equal $script:Calls ($checked + @('git:merge-base', 'git:merge')) 'A first install has nothing to back up or stop'
    }
    'source-folder' {
        # Controls run from a downloaded folder manage the ProgramData installation.
        $script:Admin = $true
        $Elevated = $true
        $script:InstalledRoot = $null
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'start') 0 'First Start installs'
        Assert-Equal $script:Calls @('run:bootstrap.ps1:') 'First Start runs the bootstrap into ProgramData'
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'stop') 0 'Stop without an installation succeeds'
        Assert-Equal $script:Calls @() 'Nothing is stopped when nothing is installed'
        $script:InstalledRoot = Join-Path ([IO.Path]::GetTempPath()) 'Setuora-Lite-installed'
        foreach ($action in @('start', 'stop', 'uninstall')) {
            Reset-Calls
            Assert-Equal (Invoke-SetuoraCommand $action) 0 "$action reaches the installation"
            Assert-Equal $script:Calls @("run:setuora.ps1:$action -Elevated") "$action runs the installed controls"
        }
    }
    'tailnet' {
        Set-Item Function:Initialize-SetuoraTailnet -Value $originalTailnet
        Set-Item Function:Test-SetuoraTailnetReady -Value $originalTailnetCheck
        function Get-SetuoraTailscale { return 'C:\Program Files\Tailscale\tailscale.exe' }
        function Get-Service([string]$Name, $ErrorAction) {
            $script:Calls.Add("service:get:$Name")
            return [pscustomobject]@{ Status = 'Stopped' }
        }
        function Set-Service([string]$Name, [string]$StartupType) {
            $script:Calls.Add("service:auto:$Name")
        }
        function Start-Service([string]$Name) { $script:Calls.Add("service:start:$Name") }
        function Invoke-SetuoraNative([string]$File, [string[]]$Arguments) {
            $script:Calls.Add("tailscale:$($Arguments[0])")
            if ($script:NativeFail -and $Arguments[0] -eq 'up') { return 1 }
            return 0
        }
        function Get-SetuoraTailnetState([string]$Tailscale) {
            $script:Calls.Add('tailscale:status')
            return [pscustomobject]@{
                BackendState = $script:TailnetState
                Self = [pscustomobject]@{ Online = $true; DNSName = 'lite.tailnet.ts.net.' }
            }
        }
        function Get-SetuoraServeConfig([string]$Tailscale) { $script:Calls.Add('tailscale:serve-status'); return [pscustomobject]@{} }
        function Assert-SetuoraServeRoute([object]$Config, [string]$TailnetName, [bool]$MustExist) { $script:Calls.Add('tailscale:serve-check') }
        function Invoke-RestMethod([string]$Uri, [int]$TimeoutSec) { $script:Calls.Add('https:health'); return [pscustomobject]@{ status='ok'; role='lite' } }
        $script:NativeFail = $false
        $script:TailnetState = 'Running'
        Initialize-SetuoraTailnet
        Assert-Equal $script:Calls @(
            'service:get:Tailscale', 'service:auto:Tailscale', 'service:start:Tailscale',
            'tailscale:status', 'tailscale:set', 'tailscale:set', 'tailscale:status'
        ) 'Setup preserves existing Tailscale settings and checks that it is online'
        Reset-Calls
        $script:NativeFail = $true
        $script:TailnetState = 'Stopped'
        $caught = $false
        try { Initialize-SetuoraTailnet } catch { $caught = $true }
        Assert-Equal $caught $true 'Failed Tailscale login stops setup'
        Assert-Equal $script:Calls @(
            'service:get:Tailscale', 'service:auto:Tailscale', 'service:start:Tailscale',
            'tailscale:status', 'tailscale:up'
        ) 'Failed Tailscale login cannot report connected'
        Reset-Calls
        $caught = $false
        try { Test-SetuoraTailnetReady } catch { $caught = $true }
        Assert-Equal $caught $true 'Offline tailnet is reported after local app startup'
        Assert-Equal $script:Calls @('tailscale:status') 'Readiness check does not mutate or disconnect Tailscale'
        Reset-Calls
        $script:TailnetState = 'Running'
        Test-SetuoraTailnetReady
        Assert-Equal $script:Calls @('tailscale:status', 'tailscale:serve-status', 'tailscale:serve-check', 'https:health', 'deploy:master-check') 'Online tailnet checks private HTTPS and Master route'
        Reset-Calls
        Test-SetuoraTailnetReady -SkipMasterCheck
        Assert-Equal $script:Calls @('tailscale:status', 'https:health') 'Read-only status checks HTTPS without reading protected settings'
    }
    'serve-route' {
        Set-Item Function:Assert-SetuoraServeRoute -Value $originalServeRoute
        $handlers = [pscustomobject]@{
            '/' = [pscustomobject]@{ Proxy = 'http://127.0.0.1:8081' }
            '/api/v1/' = [pscustomobject]@{ Proxy = 'http://127.0.0.1:8000' }
        }
        $config = [pscustomobject]@{ TCP = [pscustomobject]@{
            '443' = [pscustomobject]@{ HTTPS = $true }
        }; Web = [pscustomobject]@{
            'lite.tailnet.ts.net:443' = [pscustomobject]@{ Handlers = $handlers }
        } }
        Assert-SetuoraServeRoute $config 'lite.tailnet.ts.net' $true 8081
        $config.TCP.PSObject.Properties['443'].Value = [pscustomobject]@{ TCPForward = '127.0.0.1:9000' }
        $caught = $false
        try { Assert-SetuoraServeRoute $config 'lite.tailnet.ts.net' $true 8081 } catch { $caught = $true }
        Assert-Equal $caught $true 'A TCP forward on HTTPS port must be rejected'
        $config.TCP.PSObject.Properties['443'].Value = [pscustomobject]@{ HTTPS = $true }
        $handlers.PSObject.Properties['/'].Value.Proxy = 'http://127.0.0.1:9999'
        $caught = $false
        try { Assert-SetuoraServeRoute $config 'lite.tailnet.ts.net' $true 8081 } catch { $caught = $true }
        Assert-Equal $caught $true 'A foreign root route must not be overwritten'
        $handlers.PSObject.Properties.Remove('/')
        Assert-SetuoraServeRoute $config 'lite.tailnet.ts.net' $false 8081
        $caught = $false
        try { Assert-SetuoraServeRoute $config 'lite.tailnet.ts.net' $true 8081 } catch { $caught = $true }
        Assert-Equal $caught $true 'Missing Lite root must fail status while Master route remains'
    }
    'saved-ports' {
        $script:IsSource = $true
        $ApplicationRoot = Join-Path ([IO.Path]::GetTempPath()) ('setuora-ports-' + [guid]::NewGuid().ToString('N'))
        [IO.Directory]::CreateDirectory($ApplicationRoot) | Out-Null
        try {
            [IO.File]::WriteAllText((Join-Path $ApplicationRoot '.runtime-ports.json'), '{"web_port":8001,"caddy_port":8081}')
            $ports = Get-SetuoraSavedPorts
            Assert-Equal $ports.web_port 8001 'Saved app port is readable through PowerShell JSON'
            Assert-Equal $ports.caddy_port 8081 'Saved Caddy port is readable through PowerShell JSON'
        } finally { Remove-Item -LiteralPath $ApplicationRoot -Recurse -Force }
    }
    'menu' {
        function Invoke-SetuoraCommand([string]$Action) {
            $script:Calls.Add("menu:$Action")
            if ($Action -eq 'start' -and $script:FailMenu) { throw 'Controlled action error' }
            if ($Action -eq 'uninstall') { return $script:UninstallExit }
            return 0
        }
        $script:FailMenu = $false
        $script:UninstallExit = 0
        $script:Answers = New-Object System.Collections.Generic.Queue[string]
        $script:Answers.Enqueue('3')
        Assert-Equal (Show-SetuoraMenu) 0 'Successful uninstall exits the menu before its folder is removed'
        Assert-Equal $script:Calls @('menu:uninstall') 'Uninstall is option 3'
        Reset-Calls
        $script:Answers.Enqueue('$(throw "must remain input")')
        $script:Answers.Enqueue('0')
        $script:Answers.Enqueue('3')
        Assert-Equal (Show-SetuoraMenu) 0 'Invalid menu input is handled as plain text'
        Assert-Equal $script:Calls @('menu:uninstall') 'Invalid menu input cannot execute commands'
        Reset-Calls
        foreach ($answer in @('1', '', '2', '', '3')) { $script:Answers.Enqueue($answer) }
        Assert-Equal (Show-SetuoraMenu) 0 'Menu selections execute and return to menu'
        Assert-Equal $script:Calls @('menu:start', 'menu:stop', 'menu:uninstall') 'Every menu selection dispatches the expected action'
        Reset-Calls
        $script:FailMenu = $true
        $script:UninstallExit = 1
        foreach ($answer in @('1', '', '3', '')) { $script:Answers.Enqueue($answer) }
        $caught = $false
        try { $null = Show-SetuoraMenu } catch { $caught = $_.Exception.Message -like 'Unexpected prompt*' }
        Assert-Equal $caught $true 'Failed actions return to the menu'
        Assert-Equal $script:Calls @('menu:start', 'menu:uninstall') 'Only selected actions run after menu errors'
    }
    default { throw "Unknown harness case: $Case" }
}
Write-Output "PASS:$Case"
