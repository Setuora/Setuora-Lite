@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "DEPLOY_SCRIPT=%PROJECT_ROOT%\deploy.py"

if not exist "%DEPLOY_SCRIPT%" (
    echo Setuora deployment entry point was not found: "%DEPLOY_SCRIPT%"
    echo Run this script from a complete Setuora source checkout.
    endlocal & exit /b 1
)

powershell.exe -NoProfile -Command "$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo Setuora setup must be run with Administrator privileges.
    echo Open an elevated Command Prompt, or right-click scripts\setup.bat and choose Run as administrator.
    endlocal & exit /b 1
)

call :find_python
if errorlevel 1 (
    echo Python 3.11 or newer was not found. Install it, then rerun this script.
    endlocal & exit /b 1
)

"%PYTHON_EXE%" %PYTHON_ARGS% "%DEPLOY_SCRIPT%" setup
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo Setuora setup failed with exit code %EXIT_CODE%.
endlocal & exit /b %EXIT_CODE%

:find_python
set "PYTHON_EXE="
set "PYTHON_ARGS="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=py"
    set "PYTHON_ARGS=-3"
    exit /b 0
)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=python"
    exit /b 0
)
python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=python3"
    exit /b 0
)
exit /b 1
