@echo off
rem Nightly entry called by scheduled task HomeLinkBackup. Do not double-click.
rem Online SQLite snapshot via scripts\backup.py; output goes to logs\backup.log.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0..\.."
if not exist logs mkdir logs

echo [%date% %time%] === backup start === >> logs\backup.log
".venv\Scripts\python.exe" scripts\backup.py >> logs\backup.log 2>&1
echo [%date% %time%] === backup done === >> logs\backup.log
