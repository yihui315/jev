"""Start the protected GREEN v1.0.1 service and record exactly what happened.

WHY A SEPARATE SCRIPT. The gate leg D1_GREEN_PROTECTION requires a listener on
127.0.0.1:4778 answering /api/health with actuation_enabled=false and
policy_version=p3.0.0. On this host there is none, so D1 (and the aggregate
GREEN_4778_CHANGED blocker) cannot be satisfied by any amount of testing. This
script performs the one documented start path -- scripts/start.ps1, which sets
JCC_CONFIG to the GREEN config itself -- and writes the full stdout/stderr and
exit code to artifacts so the attempt is auditable either way.

It starts a background service, so it is run explicitly, never at import.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PS51 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / \
    "WindowsPowerShell" / "v1.0" / "powershell.exe"
OUT = REPO / "artifacts" / "release" / "v1.3_final" / "green_start_attempt.txt"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(PS51), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(REPO / "scripts" / "start.ps1")],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    body = "rc=%d\n--- STDOUT ---\n%s\n--- STDERR ---\n%s\n" % (
        proc.returncode, proc.stdout or "", proc.stderr or "")
    OUT.write_text(body, encoding="utf-8", newline="\n")

    # Independent confirmation: is anything listening on 4778 now?
    net = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=60).stdout
    listening = [ln.strip() for ln in net.splitlines() if ":4778" in ln]
    print(body[:4000])
    print("--- 4778 AFTER START ---")
    print("\n".join(listening) if listening else "(no listener on 4778)")
    return 0 if listening else 1


if __name__ == "__main__":
    raise SystemExit(main())
