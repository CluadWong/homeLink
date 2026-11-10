@echo off
rem Boot entry called by scheduled task HomeLinkAgent. Do not double-click (port 8100).
rem Keeps the agent daemon (MCP connection + warm Ollama model) alive; output goes to logs\agent.log.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0..\.."
if not exist logs mkdir logs

".venv\Scripts\homelink-agentd.exe" >> logs\agent.log 2>&1
