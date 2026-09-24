@echo off
rem JCC Claude Code hook wrapper (P2).
rem Reads the Claude hook JSON from stdin and forwards it to the JCC emitter.
rem Always exits 0 so a JCC outage never blocks the agent.
setlocal
set "JCC_ROOT=F:\jev"
if not exist "%JCC_ROOT%\.venv\Scripts\python.exe" exit /b 0
"%JCC_ROOT%\.venv\Scripts\python.exe" "%JCC_ROOT%\integrations\_common\jcc_emit.py" --agent claude
exit /b 0
