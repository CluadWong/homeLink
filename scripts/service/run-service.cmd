@echo off
rem Boot entry called by scheduled task HomeLinkService. Do not double-click (port 8000).
rem Startup output (including access addresses) goes to logs\service.log.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0..\.."
if not exist logs mkdir logs

".venv\Scripts\python.exe" scripts\start.py >> logs\service.log 2>&1
