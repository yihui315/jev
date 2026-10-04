"""Verify the uptime falsifier probe now emits PARSEABLE JSON, under real PS 5.1.

Run after editing tools/tests/fixtures/probes/uptime_falsifier.ps1. The probe
is invoked exactly the way tools/tests/test_status_uptime.py invokes it, its
output file is read back, and json.loads is attempted on the RAW bytes -- so
"the probe wrote something" and "the probe wrote something a reader can parse"
are two separate claims and only the second one is checked here.

Read-only apart from the probe's own scratch report, which goes to a temp dir.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PS51 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / \
    "WindowsPowerShell" / "v1.0" / "powershell.exe"
PROBE = REPO / "tools" / "tests" / "fixtures" / "probes" / "uptime_falsifier.ps1"


def main() -> int:
    if not PS51.is_file():
        print("SKIP: Windows PowerShell 5.1 not present")
        return 0
    # The probe exits 2 when there is no service, so a nonzero rc is expected and
    # is NOT the assertion. The assertion is that the report parses.
    with tempfile.TemporaryDirectory(prefix="jcc-uptime-probe-") as tmp:
        out = Path(tmp) / "uptime.json"
        proc = subprocess.run(
            [str(PS51), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(PROBE), "-SampleGapSec", "1", "-Samples", "1",
             "-OutPath", str(out)],
            cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
        print("probe rc      :", proc.returncode, "(2 = no service, expected here)")
        if proc.stderr.strip():
            print("probe stderr  :", proc.stderr.strip()[:500])
        if not out.is_file():
            print("VERDICT       : FAIL - the probe wrote no report at all")
            return 1
        raw = out.read_text(encoding="utf-8", errors="replace").strip()
        print("report bytes  :", raw[:200])
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        print("json.loads    : FAILS ->", exc)
        print("VERDICT       : FAIL - the probe still emits unparseable JSON")
        return 1
    print("json.loads    : OK ->", sorted(parsed))
    print("VERDICT       : PASS - the probe's report is machine-readable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
