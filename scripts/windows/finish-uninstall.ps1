[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('Master', 'Lite')][string]$Product,
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][int]$WaitForPid
)

$ErrorActionPreference = 'Stop'
$expected = @(
    (Join-Path $env:ProgramData "Setuora\Setuora-$Product"),
    (Join-Path $env:ProgramData "Setuora\Setuora-$Product-windows")
) | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\') }
$target = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$statusFile = Join-Path $PSScriptRoot 'removal-status.txt'

try {
    if ($target -notin $expected) { throw "Refusing to remove an unexpected folder: $target" }
    $sharedRoot = Join-Path $env:ProgramData 'Setuora'
    if ((Get-Item -LiteralPath $sharedRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Refusing to remove files through a linked shared folder: $sharedRoot"
    }
    if ((Test-Path -LiteralPath $target) -and
        ((Get-Item -LiteralPath $target -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Refusing to remove a linked installation folder: $target"
    }
    Set-Location -LiteralPath $PSScriptRoot
    try { Wait-Process -Id $WaitForPid -Timeout 90 -ErrorAction Stop } catch {
        if (Get-Process -Id $WaitForPid -ErrorAction SilentlyContinue) {
            throw 'The control window did not close; application files were left in place.'
        }
    }
    $removed = $false
    for ($attempt = 0; $attempt -lt 45; $attempt++) {
        try {
            if (Test-Path -LiteralPath $target) {
                Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
            }
            $removed = -not (Test-Path -LiteralPath $target)
            if ($removed) { break }
        } catch {
            if ($attempt -eq 44) { throw }
        }
        Start-Sleep -Seconds 1
    }
    if (-not $removed) { throw "Application files could not be removed from $target" }
    if ($Product -eq 'Lite') {
        $caddy = [IO.Path]::GetFullPath((Join-Path $env:ProgramData 'Setuora\caddy-lite')).TrimEnd('\')
        if ((Test-Path -LiteralPath $caddy) -and
            ((Get-Item -LiteralPath $caddy -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing to remove a linked Caddy folder: $caddy"
        }
        if (Test-Path -LiteralPath $caddy) {
            Remove-Item -LiteralPath $caddy -Recurse -Force -ErrorAction Stop
        }
    }
    "Removed $Product application files at $(Get-Date -Format o). Recovery bundle: $PSScriptRoot" |
        Set-Content -LiteralPath $statusFile -Encoding UTF8
} catch {
    "Removal needs attention at $(Get-Date -Format o): $($_.Exception.Message)" |
        Set-Content -LiteralPath $statusFile -Encoding UTF8
    exit 1
}
