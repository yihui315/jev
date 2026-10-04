"""Run ONE gate leg through a nested PowerShell child and keep its output.

WHY. `measure_legs2.ps1` records rc=1 in ~0.6s for every leg with NO traceback
and no gate output at all. 0.6s is far too fast for any leg to do work, and the
silence means the child never got as far as the gate. So the failure is in how
the driver LAUNCHES the leg, not in the leg. Two candidate causes, both
plausible on this host, and neither distinguishable from rc=1 alone:

  a) the child cannot launch `.venv\Scripts\python.exe` at all, or
  b) it launches, the gate exits 1, and its output is being swallowed by the
     `2>&1` capture (the same PS 5.1 call-operator-with-pipeline trap that
     already bit this driver once).

This script separates them: it runs the same single leg twice, once capturing
output the way the driver does and once with the output written to a FILE by
the child, and prints both. It also runs a control that cannot legitimately
fail (`-c "print(1)"`), so a zero is distinguishable from a harness artefact.

One leg only, and the cheapest one, so the diagnostic stays fast.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PS51 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / \
    "WindowsPowerShell" / "v1.0" / "powershell.exe"
PY = REPO / ".venv" / "Scripts" / "python.exe"
GATE = REPO / "tools" / "final_gate_run_v1_3.py"
OUT = REPO / "artifacts" / "release" / "v1.3_final" / "single_leg_probe.txt"

ENV = {
    "CODEBUDDY_SAFE_DELETE_ENABLED": "0",
    "PYTHONPATH": str(REPO / "packages"),
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUNBUFFERED": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
    "TEMP": os.environ.get("TEMP", r"C:\Users\Administrator\AppData\Local\Temp"),
    "TMP": os.environ.get("TEMP", r"C:\Users\Administrator\AppData\Local\Temp"),
    "PATH": os.environ.get("PATH", ""),
}

# NOTE: built by str.replace, NOT str.format. The body is full of PowerShell
# `{ ... }` script blocks, and .format() treats every one of them as a
# placeholder -- KeyError: ' Write-Output ("GATE'. Same class of mistake as the
# re-indent no-op mutation: the templating, not the thing under test.
# WHY REDIRECT TO A FILE AND NOT `2>&1`.
# `& exe args 2>&1` inside `powershell -File` on PS 5.1 routes the child's
# stdout into the ERROR stream, so `$o` comes back empty -- an empty capture
# that reads exactly like "the child failed", when the child was fine.
#
# And the file redirection had its own bug, found by running it rather than by
# reasoning about it: `Get-Content -Raw` returned a System.Object[] here, so
# `.Trim()` raised MethodNotFound -- and because that line ran BEFORE the gate
# invocation, the exception terminated the script and the gate NEVER RAN. That
# is the actual reason all 16 legs "failed" in 0.6s: they were never launched.
# `($c | Out-String)` is used instead, which is string-safe either way, and the
# control is now printed BEFORE the gate so a harness fault can never again be
# mistaken for a gate result.
PS = """
$ErrorActionPreference = 'Continue'
$env:CODEBUDDY_SAFE_DELETE_ENABLED = '0'
$env:PYTHONPATH = 'D:\\jev\\packages'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$ctl = '__OUT__'
$g   = '__OUT__'
& '__PY__' -c "print('CONTROL-OK')" > $ctl 2>&1
$c = (Get-Content $ctl -ErrorAction SilentlyContinue | Out-String)
Write-Output ("CONTROL=[" + ($c -replace '\\s+',' ').Trim() + "]")
Write-Output "--- gate begins ---"
& '__PY__' '__GATE__' --only __LEG__ > $g 2>&1
Write-Output ("GATE_RC=" + $LASTEXITCODE)
$txt = Get-Content $g -ErrorAction SilentlyContinue
foreach ($line in $txt) { Write-Output ("GATE: " + [string]$line) }
Write-Output "DRIVER_END"
""".replace("__PY__", str(PY)).replace("__GATE__", str(GATE)) \
   .replace("__LEG__", "E1_FROZEN_TREE") \
   .replace("__OUT__", str(REPO / "artifacts" / "release" / "v1.3_final" /
                           "_child_capture.txt"))

SCRIPT = REPO / "artifacts" / "release" / "v1.3_final" / "_single_leg_probe.ps1"


def main() -> int:
    SCRIPT.write_text(PS, encoding="utf-8-sig", newline="\r\n")
    proc = subprocess.run(
        [str(PS51), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(SCRIPT)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=420, cwd=str(REPO), env=ENV)
    body = ("rc=%d\n--- STDOUT ---\n%s\n--- STDERR ---\n%s\n"
            % (proc.returncode, proc.stdout or "(empty)", proc.stderr or "(empty)"))
    print(body[:4000])
    OUT.write_text(body, encoding="utf-8", newline="\n")
    print("wrote %s" % OUT)
    if "DRIVER_END" not in (proc.stdout or ""):
        print("DIAGNOSIS: the child did not reach the end of the script")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
