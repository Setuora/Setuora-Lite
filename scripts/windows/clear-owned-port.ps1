param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$expectedPython = [IO.Path]::GetFullPath((Join-Path $ProjectRoot ".venv\Scripts\python.exe"))
$expectedUvicorn = [IO.Path]::GetFullPath((Join-Path $ProjectRoot ".venv\Scripts\uvicorn.exe"))

function Test-SetuoraProcess($Process) {
    if (-not $Process -or -not $Process.ExecutablePath -or -not $Process.CommandLine) { return $false }
    $executable = [IO.Path]::GetFullPath($Process.ExecutablePath)
    $python = [string]::Equals($executable, $expectedPython, [StringComparison]::OrdinalIgnoreCase)
    $uvicorn = [string]::Equals($executable, $expectedUvicorn, [StringComparison]::OrdinalIgnoreCase)
    if ($python) {
        return $Process.CommandLine -match '(?i)(?:^|\s)-m\s+uvicorn\s+app\.main:app(?:\s|$)' -and
            $Process.CommandLine -match '(?i)(?:^|\s)--port(?:\s+|=)8000(?:\s|$)'
    }
    if ($uvicorn) {
        return $Process.CommandLine -match '(?i)(?:^|\s)app\.main:app(?:\s|$)' -and
            $Process.CommandLine -match '(?i)(?:^|\s)--port(?:\s+|=)8000(?:\s|$)'
    }
    return $false
}

$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($processId in $listeners) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if (-not (Test-SetuoraProcess $process)) {
        throw "Port $Port is held by process $processId, which is not this Setuora Lite service. Stop that application yourself, then retry."
    }
}
foreach ($processId in $listeners) {
    # Re-check the PID immediately before stopping it in case Windows recycled it.
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if (-not (Test-SetuoraProcess $process)) {
        throw "Port $Port owner changed while checking process $processId. No process was stopped; retry."
    }
    Write-Host "Stopping stale Setuora Lite process $processId on port $Port..."
    Stop-Process -Id $processId -Force -ErrorAction Stop
}
