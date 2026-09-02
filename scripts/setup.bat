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
    echo Administrator approval is required. Opening the Windows security prompt...
    set "SETUORA_SETUP_BAT=%~f0"
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath $env:SETUORA_SETUP_BAT -Verb RunAs"
    if errorlevel 1 (
        echo Setuora setup could not request Administrator privileges.
        echo Right-click setuora.bat and choose Run as administrator, then select Setup.
        endlocal & exit /b 1
    )
    echo Setup is continuing in the elevated window.
    endlocal & exit /b 0
)

call :find_python
if errorlevel 1 (
    call :install_python
    if errorlevel 1 (
        endlocal & exit /b 1
    )
    call :find_python
    if errorlevel 1 (
        echo Python was installed but is not available yet. Close this window, open a new elevated Command Prompt, and run setup again.
        endlocal & exit /b 1
    )
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
if exist "%ProgramFiles%\Python311\python.exe" (
    "%ProgramFiles%\Python311\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=%ProgramFiles%\Python311\python.exe"
        exit /b 0
    )
)
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        exit /b 0
    )
)
exit /b 1

:install_python
where winget.exe >nul 2>&1
if errorlevel 1 (
    echo Python 3.11 or newer is required, and Windows Package Manager is unavailable.
    echo Install Python 3.11 from https://www.python.org/downloads/windows/ and run setup again.
    exit /b 1
)

echo Python 3.11 was not found. Installing it with Windows Package Manager...
winget.exe install --id Python.Python.3.11 --exact --source winget --scope machine --accept-package-agreements --accept-source-agreements --disable-interactivity
if errorlevel 1 (
    echo Windows Package Manager could not install Python 3.11.
    echo Install it from https://www.python.org/downloads/windows/ and run setup again.
    exit /b 1
)
exit /b 0
