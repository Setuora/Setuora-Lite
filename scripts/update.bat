@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0..\setuora.bat" update %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
