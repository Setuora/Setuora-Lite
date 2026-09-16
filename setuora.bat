@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Setuora Lite Controls
set "ROOT_DIR=%~dp0"
set "LAUNCHER=%ROOT_DIR%setuora.ps1"
if not exist "%LAUNCHER%" set "LAUNCHER=%ROOT_DIR%client\windows\setuora.ps1"
if not exist "%LAUNCHER%" (
    echo Setuora controls were not found. Run the installer again or use a complete source checkout.
    if "%~1"=="" pause
    endlocal & exit /b 1
)
powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%LAUNCHER%" %*
set "EXIT_CODE=%ERRORLEVEL%"
if "%~1"=="" if not "%EXIT_CODE%"=="0" pause
endlocal & exit /b %EXIT_CODE%
