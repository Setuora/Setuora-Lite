@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Setuora Lite Controls

set "SCRIPT_DIR=%~dp0scripts\"

if not "%~1"=="" goto command

:menu
cls
echo.
echo   Setuora Lite - source checkout controls
echo.
echo   [1] Setup or repair (Administrator required)
echo   [2] Start
echo   [3] Stop
echo   [4] Update from Git (Administrator required)
echo   [5] Exit
echo.
set "ACTION="
set /p "ACTION=Choose an option: "

if "%ACTION%"=="1" goto setup
if "%ACTION%"=="2" goto start
if "%ACTION%"=="3" goto stop
if "%ACTION%"=="4" goto update
if "%ACTION%"=="5" goto done
echo.
echo Please enter a number from 1 to 5.
set /p "CONTINUE=Press Enter to try again..."
goto menu

:command
if /I "%~1"=="setup" goto setup
if /I "%~1"=="start" goto start
if /I "%~1"=="stop" goto stop
if /I "%~1"=="update" goto update
if /I "%~1"=="help" goto usage
if /I "%~1"=="--help" goto usage
if /I "%~1"=="-h" goto usage
echo Unknown Setuora command: %~1
goto usage_error

:setup
call "%SCRIPT_DIR%setup.bat"
goto command_complete

:start
call "%SCRIPT_DIR%start_setuora.bat"
goto command_complete

:stop
call "%SCRIPT_DIR%stop_setuora.bat"
goto command_complete

:update
call "%SCRIPT_DIR%update.bat"
goto command_complete

:command_complete
set "EXIT_CODE=%ERRORLEVEL%"
if not "%~1"=="" endlocal & exit /b %EXIT_CODE%
echo.
if not "%EXIT_CODE%"=="0" echo Command failed with exit code %EXIT_CODE%.
set /p "CONTINUE=Press Enter to return to the menu..."
goto menu

:usage
echo Usage: %~nx0 [setup^|start^|stop^|update]
echo Run without an argument to open the interactive menu.
endlocal & exit /b 0

:usage_error
echo Usage: %~nx0 [setup^|start^|stop^|update]
endlocal & exit /b 2

:done
endlocal & exit /b 0
