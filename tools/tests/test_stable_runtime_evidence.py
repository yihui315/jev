"""Falsifier for the GREEN runtime watchdog's EVIDENCE CHANNEL.

WHAT THIS SUITE IS FOR. On 2026-09-27 the two scheduled tasks that are supposed
to keep 127.0.0.1:4778 alive were installed, enabled, owned by SYSTEM, repeating
every two minutes -- and producing nothing. Measured:

    tick 22:36:41  STALE_MARKER -> START -> RECOVERED (exit 2)     [worked]
    tick 22:37:41  LastTaskResult=1, log 6211 bytes, no audit line
    tick 22:39:39  LastTaskResult=1, log 6211 bytes, no audit line
    tick 22:41:41  LastTaskResult=1, log 6211 bytes, no audit line

The log's silence read as health, which is the whole reason a watchdog exists.

ROOT CAUSE. The task action wrapped the script in cmd.exe with
`>> "<log>" 2>&1`. The script LAUNCHES a detached service that outlives it, and
Windows handle inheritance hands the service a copy of every inheritable handle
the tick holds. cmd opens a redirect target with FILE_SHARE_READ, so the tick
that launched a service locked its own log against every later tick: cmd could no
longer open its own redirect, never started PowerShell at all, and exited 1
having written nothing. Measured with the service up: an append-open of that file
failed under FileShare None AND Read AND ReadWrite while the service launcher was
alive.

A piece of state that a process hands to the thing it is watching is not
evidence. These tests hold that line:

  1. A tick must WRITE its own beacon. (A DryRun tick is driven for real; if the
     beacon is a no-op -- which it was, briefly, because its JSON template used
     PowerShell's -f operator on literal braces and its own catch ate the
     FormatException -- this fails.)
  2. A registered task action must contain NO redirection, file or pipe. Read
     from the task store's own XML, which is the oracle that does not depend on
     CIM or the ScheduledTasks module.
  3. With the live service running, the canonical durable log must still be
     append-openable. This is the regression property itself.

SCOPE. Nothing here starts, stops, restarts or signals the live service, and
nothing writes to a tracked path: the only writes are the append-only evidence
lines under runtime/logs/, which is untracked by design.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ensure-stable-runtime.ps1"
LOGS = REPO / "runtime" / "logs"
BOOT_LOG = LOGS / "stable-runtime-boot.jsonl"
DURABLE_LOG = LOGS / "stable-runtime-watchdog.log"
TASK_STORE = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "Tasks"
PS51 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / \
    "WindowsPowerShell" / "v1.0" / "powershell.exe"

STABLE_TASKS = ("JEV-JCC-Stable-Runtime", "JEV-JCC-Stable-Health")


def _ps(args, timeout=180, attempts=3):
    """Run PS 5.1 with a bounded retry for this host's memory fault.

    A process that died inside the CLR is not a verdict about the product.
    Same rule as test_status_uptime / test_continuation_infra.
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
        "Windows PowerShell 5.1 died inside the runtime on all %d attempt(s) (%s) "
        "with no measurement produced. This is this host's documented memory "
        "fault, not a verdict about the product -- re-run." %
        (attempts, died or "STATUS_ACCESS_VIOLATION (0xC0000005)"))


def _read_jsonl(path: Path):
    """Return (records, unparseable_lines).

    Unparseable lines are RETURNED rather than skipped silently. Skipping them
    is how a file that is growing but unreadable gets reported as "no new
    records" -- which is what happened on 2026-09-27 when the beacon
    hand-concatenated its JSON and every line carried an illegal \\A escape from
    the identity string "YH\\Administrator". Evidence that cannot be parsed is
    not evidence, so the count is asserted on rather than discarded.
    """
    if not path.is_file():
        return [], []
    out, bad = [], []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            bad.append(line[:200])
    return out, bad


def _append_openable(path: Path, attempts=5, gap=0.4):
    """Can this file be opened for append RIGHT NOW?

    FileShare.ReadWrite is the meaningful probe: cmd.exe's `>>` opens a redirect
    target with FILE_SHARE_READ, so it denies write access to everyone else and
    this open fails. Retried a bounded number of times because the fixed design
    writes in open/close bursts, so a concurrent scheduled tick can overlap for
    microseconds. A REAL lock -- the defect being guarded against -- is held for
    the lifetime of the service, so a ~2 second window cannot mask it; the retry
    removes flakiness without weakening the claim.
    """
    last = None
    for _ in range(attempts):
        try:
            handle = open(path, "ab")
            handle.close()
            return True, None
        except OSError as exc:              # PermissionError / sharing violation
            last = exc
            time.sleep(gap)
    return False, last


