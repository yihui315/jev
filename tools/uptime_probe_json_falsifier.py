"""Show that the uptime falsifier's no_pid_file payload is not valid JSON.

The PowerShell probe at tools/tests/fixtures/probes/uptime_falsifier.ps1:18
builds its report by STRING CONCATENATION and interpolates a Windows path:

    '{"error":"no_pid_file","pid_file":"' + $Paths.PidFile + '"}'

On Windows that path is D:\\jev\\runtime\\jcc\\jcc.pid, so the emitted document
contains the two-character sequence \\j, which is not a legal JSON escape.
Python's json.loads rejects it: "Invalid \\escape: line 1 column 38".

This is the SAME mistake scripts/ensure-stable-runtime.ps1 documents at length
in its own beacon writer ("THE LINE IS BUILT BY CONCATENATION, NOT WITH -f, ON
PURPOSE ... Both were the same mistake: inventing an encoding by hand"). One
writer was fixed; this one was not.

Read-only: parses strings, touches no product state.
"""
from __future__ import annotations

import json

# What PowerShell writes: concatenation, so the path keeps SINGLE backslashes.
emitted = '{"error":"no_pid_file","pid_file":"' + "D:\\jev\\runtime\\jcc\\jcc.pid" + '"}'

print("emitted document :", emitted)
try:
    json.loads(emitted)
    print("json.loads       : OK")
    print("VERDICT          : the probe output is valid JSON -- nothing to fix")
except json.JSONDecodeError as exc:
    print("json.loads       : FAILS ->", exc)
    print("VERDICT          : the probe emits INVALID JSON on any Windows path")
    print("                    because the backslash path is not escaped.")

print()
print("what a real serialiser emits for the same data:")
print("                 ", json.dumps(
    {"error": "no_pid_file", "pid_file": "D:\\jev\\runtime\\jcc\\jcc.pid"}))
