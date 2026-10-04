"""Report the host's scheduled-task inventory, JCC entries separately.

WHY. Two C2 failures assert that the JCC watchdogs ARE registered as Windows
scheduled tasks:

  * test_both_watchdogs_are_registered_as_scheduled_tasks
  * test_the_registration_verdict_refuses_a_disabled_or_misaimed_task

Both read the REAL host task store (C:\\Windows\\System32\\Tasks). The
adjudication question -- "product defect, or test asserting a host state that
was never established?" -- is answered by what is actually registered here, so
this probe reads it rather than inferring it. It is read-only: it queries
Get-ScheduledTask and never registers, modifies, unregisters or signals
anything.

Writes its report to artifacts/release/v1.3_final/task_probe_result.txt.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PS51 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / \
    "WindowsPowerShell" / "v1.0" / "powershell.exe"
OUT = REPO / "artifacts" / "release" / "v1.3_final" / "task_probe_result.txt"

SCRIPT = r"""
$ErrorActionPreference = 'Continue'
$all = @(Get-ScheduledTask -ErrorAction SilentlyContinue)
Write-Output ("TOTAL_TASKS=" + $all.Count)
$jcc = @($all | Where-Object { $_.TaskName -like '*JCC*' -or $_.TaskName -like '*jev*' })
Write-Output ("JCC_TASKS=" + $jcc.Count)
foreach ($t in $jcc) {
    Write-Output ("  TASK=" + $t.TaskName + " STATE=" + $t.State + " PATH=" + $t.TaskPath)
}
$named = @('JEV-JCC-Continuation-Watchdog', 'JEV-JCC-Service-Watchdog')
foreach ($n in $named) {
    $found = $all | Where-Object { $_.TaskName -eq $n }
    Write-Output ("LOOKUP[" + $n + "]=" + $(if ($found) { 'PRESENT' } else { 'ABSENT' }))
}
"""


def main() -> int:
    proc = subprocess.run(
        [str(PS51), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", SCRIPT],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    body = "rc=%d\n--- STDOUT ---\n%s\n--- STDERR ---\n%s\n" % (
        proc.returncode, proc.stdout or "", proc.stderr or "")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(body, encoding="utf-8", newline="\n")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
