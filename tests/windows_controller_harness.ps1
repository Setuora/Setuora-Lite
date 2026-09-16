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
$originalUpdate = (Get-Command Update-SetuoraSource).ScriptBlock
$originalCommand = (Get-Command Invoke-SetuoraCommand).ScriptBlock
$ApplicationRoot = [IO.Path]::GetTempPath()
$ControllerPath = "C:\Warehouse & Team\O'Neil\setuora.ps1"
$ProductName = 'Setuora test'
$BrowserUrl = 'http://127.0.0.1:8000'
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

function Assert-Equal($Actual, $Expected, [string]$Message) {
    if (($Actual -join '|') -ne ($Expected -join '|')) { throw "$Message. Expected [$($Expected -join '|')], got [$($Actual -join '|')]" }
}
function Reset-Calls { $script:Calls.Clear() }
function Test-SetuoraAdministrator { return $script:Admin }
function Test-Path([string]$LiteralPath) { return $script:IsSource }
function Invoke-SetuoraDeployment([string]$Action, [string[]]$ExtraArguments = @()) {
    $script:Calls.Add("deploy:$Action")
    if ($Action -eq 'stop' -and $script:FailStop) { return 5 }
    return $script:DeployExit
}
function Invoke-SetuoraElevated([string]$Action, [string[]]$ExtraArguments = @()) {
    $script:Calls.Add("elevate:$Action")
    return 17
}
function Update-SetuoraSource { $script:Calls.Add('source-update'); return 21 }
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
        foreach ($action in @('setup', 'start', 'stop', 'preflight', 'update', 'update-runtime')) {
            Reset-Calls
            Assert-Equal (Invoke-SetuoraCommand $action) 17 "$action forwards elevation exit code"
            Assert-Equal $script:Calls @("elevate:$action") "$action requests elevation without deploying locally"
        }
        foreach ($action in @('status', 'logs')) {
            Reset-Calls
            Assert-Equal (Invoke-SetuoraCommand $action) 0 "$action succeeds without elevation"
            Assert-Equal $script:Calls @("deploy:$action") "$action remains read-only"
        }
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'help') 0 'Help succeeds without Python/admin'
        Assert-Equal $script:Calls @() 'Help does not invoke deployment'
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'open') 0 'Open succeeds on healthy service'
        Assert-Equal $script:Calls @('deploy:status', "process:$BrowserUrl") 'Open checks health first'
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
        foreach ($action in @('setup', 'start', 'stop', 'preflight')) {
            Reset-Calls
            Assert-Equal (Invoke-SetuoraCommand $action) 0 'An administrator deploys directly'
            Assert-Equal $script:Calls @("deploy:$action") 'No nested elevation'
        }
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'update') 22 'Installed update opens picker'
        Assert-Equal $script:Calls @('choose-installer') 'Installed update does not require Git'
        $script:IsSource = $true
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'update') 21 'Source update uses Git workflow'
        Assert-Equal $script:Calls @('source-update') 'Source update does not use package picker'
        Reset-Calls
        Assert-Equal (Invoke-SetuoraCommand 'update-runtime') 0 'Installer internal update succeeds'
        Assert-Equal $script:Calls @('deploy:update') 'Internal update cannot open a second installer'
    }
    'source-update' {
        Set-Item Function:Update-SetuoraSource -Value $originalUpdate
        function Get-Command([string]$Name, $ErrorAction) { return [pscustomobject]@{ Source = 'git.exe' } }
        function Read-SetuoraGit([string[]]$Arguments) {
            $script:Calls.Add("git:$($Arguments[0])")
            if ($Arguments[0] -eq 'branch') { return 'main' }
            if ($Arguments[0] -eq 'status' -and $script:DirtySource) { return ' M app/main.py' }
            if ($Arguments[0] -eq 'merge' -and $script:FailMerge) { throw 'Mock fast-forward failure' }
            return ''
        }
        Assert-Equal (Update-SetuoraSource) 0 'Clean source update succeeds'
        Assert-Equal $script:Calls @('deploy:preflight', 'git:rev-parse', 'git:status', 'git:branch', 'git:fetch', 'git:rev-parse', 'git:merge-base', 'deploy:stop', 'git:merge', 'deploy:update') 'Source update order'
        $script:DirtySource = $true
        Reset-Calls
        $caught = $false
        try { $null = Update-SetuoraSource } catch { $caught = $true }
        Assert-Equal $caught $true 'Dirty source update refuses changes'
        Assert-Equal $script:Calls @('deploy:preflight', 'git:rev-parse', 'git:status') 'Dirty source leaves running app alone'
        $script:DirtySource = $false
        $script:FailStop = $true
        Reset-Calls
        Assert-Equal (Update-SetuoraSource) 5 'Failed stop aborts update'
        if ($script:Calls.Contains('git:merge') -or $script:Calls.Contains('deploy:update')) { throw 'Cannot mutate source after failed stop' }
        $script:FailStop = $false
        $script:FailMerge = $true
        Reset-Calls
        $caught = $false
        try { $null = Update-SetuoraSource } catch { $caught = $true }
        Assert-Equal $caught $true 'Failed merge reports failure'
        Assert-Equal $script:Calls[$script:Calls.Count - 1] 'deploy:start' 'Failed merge attempts to restart previous app'
    }
    'menu' {
        function Invoke-SetuoraCommand([string]$Action) {
            $script:Calls.Add("menu:$Action")
            if ($script:FailMenu) { throw 'Controlled action error' }
            return 0
        }
        $script:FailMenu = $false
        $script:Answers = New-Object System.Collections.Generic.Queue[string]
        $script:Answers.Enqueue('0')
        Assert-Equal (Show-SetuoraMenu) 0 'Exit closes menu cleanly'
        Assert-Equal $script:Calls @() 'Exit never invokes deployment'
        $script:Answers.Enqueue('$(throw "must remain input")')
        $script:Answers.Enqueue('')
        $script:Answers.Enqueue('0')
        Assert-Equal (Show-SetuoraMenu) 0 'Invalid menu input is handled as plain text'
        Assert-Equal $script:Calls @() 'Invalid menu input cannot execute commands'
        $expected = @('open', 'start', 'stop', 'status', 'setup', 'update', 'logs', 'preflight')
        for ($index = 0; $index -lt $expected.Count; $index++) {
            $script:Answers.Enqueue([string]($index + 1))
            $script:Answers.Enqueue('')
        }
        $script:Answers.Enqueue('0')
        Assert-Equal (Show-SetuoraMenu) 0 'Menu selections execute and return to menu'
        Assert-Equal $script:Calls @($expected | ForEach-Object { "menu:$_" }) 'Every menu selection dispatches the expected action'
        Reset-Calls
        $script:FailMenu = $true
        $script:Answers.Enqueue('2')
        $script:Answers.Enqueue('')
        $script:Answers.Enqueue('0')
        Assert-Equal (Show-SetuoraMenu) 0 'A failed action does not strand or close the menu'
        Assert-Equal $script:Calls @('menu:start') 'Only selected action runs after menu error'
    }
    default { throw "Unknown harness case: $Case" }
}
Write-Output "PASS:$Case"
