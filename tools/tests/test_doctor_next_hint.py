"""Falsifiers for the doctor "next:" hint and the Get-JccNextHint helper.

WHAT THIS SUITE IS FOR. The doctor at scripts/doctor.ps1 used to print

    next      : .\\scripts\\start.ps1

unconditionally after a green run, so a host where the service was already
UP -- which is what "RESULT: PASS" usually means -- was told to start it
again. Taken literally, that hint is a maintenance restart of a healthy
instance. The same line was printed even when the listening socket was
held by a FOREIGN process (different pid, different repo), where start.ps1
would refuse and where telling an operator to cover another process's
listener is actively unsafe.

The fix is Get-JccNextHint in scripts/lib/JccCommon.ps1, which is a pure
function over the port state measured in section 6 of the doctor. The
three cases below assert the right string in every state, and the fourth
asserts the doctor script wires it to the printed line on this live host.

Nothing here restarts or signals the live service. The FREE / FOREIGN
branches are evaluated against the pure helper, not by exercising the
start path.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
LIB = SCRIPTS / "lib" / "JccCommon.ps1"
PROBE = REPO / "tools" / "tests" / "fixtures" / "probes" / "next_hint_probe.ps1"
PS51 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / \
    "WindowsPowerShell" / "v1.0" / "powershell.exe"


def _ps(args, timeout=180):
    assert PS51.is_file(), "Windows PowerShell 5.1 is required to run these scripts"
    proc = subprocess.run(
        [str(PS51), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )
    if proc.returncode not in (0, 1):
        raise AssertionError(
            "powershell.exe died (rc=%d)\nstdout=%s\nstderr=%s" %
            (proc.returncode, (proc.stdout or "")[:1000], (proc.stderr or "")[:1000]))
    return proc


def _read_probe(scratch_dir: Path):
    """Drive the PS 5.1 probe and return its JSON report."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    out = scratch_dir / "nh_probe.json"
    err = scratch_dir / "nh_probe.err.txt"
    # `-File` mode: $PSScriptRoot is the file's directory, so the probe's
    # `..\..\scripts\lib\JccCommon.ps1` reaches the right place.
    proc = _ps(["-File", str(PROBE), "-OutPath", str(out)])
    err_text = ""
    if err.exists():
        err_text = err.read_text(encoding="utf-8", errors="replace")
    if not out.exists():
        raise AssertionError(
            "next-hint probe did not write its report.\nstdout=%s\nstderr=%s" %
            ((proc.stdout or "")[:1000], (err_text or "")[:1000]))
    text = out.read_text(encoding="utf-8", errors="replace").strip()
    # The probe writes ONE JSON document (pretty-printed), so the whole text
    # IS the document. We do not splitlines()/[-1]: in pretty mode that picks
    # up only the trailing "}" and json.loads raises -- measured on this host.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise AssertionError("probe JSON could not be parsed:\n" + text)


# -- Pure helper ---------------------------------------------------------- #

def test_ours_says_do_not_start():
    """When the listener is OURS, the hint must not propose starting again.

    Probed shape: no mention of "start.ps1" and an explicit "already running".
    A passing OURS hint that nevertheless contains "start.ps1" would be the
    historical bug, so the test is exactly that.
    """
    r = _read_probe(Path(os.environ.get("TEMP", r"C:\Users\Administrator\AppData\Local\Temp")) / "jev-pytest" / "nhh")
    ours = (r.get("ours") or "").strip()
    assert ours, "OURS hint was empty"
    assert "already running" in ours, "OURS hint must explicitly say it is already running"
    assert "start.ps1" not in ours.lower(), (
        "OURS hint must NOT propose start.ps1 (got: %r)" % ours)


def test_free_proposes_start():
    """When nothing listens, the hint must point at start.ps1.

    This is the case the previous unconditional print got right -- the test
    pins it down so a future OURS override does not regress FREE.
    """
    r = _read_probe(Path(os.environ.get("TEMP", r"C:\Users\Administrator\AppData\Local\Temp")) / "jev-pytest" / "nhh")
    free = (r.get("free") or "").strip()
    assert free, "FREE hint was empty"
    assert "start.ps1" in free, "FREE hint must point at start.ps1 (got: %r)" % free


def test_foreign_must_not_suggest_starting_over_it():
    """When a FOREIGN pid owns the port, the hint must refuse to start.

    Start.ps1 would refuse too -- so the hint telling the operator to
    "just start it" is the dangerous shape: it covers someone else's
    listener. The exact wording is left open ("do NOT run ... start.ps1")
    so the assertion is "start.ps1 must appear next to a refusal".
    """
    r = _read_probe(Path(os.environ.get("TEMP", r"C:\Users\Administrator\AppData\Local\Temp")) / "jev-pytest" / "nhh")
    foreign = (r.get("foreign") or "").strip()
    assert foreign, "FOREIGN hint was empty"
    assert "start.ps1" in foreign, (
        "FOREIGN hint must name start.ps1 to be a refusal, got: %r" % foreign)
    assert re.search(r"do\s*not", foreign, re.IGNORECASE), (
        "FOREIGN hint must refuse to start, got: %r" % foreign)


# -- End-to-end: doctor.ps1 on this live host ----------------------------- #

def test_doctor_on_this_host_does_not_suggest_starting_an_up_service():
    """Run the real doctor.ps1 under PS 5.1 and read its `next :` line.

    The probe runs doctor end-to-end on this host and reports what was
    printed. The service is currently UP (pid 9952, port 4778). The printed
    hint MUST therefore match OURS -- if it ever matches FREE on this live
    state, the wiring in doctor.ps1 is broken regardless of how correct the
    helper is.
    """
    r = _read_probe(Path(os.environ.get("TEMP", r"C:\Users\Administrator\AppData\Local\Temp")) / "jev-pytest" / "nhh")
    next_printed = (r.get("doctor_next_printed") or "").strip()
    exit_code = r.get("doctor_exit")
    assert exit_code == 0, "doctor did not exit 0 on this host: %r" % exit_code
    assert next_printed, "doctor printed no `next :` line"
    ours = (r.get("ours") or "").strip()
    assert next_printed == ours, (
        "doctor printed %r but the OURS hint is %r -- the wiring in doctor.ps1 "
        "no longer matches the helper" % (next_printed, ours))


def test_doctor_does_not_change_pass_fail_semantics():
    """The next-hint change must not move doctor.ps1 off exit 0 / 9 PASS.

    The historical bug was a wording issue; the FIX is a wording issue; a
    real regression would be doctor.ps1 starting to report 8/9 PASS or
    exit 1 because the new code path broke the count. We do not need to
    re-run the whole 9-check matrix -- the probe already runs the doctor
    and records `doctor_exit`. A non-zero exit on this healthy host would
    be the regression.
    """
    r = _read_probe(Path(os.environ.get("TEMP", r"C:\Users\Administrator\AppData\Local\Temp")) / "jev-pytest" / "nhh")
    assert r.get("doctor_exit") == 0, (
        "doctor.ps1 exited %r on this healthy host -- the next-hint change "
        "must not have changed PASS/FAIL semantics" % (r.get("doctor_exit"),))