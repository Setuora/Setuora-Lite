@echo off
setlocal
cd /d "%~dp0\..\.."
if not exist "logs" mkdir "logs"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log >> "logs\setuora.log" 2>&1
