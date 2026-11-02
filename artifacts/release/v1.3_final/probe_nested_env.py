"""Probe what a NESTED `powershell -File` child actually sees on this host.

WHY. `measure_legs2.ps1` recorded 16 legs at rc=1 in 0.5-0.9s each and an EMPTY
HEAD. 0.6s is far too fast for any leg to run, so those are not 16 real leg
failures -- they are the driver failing to launch anything. The HEAD line came
back empty because the same line shells out to `git`, and the traceback in
measure_legs2_stdout.txt shows the child dying inside
`subprocess.run(['git', ...])` at `CreateProcess`.

This host's Bash tool refuses to invoke powershell.exe directly (security), so
the probe runs from a script file, which is the pattern that has been working
all round. The question it answers is narrow and factual: does the nested child
inherit a PATH on which `git` and the venv python are resolvable?

A negative result here means the fix belongs in the driver (set an explicit PATH
before invoking anything), NOT in any gate leg.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PS51 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / \
    "WindowsPowerShell" / "v1.0" / "powershell.exe"
PROBE = REPO / "artifacts" / "release" / "v1.3_final" / "_probe_env.ps1"
OUT = REPO / "artifacts" / "release" / "v1.3_final" / "nested_ps_env.json"

BODY = "\n".join([
    r'$ErrorActionPreference = "Continue"',
    r'"PATH=[" + $env:PATH + "]"',
    r'"PATHEXT=[" + $env:PATHEXT + "]"',
    r'"SystemRoot=[" + $env:SystemRoot + "]"',
    r'"py_exists=" + (Test-Path ''D:\jev\.venv\Scripts\python.exe'')',
    r'$g = Get-Command git -ErrorAction SilentlyContinue',
    r'"git_resolved=" + [bool]$g',
    r'if ($g) { "git_path=" + $g.Source }',
    # The decisive test: can the child actually RUN the venv python?
    r'$o = & ''D:\jev\.venv\Scripts\python.exe'' -c "import sys;print(''py-ok'',sys.version.split()[0])" 2>&1',
    r'"child_result=[" + (($o | Out-String) -replace "\s+"," ").Trim() + "]"',
    r'"child_rc=" + $LASTEXITCODE',
])


def main() -> int:
    PROBE.write_text(BODY, encoding="utf-8-sig", newline="\r\n")
    proc = subprocess.run(
        [str(PS51), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(PROBE)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, cwd=str(REPO))
    print("rc=%d" % proc.returncode)
    print("--- STDOUT ---")
    print(proc.stdout or "(empty)")
    if proc.stderr.strip():
        print("--- STDERR ---")
        print(proc.stderr[:1200])
    OUT.write_text(
        json.dumps({"returncode": proc.returncode,
                    "stdout": proc.stdout, "stderr": proc.stderr[:2000]},
                   indent=2, ensure_ascii=False),
        encoding="utf-8", newline="\n")
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
