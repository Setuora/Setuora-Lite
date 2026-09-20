@echo off
setlocal
cd /d "%~dp0\..\.."
if not exist "logs" mkdir "logs"
".venv\Scripts\python.exe" deploy.py serve >> "logs\setuora.log" 2>&1
