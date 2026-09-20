param([Parameter(Mandatory = $true)][string]$PortClearer)

$ErrorActionPreference = 'Stop'
$script:Owner = 'foreign'
$script:Stopped = @()
$projectRoot = Join-Path ([IO.Path]::GetTempPath()) 'Setuora Lite Test'
$expectedPython = [IO.Path]::GetFullPath((Join-Path $projectRoot '.venv\Scripts\python.exe'))

function Get-NetTCPConnection($LocalPort, $State, $ErrorAction) {
    return [pscustomobject]@{ OwningProcess = 4242 }
}
function Get-CimInstance($ClassName, $Filter, $ErrorAction) {
    if ($script:Owner -eq 'foreign') {
        return [pscustomobject]@{
            ExecutablePath = $expectedPython
            CommandLine = 'python.exe -m uvicorn unrelated.main:app --port 8000'
        }
    }
    return [pscustomobject]@{
        ExecutablePath = $expectedPython
        CommandLine = 'python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000'
    }
}
function Stop-Process($Id, [switch]$Force, $ErrorAction) { $script:Stopped += $Id }

$caught = $false
try { . $PortClearer -ProjectRoot $projectRoot -Port 8000 } catch { $caught = $true }
if (-not $caught -or $script:Stopped.Count -ne 0) { throw 'Foreign listener must never be killed' }
. $PortClearer -ProjectRoot $projectRoot -Port 8000 -AllowForeign
if ($script:Stopped.Count -ne 0) { throw 'Safe reconfiguration stop must leave a foreign listener alone' }

$script:Owner = 'setuora'
. $PortClearer -ProjectRoot $projectRoot -Port 8000
if ($script:Stopped.Count -ne 1 -or $script:Stopped[0] -ne 4242) {
    throw 'Only the exact Setuora listener should be killed'
}
Write-Output 'PASS:port-ownership'
