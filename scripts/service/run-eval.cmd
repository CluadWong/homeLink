@echo off
rem Weekly entry called by scheduled task HomeLinkEval. Do not double-click.
rem Runs the 130-case evaluation against a temporary mock backend (no IR sent).
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0..\.."
if not exist logs mkdir logs

echo [%date% %time%] === eval start === >> logs\eval.log
".venv\Scripts\python.exe" scripts\eval_sandbox.py --report docs\eval-report.md >> logs\eval.log 2>&1
echo [%date% %time%] === eval done (exit=%ERRORLEVEL%) === >> logs\eval.log
