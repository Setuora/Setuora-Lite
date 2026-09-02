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

call :is_administrator
if errorlevel 1 (
    echo Setuora setup requires Administrator privileges.
    echo Run the root setuora.bat controller and select Setup, or open an
    echo Administrator Command Prompt and run scripts\setup.bat directly.
    endlocal & exit /b 1
)

call "%SCRIPT_DIR%find_python.bat"
if errorlevel 1 (
    call :install_python
    if errorlevel 1 (
        endlocal & exit /b 1
    )
    call "%SCRIPT_DIR%find_python.bat"
    if errorlevel 1 (
        echo Python 3.11 was installed, but its executable could not be found in this process.
        echo Restart Windows, then run setuora.bat setup again. If the problem continues,
        echo install Python from https://www.python.org/downloads/windows/ and enable its launcher.
        endlocal & exit /b 1
    )
)

"%PYTHON_EXE%" %PYTHON_ARGS% "%DEPLOY_SCRIPT%" setup
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo Setuora setup failed with exit code %EXIT_CODE%.
endlocal & exit /b %EXIT_CODE%

:is_administrator
powershell.exe -NoLogo -NoProfile -Command "$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }" >nul 2>&1
exit /b %ERRORLEVEL%

:install_python
where winget.exe >nul 2>&1
if errorlevel 1 (
    echo Python 3.11 or newer is required, but Windows Package Manager is unavailable.
    echo Install Python 3.11 from https://www.python.org/downloads/windows/ and run setup again.
    exit /b 1
)

echo Python 3.11 or newer was not found. Installing Python 3.11...
winget.exe install --id Python.Python.3.11 --exact --source winget --scope machine --accept-package-agreements --accept-source-agreements --disable-interactivity
if errorlevel 1 (
    echo Windows Package Manager could not install Python 3.11.
    echo Install it from https://www.python.org/downloads/windows/ and run setup again.
    exit /b 1
)
exit /b 0
