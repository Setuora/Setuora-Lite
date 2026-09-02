@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Setuora Lite Controls

set "ROOT_DIR=%~dp0"
set "SCRIPT_DIR=%ROOT_DIR%scripts\"
set "CLI_MODE="
set "ELEVATED_REENTRY="
set "PAUSE_ON_ERROR="

if /I "%~2"=="--elevated" set "ELEVATED_REENTRY=1"
if /I "%~3"=="--pause-on-error" set "PAUSE_ON_ERROR=1"
if "%~1"=="" goto menu
set "CLI_MODE=1"
goto command

:menu
cls
echo.
echo   Setuora Lite - source checkout controls
echo.
echo   [1] Setup or repair
echo   [2] Start
echo   [3] Stop
echo   [4] Update from Git
echo   [5] Exit
echo.
set "ACTION="
set /p "ACTION=Choose an option: "

if "%ACTION%"=="1" goto action_setup
if "%ACTION%"=="2" goto action_start
if "%ACTION%"=="3" goto action_stop
if "%ACTION%"=="4" goto action_update
if "%ACTION%"=="5" goto done
echo.
echo Invalid choice. Enter a number from 1 to 5.
echo.
pause
goto menu

:command
if /I "%~1"=="setup" goto action_setup
if /I "%~1"=="start" goto action_start
if /I "%~1"=="stop" goto action_stop
if /I "%~1"=="update" goto action_update
if /I "%~1"=="help" goto usage
if /I "%~1"=="--help" goto usage
if /I "%~1"=="-h" goto usage
echo Unknown Setuora command: %~1
echo.
call :print_usage
endlocal & exit /b 2

:action_setup
set "DISPLAY_ACTION=Setup / repair"
call :run_elevated_if_needed setup
set "EXIT_CODE=%ERRORLEVEL%"
if defined ELEVATED_CHILD_RAN goto operation_complete_saved
call "%SCRIPT_DIR%setup.bat"
goto operation_complete

:action_start
set "DISPLAY_ACTION=Start"
call "%SCRIPT_DIR%start_setuora.bat"
goto operation_complete

:action_stop
set "DISPLAY_ACTION=Stop"
call "%SCRIPT_DIR%stop_setuora.bat"
goto operation_complete

:action_update
set "DISPLAY_ACTION=Update"
call :run_elevated_if_needed update
set "EXIT_CODE=%ERRORLEVEL%"
if defined ELEVATED_CHILD_RAN goto operation_complete_saved
call "%SCRIPT_DIR%update.bat"
goto operation_complete

:operation_complete
set "EXIT_CODE=%ERRORLEVEL%"

:operation_complete_saved
if defined PAUSE_ON_ERROR if not "%EXIT_CODE%"=="0" (
    echo.
    echo %DISPLAY_ACTION% failed with exit code %EXIT_CODE%.
    echo Review the messages above before closing this Administrator window.
    echo.
    pause
)
if defined CLI_MODE goto cli_exit
echo.
if "%EXIT_CODE%"=="0" (
    echo %DISPLAY_ACTION% completed successfully.
) else (
    echo %DISPLAY_ACTION% failed with exit code %EXIT_CODE%.
)
echo.
pause
goto menu

:cli_exit
endlocal & exit /b %EXIT_CODE%

:run_elevated_if_needed
set "ELEVATED_CHILD_RAN="
call :is_administrator
if not errorlevel 1 exit /b 0
if defined ELEVATED_REENTRY (
    echo Administrator privileges were not granted.
    echo Right-click setuora.bat and choose Run as administrator, then try again.
    exit /b 1
)

echo Administrator approval is required for %~1.
echo Complete the Windows security prompt to continue...
set "ELEVATED_CHILD_RAN=1"
set "SETUORA_CONTROLLER=%~f0"
set "SETUORA_ACTION=%~1"
set "SETUORA_ELEVATED_PAUSE="
if not defined CLI_MODE set "SETUORA_ELEVATED_PAUSE=--pause-on-error"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; try { $arguments = '/d /s /c ""' + $env:SETUORA_CONTROLLER + '" ' + $env:SETUORA_ACTION + ' --elevated ' + $env:SETUORA_ELEVATED_PAUSE + '"'; $process = Start-Process -FilePath $env:ComSpec -ArgumentList $arguments -Verb RunAs -Wait -PassThru; exit $process.ExitCode } catch { Write-Host 'Administrator approval was cancelled or could not be started.'; exit 1 }"
set "ELEVATED_EXIT_CODE=%ERRORLEVEL%"
exit /b %ELEVATED_EXIT_CODE%

:is_administrator
powershell.exe -NoLogo -NoProfile -Command "$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }" >nul 2>&1
exit /b %ERRORLEVEL%

:usage
call :print_usage
endlocal & exit /b 0

:print_usage
echo Usage: %~nx0 [setup^|start^|stop^|update^|help]
echo Run without an argument to open the interactive menu.
exit /b 0

:done
endlocal & exit /b 0
