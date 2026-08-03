@echo off
setlocal
title Setuora Lite - Windows
set "SETUORA_DIST=%~dp0dist"
set "SETUORA_BUILDER=%~dp0scripts\build_client_packages.py"
set "SETUORA_INSTALLER="
set "SETUORA_PYTHON="

call :find_installer
if defined SETUORA_INSTALLER goto launch

if not exist "%SETUORA_BUILDER%" goto missing_package

call :find_python
if not defined SETUORA_PYTHON (
    echo Python 3.11 or newer is required to build the Windows installer.
    echo Install Python from https://www.python.org/downloads/windows/
    echo and select "Add python.exe to PATH", then try again.
    pause
    exit /b 1
)

if not defined SETUORA_RELEASE_VERSION set "SETUORA_RELEASE_VERSION=pilot"
echo No built Windows package was found. Building version %SETUORA_RELEASE_VERSION%...
call %SETUORA_PYTHON% "%SETUORA_BUILDER%" --version "%SETUORA_RELEASE_VERSION%"
if errorlevel 1 (
    echo.
    echo The Windows installer build failed. Review the message above.
    pause
    exit /b 1
)

call :find_installer
if not defined SETUORA_INSTALLER (
    echo The package build completed but no Windows installer was found in:
    echo %SETUORA_DIST%
    pause
    exit /b 1
)

:launch
echo Launching: %SETUORA_INSTALLER%
call "%SETUORA_INSTALLER%"
exit /b %ERRORLEVEL%

:find_installer
set "SETUORA_INSTALLER="
for /f "delims=" %%F in ('dir /b /a-d /o:-d "%SETUORA_DIST%\Setuora-Lite-*-windows.cmd" 2^>nul') do (
    set "SETUORA_INSTALLER=%SETUORA_DIST%\%%F"
    goto :eof
)
goto :eof

:find_python
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(sys.version_info ^< (3, 11))" >nul 2>&1
    if not errorlevel 1 set "SETUORA_PYTHON=py -3"
)
if defined SETUORA_PYTHON goto :eof
where python >nul 2>&1
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(sys.version_info ^< (3, 11))" >nul 2>&1
    if not errorlevel 1 set "SETUORA_PYTHON=python"
)
if defined SETUORA_PYTHON goto :eof
where python3 >nul 2>&1
if not errorlevel 1 (
    python3 -c "import sys; raise SystemExit(sys.version_info ^< (3, 11))" >nul 2>&1
    if not errorlevel 1 set "SETUORA_PYTHON=python3"
)
goto :eof

:missing_package
echo This file is the source-checkout shortcut, not the standalone installer.
echo The package builder is missing, so it cannot create an installer here.
echo.
echo Copy and run the release file named:
echo Setuora-Lite-VERSION-windows.cmd
echo.
echo Do not copy "Windows — Setuora Lite.cmd" by itself.
pause
exit /b 1
