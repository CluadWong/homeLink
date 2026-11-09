@echo off
rem Opened at user logon: prints access addresses only, never starts the service.
title JJ Butler - Addresses
cd /d "%~dp0..\.."
".venv\Scripts\python.exe" scripts\addresses.py %*
echo.
pause
