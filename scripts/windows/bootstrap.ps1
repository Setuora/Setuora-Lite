[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepositoryUrl = 'https://github.com/Setuora/Setuora-Lite.git'
$Branch = 'main'
$ProductRoot = Join-Path $env:ProgramData 'Setuora'
$InstallRoot = Join-Path $ProductRoot 'Setuora-Lite'
$PackagedRoot = Join-Path $ProductRoot 'Setuora-Lite-windows'

function Test-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-CheckedCodeAcl([string]$Path, [string[]]$Arguments) {
    $oldPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & icacls.exe @Arguments | Out-Null
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $oldPreference }
    if ($code -ne 0) { throw "Could not protect application files at $Path (icacls exit $code)." }
}

function Set-CodeAcl([string]$Path, [switch]$Recursive) {
    if ($Recursive) {
        # Give existing files explicit rights before removing inherited ACEs.
        Invoke-CheckedCodeAcl $Path @($Path, '/grant', '*S-1-5-18:F', '*S-1-5-32-544:F', '*S-1-5-32-545:RX', '/T', '/Q', '/L')
        Invoke-CheckedCodeAcl $Path @($Path, '/grant', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-32-545:(OI)(CI)RX', '/T', '/Q', '/L')
        Invoke-CheckedCodeAcl $Path @($Path, '/inheritance:r', '/T', '/Q', '/L')
        Invoke-CheckedCodeAcl $Path @($Path, '/remove:g', '*S-1-1-0', '*S-1-5-11', '/T', '/Q', '/L')
    } else {
        $inheritance = if (Test-Path -LiteralPath $Path -PathType Leaf) { '' } else { '(OI)(CI)' }
        Invoke-CheckedCodeAcl $Path @(
            $Path, '/inheritance:r', '/remove:g', '*S-1-1-0', '*S-1-5-11', '/grant:r',
            "*S-1-5-18:${inheritance}F", "*S-1-5-32-544:${inheritance}F",
            "*S-1-5-32-545:${inheritance}RX", '/Q', '/L'
        )
    }
}

function Protect-Checkout {
    # Keep secrets and database under their separate administrator-only ACLs.
    $rootItem = Get-Item -LiteralPath $InstallRoot -Force
    if ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "The Lite installation folder is a linked path. Check it before applying permissions."
    }
    Set-CodeAcl $InstallRoot
    foreach ($child in Get-ChildItem -LiteralPath $InstallRoot -Force) {
        if ($child.Name -in @('.env', 'data', 'logs')) { continue }
        if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "The Lite checkout contains a linked path, $($child.FullName). Check it before applying permissions."
        }
        if ($child.PSIsContainer) { Set-CodeAcl $child.FullName -Recursive }
        else { Set-CodeAcl $child.FullName }
    }
}

function Find-Git {
    $command = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $locations = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }
    foreach ($root in $locations) {
        $path = Join-Path $root 'Git\cmd\git.exe'
        if ($path -and (Test-Path -LiteralPath $path)) { return $path }
    }
    return $null
}

function Install-Git {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host 'Installing Git for Windows through Windows Package Manager...'
        $oldPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & $winget.Source install --id Git.Git --exact --source winget --scope machine --accept-package-agreements --accept-source-agreements --disable-interactivity
            $code = $LASTEXITCODE
        } finally { $ErrorActionPreference = $oldPreference }
        if ($code -eq 3010) { throw 'Git installed. Restart Windows, then double-click install-lite.bat again.' }
        if ($code -eq 0 -and (Find-Git)) { return }
        Write-Host 'Windows Package Manager did not provide Git. Trying the signed official installer.' -ForegroundColor Yellow
    }

    $architecture = '64-bit'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest' -Headers @{ 'User-Agent' = 'Setuora-Lite-Installer' }
    $pattern = '^Git-[0-9][A-Za-z0-9.]*-' + [regex]::Escape($architecture) + '\.exe$'
    $asset = @($release.assets | Where-Object { $_.name -match $pattern }) | Select-Object -First 1
    if (-not $asset -or $asset.browser_download_url -notlike 'https://github.com/git-for-windows/git/releases/download/*') {
        throw 'The official Git for Windows release did not contain an installer for this computer.'
    }
    $installer = Join-Path ([IO.Path]::GetTempPath()) ('setuora-git-' + [guid]::NewGuid().ToString('N') + '.exe')
    try {
        Write-Host 'Downloading the signed Git for Windows installer...'
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $installer -UseBasicParsing
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($signature.Status -ne [Management.Automation.SignatureStatus]::Valid -or
            $signature.SignerCertificate.Subject -notmatch 'Johannes Schindelin|Git for Windows') {
            throw 'The downloaded Git installer does not have a valid Git for Windows publisher signature.'
        }
        $process = Start-Process -FilePath $installer -ArgumentList @(
            '/VERYSILENT', '/NORESTART', '/SP-', '/SUPPRESSMSGBOXES', '/o:PathOption=Cmd'
        ) -Wait -PassThru -WindowStyle Hidden
        if ($process.ExitCode -notin @(0, 3010)) { throw "Git installation failed (exit $($process.ExitCode))." }
        if ($process.ExitCode -eq 3010) { throw 'Git installed. Restart Windows, then double-click install-lite.bat again.' }
    } finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
    if (-not (Find-Git)) { throw 'Git installed but was not found. Restart Windows and double-click install-lite.bat again.' }
}

