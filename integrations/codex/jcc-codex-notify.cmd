@echo off
rem JCC Codex notify wrapper (P2, opt-in).
rem Forwards the Codex notify JSON to JCC, then chains the user's original notify.
rem Always exits 0 so a JCC outage never blocks Codex.
rem
rem JCC_ROOT is taken from the environment when the operator set it, otherwise
rem derived from this file's own location (%~dp0..\.. = repo root). It is NOT a
rem hardcoded drive letter: the checkout moved, and a literal silently turned
rem this wrapper into a no-op that exited 0 forever.
setlocal
if not defined JCC_ROOT set "JCC_ROOT=%~dp0..\.."
for %%I in ("%JCC_ROOT%") do set "JCC_ROOT=%%~fI"
if not exist "%JCC_ROOT%\.venv\Scripts\python.exe" exit /b 0
"%JCC_ROOT%\.venv\Scripts\python.exe" "%JCC_ROOT%\integrations\codex\jcc-codex-notify.py" %*
exit /b 0