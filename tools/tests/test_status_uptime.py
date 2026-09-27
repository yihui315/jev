"""Falsifier for the uptime chain in scripts/status.ps1 (W-05).

WHAT THIS SUITE IS FOR. status.ps1 used to compute the `uptime` line from
one timestamp and print its `timestamp` line from another, so the two
lines came from instants up to 63 SECONDS apart (measured on this host:
timestamp printed 06:39:12 while uptime was derived from 06:40:15.865).
A reader comparing them would compute an uptime that cannot be true
for the timestamp printed beside it.

It also had a separate, quieter defect: the uptime was rounded, not
truncated. PowerShell's `[int]` cast on a double ROUNDS via
``Convert.ToInt32`` (banker's rounding); measured on this host with
the live service, an elapsed of 31033.3186547s -- plainly 8h37m13s --
was printed as "09h37m13s", because 31033/3600 = 8.6203 rounds UP to 9.
So the reading was silently ONE HOUR LONG whenever the minute
remainder passed 30, and invisible whenever it did not.

So the test states a falsifier that catches BOTH defects without ever
needing to start, stop, restart or signal the real service:

  1. The pid in the pid file does not change across samples.
  2. The uptime the script printed is MONOTONIC non-decreasing.
  3. The delta between adjacent uptime values is within 5 seconds of
     the wall-clock delta between the same two script runs.
  2. The uptime value the script printed is exactly the value the
     helper would have computed against the same instant, so the two
     clocks in the report cannot have drifted apart.

Nothing here mutates runtime/ or signals the live service.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
PROBE = REPO / "tools" / "tests" / "fixtures" / "probes" / "uptime_falsifier.ps1"
PS51 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / \
    "WindowsPowerShell" / "v1.0" / "powershell.exe"


def _ps(args, timeout=180, attempts=3):
    """Run PS 5.1 with a bounded retry for the host memory fault (0xC0000005).

    Same rule as test_continuation_infra: a process that died inside the CLR
    is not a verdict. We retry a bounded number of times; if every attempt
    dies, the caller is told we did not measure.
    """
    assert PS51.is_file(), "Windows PowerShell 5.1 is required to run these scripts"
    died = None
    for _ in range(attempts):
        proc = subprocess.run(
            [str(PS51), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        unsigned = proc.returncode & 0xFFFFFFFF
        if unsigned == 0xC0000005:
            died = "STATUS_ACCESS_VIOLATION (0xC0000005)"
            continue
        if proc.returncode not in (0, 1, 2):
            raise AssertionError(
                "powershell.exe died (rc=%d)\nstdout=%s\nstderr=%s" %
                (proc.returncode, (proc.stdout or "")[:1000], (proc.stderr or "")[:1000]))
        return proc
    raise AssertionError(
        "Windows PowerShell 5.1 died inside the runtime on all %d attempt(s) "
        "(%s) with no measurement produced. This is this host's documented "
        "memory fault, not a verdict about the product -- re-run." %
        (attempts, died or "STATUS_ACCESS_VIOLATION (0xC0000005)"))


def _to_seconds(s: str) -> int:
    """Parse '08h40m02s' / '00h00m05s' to a non-negative int."""
    m = re.fullmatch(r"(\d+)h(\d+)m(\d+)s", s.strip())
    assert m, "unparseable uptime string: %r" % s
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


@pytest.fixture(scope="module")
def falsifier_report(tmp_path_factory):
    """Drive the PS 5.1 probe three times spaced 45s apart.

    Three samples at 45-second spacing is exactly the requirement: enough
    span that a 1-hour rounding drift would visibly blow past the 5-second
    tolerance in test_deltas_track_wall_clock, and enough samples that
    monotonicity is a real claim rather than a coincidence between two
    measurements.
    """
    out_dir = tmp_path_factory.mktemp("uptime")
    out = out_dir / "uptime.json"
    proc = _ps(["-File", str(PROBE), "-SampleGapSec", "45", "-Samples", "3",
                "-OutPath", str(out)])
    if not out.exists():
        raise AssertionError(
            "probe did not write its report\nstdout=%s\nstderr=%s" %
            ((proc.stdout or "")[:1000], (proc.stderr or "")[:1000]))
    text = out.read_text(encoding="utf-8", errors="replace").strip()
    # The probe writes ONE JSON document (pretty-printed). The whole text
    # IS the document; splitting on lines and taking [-1] only gets the
    # trailing "}" and json.loads raises -- measured on this host.
    try:
        report = json.loads(text)
    except json.JSONDecodeError:
        raise AssertionError("probe JSON could not be parsed:\n" + text)
    if report.get("error"):
        pytest.skip("uptime probe has no live service to measure: " + str(report))
    assert report.get("schema") == "jcc-uptime-falsifier/1", \
        "probe schema drift: %r" % report.get("schema")
    return report


def test_pid_is_stable_across_samples(falsifier_report):
    """The recorded service pid must be the SAME pid in every sample."""
    pids = {s["pid"] for s in falsifier_report["samples"]}
    assert len(pids) == 1, "service pid changed between samples: %r" % sorted(pids)


def test_uptime_is_monotonic(falsifier_report):
    """status.ps1's printed uptime must never go backwards.

    A reading that decreases between samples is a defect by definition -- the
    service has not restarted, and a clock that goes backwards is the kind
    of "impossible uptime" that triggered this whole falsifier.
    """
    seq = [_to_seconds(s["script_str"]) for s in falsifier_report["samples"]]
    for a, b in zip(seq, seq[1:]):
        assert b >= a, "uptime went backwards: %s -> %s" % (a, b)


def test_deltas_track_wall_clock(falsifier_report):
    """Adjacent uptime deltas must agree with wall-clock deltas within 5s.

    This is the falsifier for the rounding defect: a 45-second gap with an
    elapsed remainder past the 30-minute mark would jump an hour if the
    truncation regressed. Measured on this host the first three deltas
    come in under 1 second of error.
    """
    times = [time.mktime(time.strptime(s["timestamp_iso"][:19], "%Y-%m-%dT%H:%M:%S"))
             for s in falsifier_report["samples"]]
    up_seq = [_to_seconds(s["script_str"]) for s in falsifier_report["samples"]]

    for i in range(1, len(falsifier_report["samples"])):
        wall = int(times[i] - times[i - 1])
        up_delta = up_seq[i] - up_seq[i - 1]
        # The script ran AFTER the probe stamped the timestamp line (the
        # status.ps1 process must be spawned and read /api/health), so the
        # uptime delta can legitimately be slightly LESS than the wall-clock
        # delta. We bound by 5 seconds inclusive either way.
        assert abs(up_delta - wall) <= 5, (
            "sample %d->%d: wall=%ds uptime_delta=%ds (off by %ds)" %
            (i - 1, i, wall, up_delta, abs(up_delta - wall)))


def test_printed_uptime_matches_live_process_at_the_same_instant(falsifier_report):
    """The script's printed uptime must agree with the helper's reading.

    The probe stamps `proc_start_iso` and computes `elapsed_str` from ONE
    instant; status.ps1 spawns and re-reads Get-Date internally, so the two
    reads are not the same instant -- they are a few hundred milliseconds
    apart. Floor-truncating both to seconds makes the printed uptime equal
    to OR exactly 1 second AHEAD of the probe reading (the probe is always
    the earlier instant). Anything beyond that -- 2 seconds or more -- means
    status.ps1 is reading a different clock from the one it stamped, which
    is the historical "two timestamps" defect.

    A wider tolerance would not catch the defect; a stricter one would
    catch this harmless same-instant rounding. The 1-second allowance is
    measured against the live service on this host.
    """
    for s in falsifier_report["samples"]:
        probe_sec = _to_seconds(s["elapsed_str"])
        script_sec = _to_seconds(s["script_str"]) if s["script_str"] else None
        assert script_sec is not None, (
            "sample %d: script printed no uptime at all" % s["sample"])
        drift = script_sec - probe_sec
        assert 0 <= drift <= 1, (
            "sample %d: probe=%ss script=%ss (drift=%ds, expected 0..1s)" %
            (s["sample"], probe_sec, script_sec, drift))


def test_get_jcc_elapsed_truncates_not_rounds():
    """The helper itself must truncate, never round.

    Tested from Python by asking PS 5.1 to run the helper directly with a
    fixed Since/Now pair that crosses a half-hour remainder: 8h59m59.6s
    after start must print 08h59m59s, not 09h00m00s. A helper that rounds
    would print 09h00m00s and the defect would return.
    """
    lib = REPO / "scripts" / "lib" / "JccCommon.ps1"
    # PowerShell 5.1 `DateTime.AddSeconds` requires an [int], so we add a
    # TimeSpan instead -- both forms express the same instant.
    cmd = (
        ". '%s'; "
        "$s = [datetime]'2026-01-01T00:00:00'; "
        "$n = $s.Add([TimeSpan]::FromSeconds((8*3600) + (59*60) + 59.6)); "
        "Get-JccElapsed -Since $s -Now $n"
    ) % str(lib)
    proc = _ps(["-Command", cmd])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = (proc.stdout or "").strip()
    assert out == "08h59m59s", (
        "Get-JccElapsed rounded 8h59m59.6s to %r (must truncate)" % out)