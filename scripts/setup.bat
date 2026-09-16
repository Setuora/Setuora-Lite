@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0..\setuora.bat" setup %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