def test_dry_run_tick_writes_a_boot_beacon():
    """A tick must leave evidence that it ran at all, before anything can throw.

    This is the falsifier for the beacon itself. It was briefly a no-op: the
    first version built its JSON with '{"at":"{0}",...}' -f ..., PowerShell read
    the literal braces as format items, FormatException was raised, and the
    function's own catch swallowed it. Measured: the script ran, printed NOOP,
    and stable-runtime-boot.jsonl was never created.

    A DryRun tick is used so the live service is never touched: -DryRun exits on
    the decision without stopping, starting or signalling anything.
    """
    assert SCRIPT.is_file(), "the restore action is missing: %s" % SCRIPT
    prior, _ = _read_jsonl(BOOT_LOG)
    before = len(prior)

    proc = _ps(["-File", str(SCRIPT), "-Watchdog", "-DryRun"])
    # 0 = decided, did nothing / 2 = dry-run would recover. Anything else means
    # the tick failed and the beacon is expected to say why.
    assert proc.returncode in (0, 2), (
        "DryRun tick did not complete cleanly (rc=%d)\nstdout=%s\nstderr=%s" %
        (proc.returncode, (proc.stdout or "")[:1000], (proc.stderr or "")[:1000]))

    records, bad = _read_jsonl(BOOT_LOG)
    assert not bad, (
        "%d beacon line(s) in runtime/logs/stable-runtime-boot.jsonl are not "
        "valid JSON, so the tick's evidence cannot be read back. First bad "
        "line: %r" % (len(bad), bad[0]))

    new = records[before:]
    assert new, (
        "the tick wrote NO beacon line: runtime/logs/stable-runtime-boot.jsonl "
        "went from %d to %d records. A tick with no evidence that it started is "
        "indistinguishable from a tick that never ran." % (before, len(records)))

    stages = {r.get("stage") for r in new}
    assert "enter" in stages, (
        "the beacon has no 'enter' line -- the script body was not entered, or "
        "the beacon is not a real instrument (stages seen: %r)" % sorted(stages))
    assert "preflight-ok" in stages, (
        "the tick died during preflight: it reached 'enter' but never got past "
        "parameter binding / dot-sourcing / root resolution / interpreter "
        "discovery / config port read. Exception text is in the beacon, or the "
        "beacon's 'caught' line is missing too (stages seen: %r)" % sorted(stages))
    assert not any(r.get("stage") == "caught" for r in new), (
        "the tick raised: %r" % [r.get("detail") for r in new if r.get("stage") == "caught"])


def test_registered_task_actions_contain_no_redirection():
    """Neither task may redirect its output -- not to a file, not to a pipe.

    Read from C:\\Windows\\System32\\Tasks\\<name>, the task store's own XML. The
    ScheduledTasks CIM provider throws ERROR_FILE_NOT_FOUND on this host and the
    module's query has already been observed to fold a refusal into "absent", so
    the XML is the oracle that does not depend on it.

    Three states, kept distinct: an unreadable task STORE is a failure (we could
    not look), while a genuinely absent task XML is a skip (this host has not
    installed the watchdog). Folding "could not query" into "does not exist" is
    the defect this project has already paid for once.
    """
    if not TASK_STORE.is_dir():
        raise AssertionError(
            "the task store %s could not be listed, so nothing was measured -- "
            "that is not the same as 'no task is registered'" % TASK_STORE)

    present = [n for n in STABLE_TASKS if (TASK_STORE / n).is_file()]
    if not present:
        pytest.skip("neither stable-runtime task is registered on this host: %s" %
                    ", ".join(STABLE_TASKS))

    problems = []
    for name in present:
        xml = (TASK_STORE / name).read_text(encoding="utf-16", errors="replace")
        lower = xml.lower()
        if ">>" in xml or "2>&1" in lower:
            problems.append("%s redirects to a file" % name)
        # A bare trailing pipe would be the mirror-image trap: the inherited
        # write end keeps the reader waiting forever and the task never leaves
        # "Running", after which MultipleInstances=IgnoreNew suppresses every
        # later tick.
        if "|" in xml.split("<arguments>")[-1].split("</arguments>")[0]:
            problems.append("%s pipes its output" % name)
        if "<command>cmd.exe" in lower or "\\cmd.exe</command>" in lower:
            problems.append("%s still runs through cmd.exe" % name)
        if "ensure-stable-runtime.ps1" not in xml:
            problems.append("%s does not run ensure-stable-runtime.ps1" % name)
    assert not problems, (
        "a registered task reintroduced the self-locking action: %s\n"
        "The evidence channel must not be handed to the process being watched."
        % "; ".join(problems))


def test_live_durable_log_is_still_append_openable():
    """The regression property: the watchdog's own log must stay writable.

    Skipped when no service answers, because a stopped service means the tick is
    not running either and there is nothing to assert. When GREEN is up this is
    the exact claim that was false before the fix.
    """
    health = "http://127.0.0.1:4778/api/health"
    proc = _ps(["-Command",
                "try { $r = Invoke-RestMethod -Uri '%s' -TimeoutSec 5; "
                "if ($r.status -eq 'ok') { 'UP' } else { 'NOTOK' } } "
                "catch { 'DOWN' }" % health])
    state = (proc.stdout or "").strip()
    if state != "UP":
        pytest.skip("no healthy service on 4778 (%s); nothing to assert" % (state or "no answer"))

    assert DURABLE_LOG.is_file(), (
        "GREEN is up but the watchdog has no durable log at %s" % DURABLE_LOG)

    ok, exc = _append_openable(DURABLE_LOG)
    assert ok, (
        "runtime/logs/stable-runtime-watchdog.log is NOT append-openable while "
        "GREEN is running (%s). This is the 2026-09-27 defect: a task-level "
        "redirect leaked an inheritable handle into the detached service, the "
        "file stayed locked for the service's lifetime, and every later tick "
        "died with LastTaskResult=1 having written nothing." % exc)
