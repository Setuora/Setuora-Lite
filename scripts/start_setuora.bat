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

call "%SCRIPT_DIR%find_python.bat"
if errorlevel 1 (
    echo Python 3.11 or newer was not found. Install it, then rerun this script.
    endlocal & exit /b 1
)

"%PYTHON_EXE%" %PYTHON_ARGS% "%DEPLOY_SCRIPT%" start
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo Setuora start failed with exit code %EXIT_CODE%.
endlocal & exit /b %EXIT_CODE%
