@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Setuora Lite - Install or update
set "SETUORA_BOOTSTRAP_BAT=%~f0"

powershell.exe -NoLogo -NoProfile -Command "$p=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if ($p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }" >nul 2>&1
if not "%ERRORLEVEL%"=="0" (
    echo Approve the Windows Administrator prompt to install Setuora Lite.
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $p=Start-Process -FilePath $env:SETUORA_BOOTSTRAP_BAT -Verb RunAs -Wait -PassThru; exit $p.ExitCode } catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }"
    goto elevated_exit
)

echo Setuora Lite - downloading the latest installer steps...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "& { $ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $local=Join-Path (Split-Path -Parent $env:SETUORA_BOOTSTRAP_BAT) 'scripts\windows\bootstrap.ps1'; $download=$null; try { if (Test-Path -LiteralPath $local) { $script=$local } else { $download=Join-Path ([IO.Path]::GetTempPath()) ('setuora-lite-bootstrap-'+[guid]::NewGuid().ToString('N')+'.ps1'); Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/Setuora/Setuora-Lite/main/scripts/windows/bootstrap.ps1' -OutFile $download -UseBasicParsing; $script=$download }; & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $script; exit $LASTEXITCODE } catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 } finally { if ($download) { Remove-Item -LiteralPath $download -Force -ErrorAction SilentlyContinue } } }"
set "SETUORA_EXIT=%ERRORLEVEL%"
echo.
if "%SETUORA_EXIT%"=="0" (
    echo Setuora Lite installed and verified.
    echo Open the private HTTPS address shown above from a PC on the same tailnet.
) else (
    echo Setup did not finish. Read the error above, then double-click this file to retry.
)
pause
endlocal & exit /b %SETUORA_EXIT%
:elevated_exit
endlocal & exit /b %ERRORLEVEL%
