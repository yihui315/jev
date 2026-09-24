@echo off
rem JCC Codex notify wrapper (P2, opt-in).
rem Forwards the Codex notify JSON to JCC, then chains the user's original notify.
rem Always exits 0 so a JCC outage never blocks Codex.
setlocal
set "JCC_ROOT=F:\jev"
if not exist "%JCC_ROOT%\.venv\Scripts\python.exe" exit /b 0
"%JCC_ROOT%\.venv\Scripts\python.exe" "%JCC_ROOT%\integrations\codex\jcc-codex-notify.py" %*
exit /b 0
