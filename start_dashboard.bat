@echo off
REM Paper-trader control center -- localhost dashboard
cd /d "%~dp0"
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8000
