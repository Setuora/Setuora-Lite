@echo off
setlocal
title Setuora Lite - Install or Update
set "SETUORA_SELF=%~f0"
echo Setuora Lite - Windows install or update
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "& { $ErrorActionPreference = 'Stop'; $self = $env:SETUORA_SELF; $lines = [IO.File]::ReadAllLines($self); $marker = [Array]::IndexOf($lines, '__SETUORA_PAYLOAD_BELOW__'); if ($marker -lt 0) { throw 'The Setuora Lite installer payload is missing or damaged.' }; $parent = Join-Path $env:LOCALAPPDATA 'Setuora'; $target = Join-Path $parent 'Setuora-Lite-windows'; $launcher = Join-Path $target 'setuora.ps1'; $legacyLauncher = Join-Path $target 'Setuora.exe'; $isUpdate = Test-Path (Join-Path $target '.env'); if ($isUpdate) { Write-Host 'Existing installation found. Stopping Setuora Lite before updating...'; if (Test-Path -LiteralPath $launcher) { & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $launcher stop } elseif (Test-Path -LiteralPath $legacyLauncher) { & $legacyLauncher stop } else { throw 'The existing Setuora Lite launcher is missing.' }; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }; [IO.Directory]::CreateDirectory($parent) | Out-Null; $zip = Join-Path ([IO.Path]::GetTempPath()) ('setuora-lite-' + [guid]::NewGuid().ToString('N') + '.zip'); try { $encoded = [string]::Concat($lines[($marker + 1)..($lines.Length - 1)]); [IO.File]::WriteAllBytes($zip, [Convert]::FromBase64String($encoded)); Expand-Archive -LiteralPath $zip -DestinationPath $parent -Force; Remove-Item -LiteralPath $legacyLauncher -Force -ErrorAction SilentlyContinue } finally { Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue }; Write-Host ('Application files installed in: ' + $target); if ($isUpdate) { & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $launcher preflight; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $launcher update; exit $LASTEXITCODE }; & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $launcher setup; exit $LASTEXITCODE }"
set "SETUORA_EXIT=%ERRORLEVEL%"
echo.
if "%SETUORA_EXIT%"=="0" (
  echo Setuora Lite completed successfully.
) else (
  echo Setuora Lite did not complete. Review the message above.
)
pause
exit /b %SETUORA_EXIT%
__SETUORA_PAYLOAD_BELOW__