function Invoke-Git([string[]]$Arguments) {
    $oldPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $script:GitExecutable @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $oldPreference }
    if ($code -ne 0) { throw ('Git failed: ' + ($output -join [Environment]::NewLine)) }
    return ($output -join [Environment]::NewLine).Trim()
}

function Invoke-Controller([string]$Action) {
    $controller = Join-Path $InstallRoot 'client\windows\setuora.ps1'
    if (-not (Test-Path -LiteralPath $controller)) { throw "Lite control file is missing: $controller" }
    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $controller $Action
    if ($LASTEXITCODE -ne 0) { throw "Setuora Lite $Action failed (exit $LASTEXITCODE)." }
}

function Backup-Lite {
    $python = Join-Path $InstallRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'The existing Python runtime is missing. Use the Lite Setup / repair control before updating.'
    }
    Write-Host 'Creating a verified SQLite backup before updating...'
    Push-Location -LiteralPath $InstallRoot
    try {
        & $python -c 'from app.services.backup import create_scheduled_backup; print(create_scheduled_backup().path)'
        if ($LASTEXITCODE -ne 0) { throw 'The pre-update database backup failed; the running Lite was left untouched.' }
    } finally { Pop-Location }
}

function Test-Task([string]$Name) {
    $oldPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $null = & schtasks.exe /Query /TN $Name 2>$null
        return $LASTEXITCODE -eq 0
    } finally { $ErrorActionPreference = $oldPreference }
}

try {
    if (-not (Test-Administrator)) { throw 'Run install-lite.bat and approve its Administrator prompt.' }
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
        throw 'This release needs an x64 Windows 10 or 11 computer. The locked Python runtime does not support Windows ARM64 or 32-bit.'
    }
    if (Test-Path -LiteralPath (Join-Path $PackagedRoot '.env')) {
        throw "A packaged Lite is already installed in $PackagedRoot. Update it with its release installer; this Git installer will not replace it."
    }
    $taskExists = if (-not (Test-Path -LiteralPath $InstallRoot)) { Test-Task 'Setuora-Lite' } else { $false }
    if (-not (Test-Path -LiteralPath $InstallRoot) -and $taskExists) {
        throw 'A Setuora Lite boot task already exists outside this Git installation. Remove or migrate that installation before continuing.'
    }
    New-Item -ItemType Directory -Path $ProductRoot -Force | Out-Null
    if ((Get-Item -LiteralPath $ProductRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'The Setuora parent folder is a linked path. Setup stopped before changing permissions.'
    }
    & icacls.exe $ProductRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'The Setuora installation folder could not be secured.' }
    & icacls.exe $ProductRoot /remove:g '*S-1-5-11' '*S-1-1-0' /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Broad access to the Setuora installation folder could not be removed.' }
    $script:GitExecutable = Find-Git
    if (-not $script:GitExecutable) {
        Install-Git
        $script:GitExecutable = Find-Git
    }
    if (-not $script:GitExecutable) { throw 'Git is unavailable after installation.' }
    Write-Host "Using Git: $script:GitExecutable"

    if (Test-Path -LiteralPath $InstallRoot) {
        if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot '.git'))) {
            throw "$InstallRoot exists but is not a Git checkout. It was left untouched."
        }
        Protect-Checkout
        $origin = Invoke-Git @('-C', $InstallRoot, 'remote', 'get-url', 'origin')
        if ($origin -ne $RepositoryUrl) { throw "The existing Lite checkout has a different origin ($origin). It was left untouched." }
        $branch = Invoke-Git @('-C', $InstallRoot, 'branch', '--show-current')
        if ($branch -ne $Branch) { throw "The existing Lite checkout is on $branch, expected $Branch. It was left untouched." }
        if (Invoke-Git @('-C', $InstallRoot, 'status', '--porcelain', '--untracked-files=all')) {
            throw 'The Lite checkout has local changes. Save them before updating; the running app was left untouched.'
        }
        Write-Host 'Fetching the latest Lite code...'
        $null = Invoke-Git @('-C', $InstallRoot, 'fetch', '--quiet', 'origin', $Branch)
        $null = Invoke-Git @('-C', $InstallRoot, 'merge-base', '--is-ancestor', 'HEAD', "origin/$Branch")
        $current = Invoke-Git @('-C', $InstallRoot, 'rev-parse', 'HEAD')
        $latest = Invoke-Git @('-C', $InstallRoot, 'rev-parse', "origin/$Branch")
        if ($current -ne $latest) {
            if (Test-Path -LiteralPath (Join-Path $InstallRoot '.env')) {
                Invoke-Controller 'preflight'
                Backup-Lite
                Invoke-Controller 'stop'
            }
            try {
                $null = Invoke-Git @('-C', $InstallRoot, 'merge', '--ff-only', "origin/$Branch")
            } catch {
                if (Test-Path -LiteralPath (Join-Path $InstallRoot '.env')) {
                    try { Invoke-Controller 'start' } catch { Write-Host $_.Exception.Message -ForegroundColor Yellow }
                }
                throw
            }
        }
    } else {
        Write-Host "Downloading Setuora Lite to $InstallRoot..."
        $null = Invoke-Git @('clone', '--single-branch', '--branch', $Branch, $RepositoryUrl, $InstallRoot)
        Protect-Checkout
    }

    Invoke-Controller 'setup'
    Protect-Checkout
    Write-Host ''
    Write-Host 'Setuora Lite is ready. Use the private HTTPS address shown above.' -ForegroundColor Green
    Write-Host "For later controls, open $(Join-Path $InstallRoot 'setuora.bat')."
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
