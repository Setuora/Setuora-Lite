@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Setuora Lite - Install or Update
set "SETUORA_SELF=%~f0"
powershell.exe -NoLogo -NoProfile -Command "$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }" >nul 2>&1
if not "%ERRORLEVEL%"=="0" (
  echo Administrator access is required. Opening the Windows security prompt...
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $process=Start-Process -FilePath $env:SETUORA_SELF -Verb RunAs -Wait -PassThru; exit $process.ExitCode } catch { Write-Error $_; exit 1 }"
  goto elevated_exit
)
echo Setuora Lite - Windows install or update
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "& { $ErrorActionPreference = 'Stop'; $self = $env:SETUORA_SELF; $lines = [IO.File]::ReadAllLines($self); $marker = [Array]::IndexOf($lines, '__SETUORA_PAYLOAD_BELOW__'); if ($marker -lt 0) { throw 'The Setuora installer payload is missing or damaged.' }; $parent = Join-Path $env:ProgramData 'Setuora'; $sourceInstall = Join-Path $parent 'Setuora-Lite'; if ((Test-Path (Join-Path $sourceInstall '.env')) -or (Test-Path (Join-Path $sourceInstall '.git'))) { throw 'A Git Lite installation already exists. Use install-lite.bat to update it; this package will not replace it.' }; [IO.Directory]::CreateDirectory($parent) | Out-Null; if ((Get-Item -LiteralPath $parent -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'The Setuora folder is linked; setup stopped.' }; & icacls.exe $parent /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null; if ($LASTEXITCODE -ne 0) { throw 'The Setuora installation folder could not be secured.' }; & icacls.exe $parent /remove:g '*S-1-5-11' '*S-1-1-0' /Q | Out-Null; if ($LASTEXITCODE -ne 0) { throw 'Broad access to the Setuora installation folder could not be removed.' }; $target = Join-Path $parent 'Setuora-Lite-windows'; $launcher = Join-Path $target 'setuora.ps1'; $isUpdate = Test-Path (Join-Path $target '.env'); & schtasks.exe /Query /TN 'Setuora-Lite' >$null 2>$null; if ($LASTEXITCODE -eq 0 -and -not $isUpdate) { throw 'A different Lite installation already owns the startup task. This package will not replace it.' }; $stage = Join-Path ([IO.Path]::GetTempPath()) ('setuora-stage-' + [guid]::NewGuid().ToString('N')); [IO.Directory]::CreateDirectory($stage) | Out-Null; $zip = Join-Path $stage 'payload.zip'; try { $encoded = [string]::Concat($lines[($marker + 1)..($lines.Length - 1)]); [IO.File]::WriteAllBytes($zip, [Convert]::FromBase64String($encoded)); Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force; $source = Join-Path $stage 'Setuora-Lite-windows'; if (-not (Test-Path (Join-Path $source 'deploy.py'))) { throw 'The installer payload is incomplete.' }; if ($isUpdate) { & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $launcher preflight; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; Write-Host 'Existing installation found. Stopping Setuora before updating...'; & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $launcher stop; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }; [IO.Directory]::CreateDirectory($target) | Out-Null; Copy-Item -Path (Join-Path $source '*') -Destination $target -Recurse -Force; $caches = @(Get-ChildItem -LiteralPath (Join-Path $target 'app') -Directory -Filter '__pycache__' -Recurse -Force); foreach ($cache in $caches) { Remove-Item -LiteralPath $cache.FullName -Recurse -Force } } finally { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }; Write-Host ('Application files installed in: ' + $target); & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $launcher setup; exit $LASTEXITCODE }"
set "SETUORA_EXIT=%ERRORLEVEL%"
echo.
if "%SETUORA_EXIT%"=="0" (
  echo Setuora Lite completed successfully.
  echo Open "%ProgramData%\Setuora\Setuora-Lite-windows\setuora.bat" for the control menu.
  echo Browser: use the private HTTPS address shown above.
) else (
  echo Setuora Lite did not complete. Review the message above.
)
pause
exit /b %SETUORA_EXIT%
:elevated_exit
exit /b %ERRORLEVEL%
__SETUORA_PAYLOAD_BELOW__
