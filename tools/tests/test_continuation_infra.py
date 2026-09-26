"""Falsifiers for the FINAL-phase execution infrastructure (FINAL-01).

WHAT THIS SUITE IS FOR. The release is delivered by a pipeline that must keep
running when nobody is watching: a controller that persists its own progress, a
scheduled task that restarts it when it dies, and a second watchdog that keeps
the service up. Every one of those is a mechanism whose failure mode is silence
-- a watchdog that never fires looks exactly like a watchdog with nothing to do,
and a controller that never advances looks exactly like a controller waiting.

So each test below states a way the mechanism could be silently useless and
fails if it is. The three that matter most:

  * ``test_exit_zero_without_evidence_is_not_progress`` -- the controller must
    not accept "the child exited 0" as a step result.
  * ``test_live_controller_is_never_duplicated`` / ``test_stale_lock_restarts``
    -- the watchdog's decision rules, driven against a SCRATCH root so no test
    ever races, starts or stops the real pipeline.
  * ``test_service_watchdog_refuses_foreign_occupancy`` -- the one rule that
    stops a watchdog from becoming an outage by killing a stranger's process.

Nothing here starts, stops or signals the real service or the real controller.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONTROLLER = REPO / "tools" / "jcc_continuation.py"
SCRIPTS = REPO / "scripts"
PS51 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / \
    "WindowsPowerShell" / "v1.0" / "powershell.exe"

CONTINUATION_TASK = "JEV-JCC-Continuation-Watchdog"
SERVICE_TASK = "JEV-JCC-Service-Watchdog"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ctl():
    """The controller module, loaded from its path (it is a script)."""
    return _load(CONTROLLER, "_jcc_controller")


#: NTSTATUS values that mean "the process died inside the runtime", not "the
#: program decided something".
_NATIVE_DEATH = {
    0xC0000005: "STATUS_ACCESS_VIOLATION",
    0xC0000006: "STATUS_IN_PAGE_ERROR",
    0xC0000017: "STATUS_NO_MEMORY",
    0xC0000374: "STATUS_HEAP_CORRUPTION",
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN",
    0xC0000135: "STATUS_DLL_NOT_FOUND",
}
#: Bounded, and small on purpose: this is for a host fault, not for flakiness.
_PS_ATTEMPTS = 3


def _native_death(proc):
    """The NTSTATUS name when PowerShell died inside the runtime, else None."""
    if proc.returncode is None:
        return None
    unsigned = proc.returncode & 0xFFFFFFFF
    if unsigned in _NATIVE_DEATH:
        return "%s (0x%08X)" % (_NATIVE_DEATH[unsigned], unsigned)
    return None


def _ps(args, timeout=180, attempts=_PS_ATTEMPTS):
    """Run Windows PowerShell 5.1 -- the interpreter the product's scripts get.

    WITH A BOUNDED RETRY FOR A HOST FAULT, AND NOT AS AN EXCUSE. Measured
    2026-09-26 from the continuation controller: three consecutive suite runs each
    failed on a DIFFERENT service-watchdog test, every time with
    ``returncode=3221225477`` (0xC0000005 STATUS_ACCESS_VIOLATION), every time
    inside powershell.exe with an EMPTY stdout and a CLR stack trace
    (``ContextCallback``/``ThreadHelper.ThreadStart``), and every time passing
    when the same test was run on its own. A process killed inside the CLR has not
    decided anything -- and this is the same signature as the 0xC0000005 death of
    ``apps/control_ui/tests`` recorded earlier in this session, on a host whose
    memory subsystem has been measured to corrupt data under bandwidth load
    (5-34 byte errors in 9 of 9 blocks).

    So the retry buys a real measurement instead of an environment reading. It is
    BOUNDED, and when every attempt dies the caller gets an AssertionError naming
    the NTSTATUS: a run that measured nothing must never be reported as a pass,
    which is the rule this entire project is built on. Nothing here relaxes an
    assertion about the product.
    """
    assert PS51.is_file(), "Windows PowerShell 5.1 is required to run these scripts"
    died_with = None
    for _ in range(attempts):
        proc = subprocess.run(
            [str(PS51), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        died_with = _native_death(proc)
        if died_with is None:
            return proc
    raise AssertionError(
        "Windows PowerShell 5.1 died inside the runtime on all %d attempt(s) (%s) "
        "with no measurement produced. This is this host's documented memory fault, "
        "not a verdict about the product -- re-run. Not reported as a product "
        "failure, and not reported as a pass either." % (attempts, died_with))


def _ps_json(command: str):
    proc = _ps(["-Command", command])
    text = (proc.stdout or "").strip()
    assert text, "no output from: %s\nstderr=%s" % (command, (proc.stderr or "")[:500])
    return json.loads(text.splitlines()[-1]), proc


def test_a_powershell_crash_is_never_read_as_a_verdict():
    """0xC0000005 must be classified as "no measurement", not as a product result.

    Measured: three consecutive suite runs under the controller each failed a
    DIFFERENT service-watchdog test with returncode 3221225477 and an empty
    stdout. If that were merely compared to the expected exit code, the suite
    would report a product failure -- and the temptation would be to relax the
    assertion. The classification is therefore explicit and falsified here.
    """

    class _P:
        def __init__(self, rc):
            self.returncode = rc

    assert _native_death(_P(3221225477)) == "STATUS_ACCESS_VIOLATION (0xC0000005)"
    # the same value as subprocess reports it when interpreted as signed
    assert _native_death(_P(3221225477 - 0x100000000)) == "STATUS_ACCESS_VIOLATION (0xC0000005)"
    assert _native_death(_P(0xC0000374)) == "STATUS_HEAP_CORRUPTION (0xC0000374)"
    # A normal program exit, including a NON-zero one, is a verdict.
    assert _native_death(_P(0)) is None
    assert _native_death(_P(1)) is None
    assert _native_death(_P(3)) is None
    assert _native_death(_P(4)) is None
    # A test without a message is a test that has not been shown to work.
    assert _native_death(_P(None)) is None


# --------------------------------------------------------------------------- #
# The controller's own state machine.
# --------------------------------------------------------------------------- #

def test_controller_selftest_is_green(ctl):
    """The controller's bundled falsifiers must all hold.

    Run as a subprocess so the assertions are the shipped ones, not a private
    copy: a selftest that only passes when invoked in-process is a selftest that
    does not describe the artifact that runs on the schedule.
    """
    proc = subprocess.run([sys.executable, str(CONTROLLER), "selftest"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)
    tail = (proc.stdout or "").strip().splitlines()[-6:]
    assert proc.returncode == 0, "\n".join(tail) + "\n" + (proc.stderr or "")[:800]
    assert "selftest: " in (proc.stdout or ""), tail
    line = [l for l in (proc.stdout or "").splitlines() if "selftest: " in l][-1]
    got, total = line.split("selftest: ")[1].split()[0].split("/")
    assert got == total, "controller selftest reported %s" % line


def test_atomic_write_is_atomic_and_leaves_no_temp_file(ctl, tmp_path):
    destination = tmp_path / "state.json"
    digest = ctl.fsync_atomic_write(str(destination), '{"a": 1}')
    assert json.loads(destination.read_text(encoding="utf-8")) == {"a": 1}
    assert digest == ctl.hashlib.sha256(b'{"a": 1}').hexdigest()
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert leftovers == [], "an atomic write left a temp file behind: %s" % leftovers


def test_saving_state_keeps_the_previous_generation(ctl, tmp_path, monkeypatch):
    """The `.previous.json` sidecar is what makes a hard kill recoverable.

    Without it there is no way to answer "what did the state say before the
    writer died mid-pipeline".
    """
    state = tmp_path / "state.json"
    monkeypatch.setattr(ctl, "STATE_PATH", str(state))
    monkeypatch.setattr(ctl, "STATE_PREV", str(state) + ".previous.json")

    first = ctl.default_state()
    first["current_step"] = "FINAL-05_DETACHED"
    ctl.save_state(first)
    second = ctl.load_state()
    second["current_step"] = "FINAL-08_SOAK"
    ctl.save_state(second)

    prev = json.loads((tmp_path / "state.json.previous.json").read_text(encoding="utf-8"))
    assert prev["current_step"] == "FINAL-05_DETACHED"
    assert ctl.load_state()["current_step"] == "FINAL-08_SOAK"


def test_only_one_controller_can_hold_the_lock(ctl, tmp_path, monkeypatch):
    """A second controller must refuse rather than race the first.

    Two controllers driving the same pipeline is worse than none: both would
    advance the same step and the state file would record whichever wrote last.
    """
    lock = tmp_path / "jcc-continuation.lock.json"
    monkeypatch.setattr(ctl, "CONTROLLER_LOCK", str(lock))

    held = ctl.acquire_controller_lock()
    assert held is not None and held["pid"] == os.getpid()
    assert ctl.acquire_controller_lock() is None, "a second controller took the lock"
    assert lock.is_file()

    # A lock whose owner is dead is reclaimable -- otherwise a crash would wedge
    # the pipeline forever and the watchdog could never restart it.
    lock.write_text(json.dumps({"pid": 0, "expires_at": ctl.iso(
        ctl.now() + ctl.timedelta(seconds=600))}), encoding="utf-8")
    assert ctl.acquire_controller_lock() is not None

    ctl.release_controller_lock()
    assert not lock.exists()


def test_pid_liveness_is_by_handle_not_by_name(ctl):
    """Liveness must not go through Get-Process-equivalent guesswork.

    A process in another session is invisible to a name-based lookup, which
    would read as "dead" and let the watchdog start a second controller next to
    a live one.
    """
    assert ctl.pid_alive(os.getpid()) is True
    assert ctl.pid_alive(0) is False
    assert ctl.pid_alive(None) is False
    assert ctl.pid_alive("not a pid") is False
    # A pid that cannot exist. 4 * 1000_0000 is above the Windows pid ceiling.
    assert ctl.pid_alive(4_000_000_000) is False


def test_a_step_may_not_declare_the_project_complete(ctl):
    """`runtime_state`/`project_run` are DERIVED, never written by a step.

    A step that could declare the project complete is the "instrument reports
    success" failure in its purest form.
    """
    state = ctl.default_state()
    state["runtime_state"] = "COMPLETE"
    state["project_run"] = "PASS"
    state["steps"] = {sid: {"status": "BLOCKED"} for sid in ctl.STEP_IDS}
    ctl.derive(state)
    assert state["runtime_state"] == "BLOCKED", state["runtime_state"]
    assert state["blockers"], "a blocked step did not surface as a blocker"


def test_exit_zero_without_evidence_is_not_progress(ctl, monkeypatch):
    """THE rule the whole pipeline rests on.

    A child that exits 0 and writes nothing must be BLOCKED, and its judge must
    never be consulted -- consulting a judge with no artifact is how a missing
    measurement becomes a pass.
    """
    scratch = REPO / ".workbuddy" / "scratch" / "final" / "infra"
    scratch.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ctl, "STEP_LOG_DIR", str(scratch / "logs"))
    calls = []

    def judge(rec):
        calls.append(rec)
        return True, "should never be reached"

    # The probe lives INSIDE the repository: `run_step` resolves the script
    # relative to the project root, and os.path.relpath cannot cross volumes
    # ("path is on mount 'C:', start on mount 'F:'"). `.workbuddy/` is ignored,
    # so this does not dirty the tree.
    probe = scratch / "exits_zero.py"
    probe.write_text("raise SystemExit(0)\n", encoding="utf-8")
    step = ctl.Step("X-ZERO", "exits 0, writes no artifact",
                    "tools/continuation/../tests/../../.workbuddy/scratch/final/"
                    "infra/exits_zero.py",
                    (), "artifacts/acceptance/__absent__.json", judge, 60)
    record = ctl.run_step(step)
    assert record["status"] == "BLOCKED", record
    assert record["exit_code"] == 0
    assert calls == [], "the judge was consulted without an artifact"


def test_gate_judge_refuses_a_verdict_it_should_not_accept(ctl):
    ok, _ = ctl._gate_judge({"project_complete": True, "blocker": 0,
                             "missing": [], "failed": [], "checks": [{}]})
    assert ok
    for bad in (
        {"project_complete": False, "blocker": 0, "missing": [], "failed": []},
        {"project_complete": True, "blocker": 1, "missing": ["P-06"], "failed": []},
        {"project_complete": True, "blocker": 0, "missing": [], "failed": ["P-01"]},
        {"project_complete": True, "blocker": 0, "missing": [], "failed": [],
         "degraded": ["S-04"]},
    ):
        got, detail = ctl._gate_judge(bad)
        assert not got, "accepted %r (%s)" % (bad, detail)


def test_soak_judge_refuses_a_red_or_unclean_soak(ctl):
    for bad in (
        {"verdict": "FAIL: service died", "soak": {"health_failures": 0}},
        {"verdict": "PASS: ok", "soak": {"health_failures": 2, "pid_changes": 0}},
        {"verdict": "PASS: ok", "soak": {"health_failures": 0, "pid_changes": 1}},
        {"verdict": "", "soak": {}},
    ):
        ok, detail = ctl._soak_judge(bad)
        assert not ok, "accepted %r (%s)" % (bad, detail)


# --------------------------------------------------------------------------- #
# This suite's own step definition: FINAL-01 must not pass by running nothing.
# --------------------------------------------------------------------------- #

RUNNER = REPO / "tools" / "continuation" / "run_infra_suite.py"
INFRA_EVIDENCE = REPO / "artifacts" / "acceptance" / "CONTINUATION_INFRA_TEST.json"


def test_final01_step_declares_an_artifact_and_a_judge(ctl):
    """THE hole this file was created to close.

    ``run_step`` executes ``[venv-python, script] + args``. FINAL-01 used to
    declare ``tools/tests/test_continuation_infra.py`` as its script and no
    result artifact -- and ``python tools/tests/test_continuation_infra.py``
    imports pytest, defines the tests, and exits 0 without running one of them.
    The step passed by doing nothing, and the exit code was the only thing
    consulted, so nothing could contradict it.
    """
    step = ctl.STEP_BY_ID["FINAL-01_CONTROLLER"]
    assert step.result, "FINAL-01 declares no result artifact"
    assert step.judge is not None, "FINAL-01 has no judge: the exit code would be the verdict"
    assert step.script == "tools/continuation/run_infra_suite.py", step.script
    assert (REPO / step.script).is_file(), step.script


def test_the_infra_judge_refuses_the_ways_this_step_could_fake_a_pass(ctl):
    good = {"ok": True, "status": "PASS", "failed": 0, "check_count": 66,
            "passed_count": 66,
            "provenance": {"selection": "full directory (no -k / -m / --deselect; all items ran)"}}
    ok, detail = ctl._infra_judge(good)
    assert ok, detail
    for bad in (
        dict(good, status="FAIL"),
        dict(good, ok=False),
        dict(good, failed=1),
        dict(good, check_count=0, passed_count=0),
        dict(good, passed_count=65),
        dict(good, provenance={"selection": "-k 'not something'"}),
        dict(good, provenance={}),
    ):
        got, detail = ctl._infra_judge(bad)
        assert not got, "accepted %r (%s)" % (bad, detail)


def test_the_infra_runner_selftest_is_green():
    """The runner's reconciliation must be able to fail before it can be trusted."""
    proc = subprocess.run([sys.executable, str(RUNNER), "--selftest"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")
    line = [l for l in (proc.stdout or "").splitlines() if "selftest: " in l]
    assert line, proc.stdout
    got, total = line[-1].split("selftest: ")[1].split()[0].split("/")
    assert got == total, line[-1]


def test_a_narrowed_run_does_not_rewrite_the_suite_verdict():
    """A subset run must not produce a certificate for the whole suite.

    `session.items` is the collection AFTER deselection, so `pytest tools/tests
    -k ...` used to write "65/65 infrastructure test(s) passed", status PASS --
    a green certificate for a suite with one of its tests skipped out of
    existence. Measured, on this tree, from exactly that command. This drives
    the real conftest and asserts the artifact is byte-identical afterwards.
    """
    before = INFRA_EVIDENCE.read_bytes() if INFRA_EVIDENCE.is_file() else None
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tools/tests", "-q", "--no-header",
         "-p", "no:cacheprovider", "-k", "definitely_no_such_test_xyz"],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300,
        env=dict(os.environ, CODEBUDDY_SAFE_DELETE_ENABLED="0"))
    after = INFRA_EVIDENCE.read_bytes() if INFRA_EVIDENCE.is_file() else None
    assert after == before, (
        "a deselected run rewrote the suite evidence; the artifact now claims a "
        "verdict for tests that were not run")
    assert "NOT writing" in (proc.stdout or ""), (
        "the conftest did not say it was refusing to write:\n%s" % (proc.stdout or "")[:500])


# --------------------------------------------------------------------------- #
# Scripts: parse under 5.1, and never do the two things that break this host.
# --------------------------------------------------------------------------- #

NEW_SCRIPTS = [
    "lib/JccWatchdogCommon.ps1",
    "continuation-watchdog.ps1",
    "continuation-watchdog-install.ps1",
    "continuation-watchdog-uninstall.ps1",
    "continuation-watchdog-status.ps1",
    "service-watchdog.ps1",
    "service-watchdog-install.ps1",
    "service-watchdog-uninstall.ps1",
    "service-watchdog-status.ps1",
]


def _code_only(text: str) -> str:
    """Strip PowerShell comments, leaving only code.

    WHY THIS EXISTS. These scripts document the very things they are forbidden
    to do ("`taskkill /IM python.exe` appears nowhere in this file", "`schtasks.exe`
    is on the host's program blacklist"), so a naive ``"taskkill" in text`` search
    fires on the prohibition instead of on the violation. A guard that fails on
    correct code is worse than no guard: it gets deleted. So scan code, not prose.

    Handles ``<# ... #>`` blocks, ``#`` to end of line, single/double quoted
    strings (with the doubled-quote and backtick escapes) and here-strings, so a
    ``#`` inside a literal is not mistaken for a comment.
    """
    out: list = []
    i, n = 0, len(text)
    quote = None
    while i < n:
        ch = text[i]
        if quote is None:
            if text.startswith("<#", i):
                end = text.find("#>", i + 2)
                i = n if end == -1 else end + 2
                continue
            if ch == "@" and i + 1 < n and text[i + 1] in "'\"":
                # Here-string: its body is a literal, even if it contains '#'.
                marker = "\n" + text[i + 1] + "@"
                end = text.find(marker, i + 2)
                i = n if end == -1 else end + len(marker)
                continue
            if ch == "#":
                end = text.find("\n", i)
                i = n if end == -1 else end
                continue
            if ch in "'\"":
                quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "`":
            out.append(text[i:i + 2])
            i += 2
            continue
        if ch == quote:
            if i + 1 < n and text[i + 1] == quote:
                out.append(text[i:i + 2])
                i += 2
                continue
            quote = None
        out.append(ch)
        i += 1
    return "".join(out)


@pytest.fixture(scope="module")
def parse_report():
    """Parse every watchdog script with the SAME parser the product uses.

    Not a lint: `[Parser]::ParseFile` is the check the acceptance gate already
    applies to the seven operator scripts, and Windows PowerShell 5.1 is what
    the scheduled task will invoke. A script that needs 7.x parses fine in the
    editor and fails at 02:00 on a machine nobody is watching.
    """
    targets = [str(SCRIPTS / s) for s in NEW_SCRIPTS]
    missing = [t for t in targets if not Path(t).is_file()]
    assert not missing, "missing scripts: %s" % missing
    quoted = ",".join("'%s'" % t.replace("'", "''") for t in targets)
    command = (
        "$out = @(); foreach ($p in @(%s)) { "
        "$t=$null;$e=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile($p,[ref]$t,[ref]$e) | Out-Null;"
        "$out += [pscustomobject]@{ file=(Split-Path -Leaf $p); errors=@($e).Count; "
        "messages=@($e | ForEach-Object { $_.Message }) } }; "
        "($out | ConvertTo-Json -Depth 5 -Compress)" % quoted
    )
    report, proc = _ps_json(command)
    return report if isinstance(report, list) else [report]


def test_every_watchdog_script_parses_under_windows_powershell_51(parse_report):
    bad = [r for r in parse_report if r["errors"]]
    assert not bad, "scripts that Windows PowerShell 5.1 cannot parse: %s" % json.dumps(
        bad, indent=2)


def test_no_script_shells_out_to_schtasks():
    """`schtasks.exe` is on this host's program blacklist.

    A registration path that shells out to it does not fail loudly -- it fails
    to register, and the failure is only visible the next time the pipeline dies
    and does not come back. Scanned as CODE: the prohibition is itself written
    down in these files, and a guard that fires on the prohibition is a guard
    that gets deleted.
    """
    offenders = []
    for path in sorted(SCRIPTS.rglob("*.ps1")):
        if "schtasks" in _code_only(path.read_text(encoding="utf-8", errors="replace")).lower():
            offenders.append(path.name)
    assert offenders == [], "schtasks is blacklisted on this host: %s" % offenders


def test_no_watchdog_kills_python_by_image_name():
    """`taskkill /IM python.exe` must never appear.

    This host runs unrelated Python. A watchdog that kills by image name reports
    success and takes someone else's process with it.
    """
    offenders = []
    for path in sorted(SCRIPTS.rglob("*.ps1")):
        code = _code_only(path.read_text(encoding="utf-8", errors="replace")).lower()
        if "taskkill" in code or "stop-process" in code and "-name python" in code:
            offenders.append(path.name)
    assert offenders == [], "image-name kills found in: %s" % offenders


def test_detached_start_redirects_streams_to_files():
    """The controller must never inherit a pipe from the watchdog.

    Two failures follow from a pipe: PowerShell's pump waits for it, so the tick
    never returns and the next tick is skipped; and when the tick does end the
    reader goes away and the child dies mid-pipeline. Both were observed.

    The invariant is about the LAUNCH STATEMENT, not about the function: the
    launcher is allowed to pipe `New-Item` into `Out-Null`, and an earlier
    version of this test asserted "no `|` anywhere in the body", which failed on
    that correct line. Broad assertions get relaxed instead of fixed; this one
    names the pipe that actually matters.
    """
    text = (SCRIPTS / "lib" / "JccWatchdogCommon.ps1").read_text(encoding="utf-8")
    body = _code_only(
        text.split("function Start-JccDetachedPython", 1)[1].split("\nfunction ", 1)[0])
    assert "RedirectStandardOutput" in body and "RedirectStandardError" in body, body
    assert "-PassThru" in body, "without -PassThru the caller never learns the pid"
    assert "-WindowStyle Hidden" in body, body
    assert "2>&1" not in body, "merging the child's streams puts them on a pipe again"

    lines = body.splitlines()
    # `-FilePath` matters: the function's own failure message contains the words
    # "Start-Process returned nothing", and a bare substring search counted that
    # string literal as a second launch point.
    starts = [n for n, l in enumerate(lines) if "Start-Process" in l and "-FilePath" in l]
    assert len(starts) == 1, "expected exactly one launch point, found %d" % len(starts)
    # the launch is a backtick-continued statement: take all of it
    statement, k = [lines[starts[0]]], starts[0]
    while statement[-1].rstrip().endswith("`") and k + 1 < len(lines):
        k += 1
        statement.append(lines[k])
    joined = "\n".join(statement)
    assert "|" not in joined, (
        "the launch statement uses a pipeline, which is the exact defect this "
        "function exists to avoid:\n%s" % joined)


def test_the_case_duplicate_proxy_guard_lives_inside_the_launcher():
    """The guard for the environment PS 5.1 cannot bind must sit at the failure.

    MEASURED 2026-09-26. This host's agent shell exports BOTH ``HTTP_PROXY`` and
    ``http_proxy``. Windows treats those as one variable, but PowerShell 5.1's
    ``Start-Process`` rejects the pair:

        Start-Process : An item with the same key has already been added.
        Key in dictionary: 'HTTP_PROXY'  Key being added: 'http_proxy'
        at F:\\jev\\scripts\\lib\\JccWatchdogCommon.ps1:239

    The guard that removes the lower-case twin used to live in
    ``scripts/continuation-watchdog.ps1``, which guards ONE caller -- the
    scheduled task. ``tools/continuation/detached_survival.py`` calls the
    launcher directly, took the unguarded path, and reported "the shipped
    launcher produced no process" for what was really "this shell's environment
    could not be bound". A guard whose placement converts a launch into a false
    reading of the product is worse than the missing guard it replaced.

    WHY AN ASSERTION ABOUT PLACEMENT. No call can observe this property: from a
    clean console the guard is unnecessary, and from a dirty one the observable
    difference is only visible if the guard is absent -- which is the thing under
    test. ``test_the_shipped_launcher_starts_a_python_that_outlives_the_call``
    is the behavioural half; this one names where the guard has to be so that
    half holds for every caller instead of one.
    """
    lib = (SCRIPTS / "lib" / "JccWatchdogCommon.ps1").read_text(encoding="utf-8")
    assert "function Remove-JccDuplicateCaseProxyEnv" in lib, (
        "the guard function is gone; every caller of Start-JccDetachedPython is "
        "back on the environment PS 5.1 cannot bind")
    body = _code_only(
        lib.split("function Start-JccDetachedPython", 1)[1].split("\nfunction ", 1)[0])
    assert "Remove-JccDuplicateCaseProxyEnv" in body, (
        "the guard exists but the launcher never calls it, so the failure at the "
        "Start-Process call is unguarded:\n%s" % body[:600])

    # ...and it must not have grown a second copy back in the watchdog, which is
    # how it came to guard exactly one caller in the first place.
    watchdog = _code_only(
        (SCRIPTS / "continuation-watchdog.ps1").read_text(encoding="utf-8")).lower()
    assert "http_proxy" not in watchdog, (
        "the watchdog carries its own copy of the proxy pair list again; the "
        "failure belongs to Start-Process inside the launcher, so the guard "
        "belongs there too")


def test_the_shipped_launcher_starts_a_python_that_outlives_the_call(tmp_path):
    """The behavioural half of the guard test, through the shipped function.

    What is measured: ``Start-JccDetachedPython`` returns, and the python it
    started keeps writing heartbeats AFTERWARDS. The canary is the clock; the
    launcher's own ``ok``/``pid`` are only checked so a swallowed exception
    cannot be mistaken for a slow start.

    THE CALLER'S OWN STREAMS GO TO FILES, AND THAT IS NOT A DETAIL. The first
    version of this test captured the PowerShell process with a pipe and read
    "the launcher returned after 7.32 s" against a 6 s canary -- i.e. it measured
    the pipe, not the launcher. A pipe's write end is inheritable, so the
    grandchild python holds it open and the reader blocks until the grandchild
    exits, which turns every detached launch into a synchronous one. That is the
    same trap the product records for the watchdog's own stdout, and it is why
    this test uses the product's measured-correct helper rather than a fresh
    capture: ``_run_to_files`` puts both streams on file handles that Python
    creates non-inheritable. A test of detachment that captures on a pipe is a
    test that always passes.

    Ambitious about the environment on purpose: this process inherits whatever
    the caller's environment block holds. When the caller is this host's agent
    shell, that block carries both proxy spellings and this test is exactly the
    measurement that fails when the guard is not inside the launcher. Run from a
    clean console it is weaker -- that is recorded here rather than papered over,
    and it is why the placement assertion above exists alongside it.
    """
    surv = _load(REPO / "tools" / "continuation" / "detached_survival.py",
                 "_jcc_detached_survival")
    canary = tmp_path / "canary.py"
    canary.write_text(
        "import io, sys, time\n"
        "with io.open(sys.argv[1], 'a', encoding='utf-8') as fh:\n"
        "    for _ in range(6):\n"
        "        fh.write('%.3f\\n' % time.time())\n"
        "        fh.flush()\n"
        "        time.sleep(1)\n",
        encoding="utf-8")
    beat = tmp_path / "beats.txt"
    out_log, err_log = tmp_path / "child.out.txt", tmp_path / "child.err.txt"
    ps_out, ps_err = tmp_path / "caller.out.txt", tmp_path / "caller.err.txt"
    lib = SCRIPTS / "lib" / "JccWatchdogCommon.ps1"
    script = tmp_path / "arm.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        ". '%s'\n"
        "$r = Start-JccDetachedPython -PythonExe '%s' `\n"
        "    -Arguments @('%s', '%s') `\n"
        "    -OutLog '%s' -ErrLog '%s' -WorkingDirectory '%s'\n"
        "Write-Output ('LAUNCH ok=' + $r.ok + ' pid=' + $r.pid)\n"
        % (lib, REPO / ".venv" / "Scripts" / "python.exe", canary, beat,
           out_log, err_log, REPO),
        encoding="ascii", newline="\r\n")

    rc, took = surv._run_to_files(
        [str(PS51), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script)], str(ps_out), str(ps_err), timeout=120)
    returned = time.time()
    text = (ps_out.read_text(encoding="utf-8", errors="replace")
            + ps_err.read_text(encoding="utf-8", errors="replace")).strip()
    assert "LAUNCH ok=True" in text, (
        "the shipped launcher did not return a process (rc=%s, %.2fs):\n%s\n"
        "If the message names a duplicate environment key, the guard is not "
        "doing its job; if it names anything else, that is the finding."
        % (rc, took, text[:800]))
    assert took < 4.0, (
        "the launcher call took %.2fs against a 6s canary, so the caller is "
        "waiting for the child -- the detachment this function exists to provide "
        "is not there" % took)
    assert out_log.is_file() and err_log.is_file(), (
        "the child's streams did not go to files, so a caller holding a pipe "
        "would be coupled to the child's lifetime")

    time.sleep(6)
    beats = []
    if beat.is_file():
        beats = [float(x) for x in beat.read_text().split() if x]
    assert beats, (
        "the launcher returned ok=True and the python it started never wrote a "
        "heartbeat: ok=True is not the measurement, survival is")
    alive_after_return = max(beats) - returned
    assert alive_after_return >= 2.0, (
        "the canary lived %.2fs past the launcher's return, out of ~6s of work: "
        "that is a child tied to the launching command, which is the defect "
        "Start-JccDetachedPython exists to prevent" % alive_after_return)


# --------------------------------------------------------------------------- #
# The watchdogs' decision rules, driven against a scratch root.
# --------------------------------------------------------------------------- #

def _scratch_root(tmp_path: Path, *, state=None, lock=None, maintenance=None,
                  release=None, ownership=None) -> Path:
    root = tmp_path / "root"
    (root / ".workbuddy" / "locks").mkdir(parents=True, exist_ok=True)
    (root / ".workbuddy" / "scheduled" / "logs").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "jcc").mkdir(parents=True, exist_ok=True)
    if state is not None:
        (root / ".workbuddy" / "jcc_execution_state.json").write_text(
            json.dumps(state), encoding="utf-8")
    if lock is not None:
        (root / ".workbuddy" / "locks" / "jcc-continuation.lock.json").write_text(
            json.dumps(lock), encoding="utf-8")
    if maintenance is not None:
        (root / ".workbuddy" / "maintenance.json").write_text(
            json.dumps(maintenance), encoding="utf-8")
    if release is not None:
        (root / ".workbuddy" / "release_state.json").write_text(
            json.dumps(release), encoding="utf-8")
    if ownership is not None:
        (root / "runtime" / "jcc" / "jcc.ownership.json").write_text(
            json.dumps(ownership), encoding="utf-8")
    return root


def _ticks(root: Path, which: str) -> list:
    """Read a watchdog's audit log, strictly.

    Strictly, and not with ``utf-8-sig``: every record is one line of JSON read
    back by ordinary tooling, so a BOM on the first line means the log is not
    readable by any standard reader. Measured: Windows PowerShell 5.1's
    ``Add-Content -Encoding UTF8`` put one there and ``json.loads`` raised on
    line 1. A tolerant reader here would have papered over exactly the defect
    this suite is supposed to catch, so the assertion is explicit instead.
    """
    path = root / ".workbuddy" / "scheduled" / "logs" / (
        "continuation-watchdog.jsonl" if which == "continuation"
        else "service-watchdog.jsonl")
    if not path.is_file():
        return []
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), (
        "the %s audit log starts with a UTF-8 BOM, so its first line is not "
        "JSON for any standard reader" % which)
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]


def test_continuation_watchdog_is_idle_once_the_release_is_complete(tmp_path):
    """A finished release is never resurrected.

    A watchdog that restarts a completed pipeline is worse than no watchdog: it
    re-runs the release on every tick, forever.
    """
    root = _scratch_root(tmp_path, state={"runtime_state": "COMPLETE",
                                          "project_run": "PASS"})
    proc = _ps(["-File", str(SCRIPTS / "continuation-watchdog.ps1"),
                "-Root", str(root), "-Force"])
    assert proc.returncode == 0, proc.stderr
    actions = [t["action"] for t in _ticks(root, "continuation")]
    assert actions == ["NOOP"], actions
    assert not (root / ".workbuddy" / "locks" / "jcc-continuation.lock.json").exists(), \
        "the watchdog started a controller for a completed release"


def test_continuation_watchdog_is_idle_when_a_live_controller_holds_the_lock(tmp_path):
    """The common case: something is already running.

    Driven with OUR OWN pid as the lock holder, so the liveness rule is
    exercised against a process that is provably alive without starting
    anything.
    """
    from datetime import datetime, timezone, timedelta
    fresh = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    root = _scratch_root(tmp_path, state={"runtime_state": "RUNNING",
                                          "project_run": "UNKNOWN"},
                         lock={"pid": os.getpid(), "expires_at": fresh})
    proc = _ps(["-File", str(SCRIPTS / "continuation-watchdog.ps1"),
                "-Root", str(root), "-Force"])
    assert proc.returncode == 0, proc.stderr
    actions = [t["action"] for t in _ticks(root, "continuation")]
    assert actions == ["NOOP"], actions
    assert "-Force ignored for a live controller" in _ticks(root, "continuation")[0]["detail"]


def test_continuation_watchdog_replaces_a_stale_lock(tmp_path):
    """A dead controller must be replaced, and the intent recorded.

    The scratch root has no controller script, so the replacement cannot
    actually launch -- which is exactly what makes this safe to assert: the
    observable is that the watchdog DECIDED to replace, not that it started
    something.
    """
    from datetime import datetime, timezone, timedelta
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    root = _scratch_root(tmp_path, state={"runtime_state": "RUNNING",
                                          "project_run": "UNKNOWN"},
                         lock={"pid": 0, "expires_at": stale})
    proc = _ps(["-File", str(SCRIPTS / "continuation-watchdog.ps1"),
                "-Root", str(root)])
    actions = [t["action"] for t in _ticks(root, "continuation")]
    assert "STALE" in actions, actions
    assert "ERROR" in actions, actions
    assert proc.returncode == 1, proc.stdout
    assert "controller script missing" in _ticks(root, "continuation")[-1]["detail"]


def _free_port() -> int:
    """A port nothing is listening on, reserved then released by this process."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_the_operator_hold_stops_the_watchdog_starting_a_controller(tmp_path):
    """The only lever that can silence the watchdog without lying about the release.

    The alternative -- writing runtime_state=COMPLETE + project_run=PASS -- stops
    the watchdog by making a FALSE STATEMENT about the release, which is the move
    this project refuses everywhere else. So the hold is a separate, expiring,
    observable flag. Measured here against a scratch root: with the hold in force
    the controller is NOT started, and with the same state and no hold it IS.
    """
    from datetime import datetime, timezone, timedelta
    until = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    root = _scratch_root(tmp_path / "held", state={"runtime_state": "RUNNING",
                                                   "project_run": "UNKNOWN"})
    (root / ".workbuddy" / "operator_hold.json").write_text(
        json.dumps({"active": True, "reason": "authoring FINAL scripts",
                    "expires_at": until}), encoding="utf-8")
    proc = _ps(["-File", str(SCRIPTS / "continuation-watchdog.ps1"), "-Root", str(root)])
    assert proc.returncode == 0, proc.stderr
    ticks = _ticks(root, "continuation")
    assert [t["action"] for t in ticks] == ["NOOP"], ticks
    assert "operator hold active" in ticks[0]["detail"], ticks[0]
    assert "authoring FINAL scripts" in ticks[0]["detail"], ticks[0]

    # An EXPIRED hold is not honoured: a hold that outlives its operator would
    # freeze the platform silently and forever, and a frozen watchdog looks
    # exactly like an idle one.
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    root2 = _scratch_root(tmp_path / "expired", state={"runtime_state": "RUNNING",
                                                        "project_run": "UNKNOWN"})
    (root2 / ".workbuddy" / "operator_hold.json").write_text(
        json.dumps({"active": True, "reason": "forgotten", "expires_at": expired}),
        encoding="utf-8")
    proc2 = _ps(["-File", str(SCRIPTS / "continuation-watchdog.ps1"), "-Root", str(root2)])
    actions2 = [t["action"] for t in _ticks(root2, "continuation")]
    assert "NOTE" in actions2, actions2
    assert "STALE" in actions2 or "ERROR" in actions2, (
        "an expired hold must let the watchdog resume: %s" % actions2)

    # An UNREADABLE hold is not honoured either -- same asymmetry, stated in the
    # tick log rather than silently.
    root3 = _scratch_root(tmp_path / "corrupt", state={"runtime_state": "RUNNING",
                                                       "project_run": "UNKNOWN"})
    (root3 / ".workbuddy" / "operator_hold.json").write_text(
        json.dumps({"active": True, "reason": "no expiry at all"}), encoding="utf-8")
    _ps(["-File", str(SCRIPTS / "continuation-watchdog.ps1"), "-Root", str(root3)])
    actions3 = [t["action"] for t in _ticks(root3, "continuation")]
    assert "NOTE" in actions3, actions3
    assert "NOT in force" in _ticks(root3, "continuation")[0]["detail"], \
        _ticks(root3, "continuation")[0]


def test_the_operator_hold_tool_refuses_a_hold_that_cannot_expire(tmp_path):
    """`operator_hold.py` must not be usable as a permanent silencing switch."""
    hold = _load(REPO / "tools" / "continuation" / "operator_hold.py", "_jcc_hold")
    real = hold.HOLD
    try:
        hold.HOLD = str(tmp_path / "operator_hold.json")
        assert hold.main(["--set", "--minutes", "20"]) == 2, "a hold with no reason"
        assert hold.main(["--set", "--reason", "x"]) == 2, "a hold with no duration"
        assert hold.main(["--set", "--minutes", "600", "--reason", "x"]) == 2, "over the cap"
        assert hold.main(["--set", "--minutes", "20", "--reason", "editing"]) == 0
        assert hold.in_force(hold.read()), "a fresh hold is not in force"
        assert hold.main(["--clear"]) == 0
        assert not hold.in_force(hold.read()), "a cleared hold is still in force"
        payload = json.loads((tmp_path / "operator_hold.json").read_text(encoding="utf-8"))
        assert payload["active"] is False
        assert payload["reason"] == "editing", "clearing erased who stopped the platform"
    finally:
        hold.HOLD = real


def test_continuation_watchdog_survives_a_state_file_of_the_wrong_shape(tmp_path):
    """A watchdog that dies on a malformed state file cannot report the incident.

    `Set-StrictMode -Version 2.0` makes a missing property a terminating error,
    so every field the watchdog reads has to be read defensively. This drives a
    state file that lacks the fields AND a lock that lacks a pid.
    """
    for index, state in enumerate(({"something_else": 1}, {})):
        root = _scratch_root(tmp_path / ("shape%d" % index),
                             state=state, lock={"no_pid_here": True})
        proc = _ps(["-File", str(SCRIPTS / "continuation-watchdog.ps1"),
                    "-Root", str(root)])
        assert proc.returncode in (0, 1), (proc.returncode, proc.stderr)
        assert "cannot be found" not in (proc.stderr or ""), proc.stderr
        assert "PropertyNotFoundStrict" not in (proc.stderr or ""), proc.stderr


def test_service_watchdog_stays_away_during_a_maintenance_window(tmp_path):
    """The soak must not have its service restarted underneath it.

    A watchdog that recovers the service during the 40-minute soak destroys the
    measurement it is supposed to protect.
    """
    from datetime import datetime, timezone, timedelta
    until = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    root = _scratch_root(tmp_path,
                         maintenance={"active": True, "expires_at": until,
                                      "reason": "FINAL-08 soak"},
                         release={"state": "READY"})
    proc = _ps(["-File", str(SCRIPTS / "service-watchdog.ps1"),
                "-Root", str(root), "-Port", str(_free_port())])
    assert proc.returncode == 0, proc.stderr
    ticks = _ticks(root, "service")
    assert [t["action"] for t in ticks][:1] == ["NOOP"], ticks
    assert "maintenance window active" in ticks[0]["detail"]


def test_service_watchdog_monitors_nothing_until_the_release_is_ready(tmp_path):
    """Monitoring is reserved for a READY release."""
    for release in ({"state": "STAGED"}, {"state": "FROZEN"}, {}):
        root = _scratch_root(tmp_path / ("rel-" + (release.get("state") or "unset")),
                             release=release)
        proc = _ps(["-File", str(SCRIPTS / "service-watchdog.ps1"),
                    "-Root", str(root), "-Port", str(_free_port())])
        assert proc.returncode == 0, proc.stderr
        ticks = _ticks(root, "service")
        assert [t["action"] for t in ticks] == ["NOOP"], ticks
        assert "reserved for READY" in ticks[0]["detail"]


def test_service_watchdog_refuses_foreign_occupancy(tmp_path):
    """THE rule that stops a watchdog becoming an outage.

    A listener this project cannot prove it owns must produce FAIL-CLOSED and a
    refusal to touch it -- never a restart. The listener here is bound by the
    test itself on an ephemeral port, so the assertion is about the rule and not
    about whatever happens to be on 4778.

    Three distinct states, and they are NOT the same state:
      * a marker naming a DIFFERENT process -> foreign occupancy;
      * no marker at all                    -> unknown owner;
      * a marker whose pid is unusable       -> unknown owner, but the record
        must not claim the marker is absent.
    An earlier version of this test used pid 4_000_000_000 for the first case,
    which does not fit a 32-bit int: `Get-JccPropInt` folded it to 0 and the run
    silently exercised the "no marker" branch while asserting the "foreign
    occupancy" text. The impossible pid has to be one the field can hold.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    port = listener.getsockname()[1]
    # Far above any pid this host can allocate, and still a valid 32-bit int.
    impossible_pid = 2_000_000_000
    try:
        root = _scratch_root(tmp_path, release={"state": "READY"},
                             ownership={"service_pid": impossible_pid})
        proc = _ps(["-File", str(SCRIPTS / "service-watchdog.ps1"),
                    "-Root", str(root), "-Port", str(port)])
        assert proc.returncode == 3, (proc.returncode, proc.stdout, proc.stderr)
        ticks = _ticks(root, "service")
        assert [t["action"] for t in ticks] == ["FAIL_CLOSED"], ticks
        assert "FOREIGN OCCUPANCY" in ticks[0]["detail"]
        assert "Escalate to a human" in ticks[0]["detail"]

        # Same rule, no marker at all: an unknown listener is still not ours.
        root2 = _scratch_root(tmp_path / "no-marker", release={"state": "READY"})
        proc2 = _ps(["-File", str(SCRIPTS / "service-watchdog.ps1"),
                     "-Root", str(root2), "-Port", str(port)])
        assert proc2.returncode == 3, (proc2.returncode, proc2.stdout)
        ticks2 = _ticks(root2, "service")
        assert [t["action"] for t in ticks2] == ["FAIL_CLOSED"], ticks2
        assert "no ownership marker exists" in ticks2[0]["detail"]

        # A marker that exists but cannot be used: still FAIL-CLOSED, but the
        # recorded reason must describe what is actually on disk.
        root3 = _scratch_root(tmp_path / "bad-pid", release={"state": "READY"},
                              ownership={"service_pid": 4_000_000_000})
        proc3 = _ps(["-File", str(SCRIPTS / "service-watchdog.ps1"),
                     "-Root", str(root3), "-Port", str(port)])
        assert proc3.returncode == 3, (proc3.returncode, proc3.stdout)
        ticks3 = _ticks(root3, "service")
        assert [t["action"] for t in ticks3] == ["FAIL_CLOSED"], ticks3
        assert "no ownership marker exists" not in ticks3[0]["detail"], (
            "the marker is on disk; the FAIL-CLOSED record must not say it is "
            "absent: %s" % ticks3[0]["detail"])
        assert "names no usable pid" in ticks3[0]["detail"], ticks3[0]["detail"]
    finally:
        listener.close()


def test_service_watchdog_budget_is_bounded(tmp_path):
    """A watchdog that restarts forever turns a crash loop into a denial of service.

    Driven with an exhausted budget and a port that answers nothing: without the
    budget rule this tick would have run stop.ps1 + start.ps1 (it is not a dry
    run), so exit 4 also proves nothing was touched.
    """
    from datetime import datetime, timezone
    root = _scratch_root(tmp_path, release={"state": "READY"},
                         ownership={"service_pid": 4_000_000_000})
    state_dir = root / ".workbuddy" / "scheduled"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "service-watchdog-state.json").write_text(json.dumps({
        "window_start": datetime.now(timezone.utc).isoformat(),
        "restart_count": 6,
    }), encoding="utf-8")
    proc = _ps(["-File", str(SCRIPTS / "service-watchdog.ps1"),
                "-Root", str(root), "-Port", str(_free_port()),
                "-MaxRestartsPerHour", "6"])
    assert proc.returncode == 4, (proc.returncode, proc.stdout, proc.stderr)
    ticks = _ticks(root, "service")
    assert any(t["action"] == "BUDGET_EXCEEDED" for t in ticks), ticks
    assert not (root / "scripts").exists(), "the budget rule must stop before recovery"


# --------------------------------------------------------------------------- #
# The registrations themselves. A script that exists proves nothing.
# --------------------------------------------------------------------------- #

def test_new_jcc_scheduled_task_accepts_an_empty_argument_list():
    """The registration path must actually bind, construct and negotiate.

    MEASURED DEFECT. ``-ScriptArguments @()`` was rejected outright: a Mandatory
    ``[string[]]`` refuses an empty array AT BINDING TIME, so both install
    scripts threw ``ParameterArgumentValidationErrorEmptyArrayNotAllowed`` before
    the function body ran. The watchdog takes no arguments, so the task could
    never have been registered -- and all nine parse checks, and everything else
    in this file, passed anyway. A static check cannot see this; only a call can.

    AND A MEASURED TESTING TRAP. The first version of this test tried to shadow
    ``Register-ScheduledTask`` with a global function. That does NOT work -- the
    ScheduledTasks module's own export won the lookup, the real cmdlet ran, and a
    task named ``JEV-JCC-Unit-Test`` appeared in this host's scheduler and had to
    be unregistered by hand. So the call goes through the library's explicit
    ``-DryRun`` seam, which builds the action, triggers, settings and principal
    ladder for real and stops before registration.
    """
    command = (
        ". '%s'; "
        "$res = New-JccScheduledTask -TaskName 'JEV-JCC-Unit-Test' -ScriptPath '%s' "
        "-ScriptArguments @() -TriggerRepetition @('AtLogOn','AtStartup','Every') "
        "-RepetitionMinutes 2 -PreferSystem -DryRun; "
        "($res | ConvertTo-Json -Depth 6 -Compress)"
        % (str(SCRIPTS / "lib" / "JccWatchdogCommon.ps1").replace("'", "''"),
           str(SCRIPTS / "continuation-watchdog.ps1").replace("'", "''"))
    )
    report, proc = _ps_json(command)
    assert report["ok"] is True, (proc.stdout or "") + (proc.stderr or "")
    definition = report["definition"]
    assert definition["registers"] is False, "the dry run registered something"
    assert definition["task_name"] == "JEV-JCC-Unit-Test"
    assert definition["script_arguments"] == [], definition["script_arguments"]
    assert definition["arguments"].endswith(
        '-File "%s"' % (SCRIPTS / "continuation-watchdog.ps1")), definition["arguments"]
    assert "-NonInteractive" in definition["arguments"], definition["arguments"]
    assert "-NoProfile" in definition["arguments"], definition["arguments"]
    assert definition["working_directory"] == str(REPO), definition["working_directory"]
    assert definition["trigger_count"] == 3, definition
    assert any("PT2M" in r for r in (definition["repetition"] or [])), (
        "no repetition interval was built, so the task could only ever run at "
        "logon/startup and could not revive a controller that died mid-session: %s"
        % definition)
    assert definition["multiple_instances"] == "IgnoreNew", (
        "concurrent ticks must be ignored, not queued: %s" % definition)
    assert definition["principals"][0].startswith("SYSTEM/ServiceAccount/"), (
        "-PreferSystem must try SYSTEM FIRST, and record which identity was "
        "actually used: %s" % definition["principals"])
    assert len(definition["principals"]) >= 2, definition["principals"]


def test_the_installers_never_ask_for_a_dry_run():
    """`-DryRun` must not be how the tasks get "registered".

    The seam above exists so a test can exercise the registration path without
    touching the scheduler. If an installer passed it, the install would report
    success and register nothing -- the same shape as every other defect in this
    file.
    """
    for name in ("continuation-watchdog-install.ps1", "service-watchdog-install.ps1"):
        code = _code_only((SCRIPTS / name).read_text(encoding="utf-8"))
        assert "DryRun" not in code, (
            "%s passes -DryRun, so it would report a registration it never made" % name)
        assert "New-JccScheduledTask" in code, name


def test_both_watchdogs_are_registered_as_scheduled_tasks():
    """FINAL-2 / FINAL-4: registered, enabled, and pointed at the right script.

    Not "the install script exists". The deliverable is a platform that keeps
    running after this conversation ends, and that depends on a task existing in
    the scheduler with a repetition interval -- AtLogOn alone leaves a dead
    controller dead for the rest of the session.
    """
    command = (
        "$out = @(); foreach ($n in @('%s','%s')) { "
        "$t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue; "
        "if (-not $t) { $out += [pscustomobject]@{ name=$n; exists=$false } } else { "
        "$i = Get-ScheduledTaskInfo -TaskName $n -ErrorAction SilentlyContinue; "
        "$rep = @($t.Triggers | Where-Object { $_.Repetition.Interval } | "
        "ForEach-Object { $_.Repetition.Interval }); "
        "$out += [pscustomobject]@{ name=$n; exists=$true; state=[string]$t.State; "
        "enabled=($t.State -ne 'Disabled'); principal=[string]$t.Principal.UserId; "
        "run_level=[string]$t.Principal.RunLevel; "
        "exec=([string]$t.Actions[0].Execute); args=([string]$t.Actions[0].Arguments); "
        "repetition=$rep; last_result=$(if ($i) { $i.LastTaskResult } else { $null }); "
        "next_run=$(if ($i) { [string]$i.NextRunTime } else { $null }) } } }; "
        "($out | ConvertTo-Json -Depth 6 -Compress)" % (CONTINUATION_TASK, SERVICE_TASK)
    )
    report, _ = _ps_json(command)
    rows = report if isinstance(report, list) else [report]
    by_name = {r["name"]: r for r in rows}

    for name in (CONTINUATION_TASK, SERVICE_TASK):
        row = by_name.get(name, {})
        assert row.get("exists") is True, (
            "scheduled task %s is not registered -- the pipeline stops for good "
            "the first time this session ends: %s" % (name, rows))
        assert row.get("enabled") is True, row
        assert row.get("repetition"), (
            "%s has no repetition interval, so it only runs at logon/startup and "
            "cannot revive a controller that died mid-session: %s" % (name, row))
        assert "continuation-watchdog.ps1" in row["args"] or \
            "service-watchdog.ps1" in row["args"], row
        assert "-NonInteractive" in row["args"], row


# --------------------------------------------------------------------------- #
# FINAL-12 / FINAL-13 -- the two aggregators, and regression locks for the two
# defects that were found in the soak's T1 producer and its instrument.
# --------------------------------------------------------------------------- #

PROJECT_RUN_GATE = REPO / "artifacts" / "acceptance" / "project_run_gate.py"
PUBLISHER = REPO / "tools" / "continuation" / "publish_release_evidence.py"
SOAK = REPO / "tools" / "continuation" / "soak_driver.py"
WD_PROOF = REPO / "tools" / "continuation" / "watchdog_proof.py"


def _selftest_tally(path: Path) -> str:
    """Run a script's ``--selftest`` and return the ``x/y`` tally IT printed.

    The tally is read out of the script rather than inferred from the exit code.
    This project has seen four instruments whose return value disagreed with what
    they actually did, so a rule is reconciled against the number the instrument
    reported instead of being trusted on its word.
    """
    proc = subprocess.run([sys.executable, str(path), "--selftest"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180)
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")
    lines = [l for l in (proc.stdout or "").splitlines() if "selftest: " in l]
    assert lines, proc.stdout
    return lines[-1].split("selftest: ")[1].split()[0]


def test_the_project_run_gate_selftest_is_green():
    """FINAL-12's judge must be able to refuse before its PASS can mean anything."""
    got, total = _selftest_tally(PROJECT_RUN_GATE).split("/")
    assert got == total, "project_run_gate selftest %s/%s" % (got, total)
    assert int(total) >= 6, (
        "a verdict rule with fewer than six falsifiers has not been shown to be "
        "refusable in both directions: %s" % total)


def test_the_release_publisher_selftest_is_green():
    """FINAL-13 owns the regeneration check the gate explicitly defers, so its
    rule -- not the gate's -- is what has to be able to fail."""
    got, total = _selftest_tally(PUBLISHER).split("/")
    assert got == total, "publisher selftest %s/%s" % (got, total)
    assert int(total) >= 8, total


def test_the_soak_runs_the_tracked_instrument_and_keeps_its_own_artifact():
    """Regression lock for two defects in the soak's T1, both of the same family.

    (a) It ran ``.workbuddy/scratch/continuation/watchdog_proof.py`` -- a COPY. A
        verdict produced by an untracked instrument verifies exactly once, on the
        machine that produced it.
    (b) It wrote ``WATCHDOG_RECOVERY_PROOF.json``, the same path FINAL-09 writes.
        FINAL-09 therefore overwrote the exact bytes the soak's record pointed at:
        the soak claimed a verdict about a file that no longer contained it.
    """
    with io.open(SOAK, "r", encoding="utf-8") as fh:
        code = _code_only(fh.read())
    t1 = code.split("PHASE T1", 1)[1][:2000]
    assert ".workbuddy" not in t1, (
        "the soak's T1 must run the tracked instrument, not a scratch copy:\n%s" % t1[:400])
    assert '"tools", "continuation", "watchdog_proof.py"' in t1, t1[:400]
    assert "SOAK_WATCHDOG_PROOF.json" in t1, t1[:400]
    assert "WATCHDOG_RECOVERY_PROOF.json" not in t1, (
        "the soak and FINAL-09 must not share an artifact path:\n%s" % t1[:400])


def test_the_watchdog_proof_drives_the_shipped_script_in_place():
    """Regression lock: the instrument must not patch, or copy, the shipped tick.

    It used to open ``tools/continuation-watchdog.ps1`` -- a path that no longer
    exists -- and to rewrite ``$ProjectRoot`` inside a private copy, asserting the
    edit touched exactly one line. Both facts expired when the watchdog moved to
    ``scripts/`` and learned ``-Root``, and the assert would have thrown during a
    forty-minute soak instead of before it.
    """
    with io.open(WD_PROOF, "r", encoding="utf-8") as fh:
        src = fh.read()
    assert 'os.path.join(ROOT, "tools", "continuation-watchdog.ps1")' not in src, (
        "watchdog_proof.py still reads the pre-move path")
    assert 'os.path.join(REP, "scripts", "continuation-watchdog.ps1")' in src
    assert "replica.build_replica" in src, (
        "the replica must have one builder; a second copy is how this went stale")
    assert '"-Root", REP' in src, "the tick must be told its root, not patched"


def test_the_controller_reconciles_recorded_evidence_before_selecting_work(
        tmp_path, monkeypatch):
    """Regression lock for a measured fail-open in step selection.

    `verify_recorded_evidence` refuses a PASS whose artifact is gone or has
    changed, but it used to run only AFTER a step executed -- so the first pass
    through the selection loop trusted the raw statuses. Measured: `status` called
    FINAL-01_CONTROLLER BLOCKED while `run --only FINAL-01_CONTROLLER` answered
    "already PASS; nothing to do".

    The assertion is on WHICH STEP WAS SELECTED, because that is the only
    observable that separates the two orderings. The lock and state paths are
    redirected into a scratch directory, so this never touches the real pipeline.
    """
    ctl = _load(CONTROLLER, "_jcc_controller_reconcile")
    monkeypatch.setattr(ctl, "CONTROLLER_LOCK", str(tmp_path / "lock.json"))
    monkeypatch.setattr(ctl, "STEP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(ctl, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(ctl, "STATE_PREV", str(tmp_path / "state.json.previous.json"))

    stale = ctl.STEPS[0]
    state = ctl.default_state()
    state["steps"] = {
        stale.id: {"status": "PASS",
                   "detail": "judged in an earlier run",
                   "evidence": {"path": "artifacts/acceptance/__absent_forever__.json",
                                "sha256": "0" * 64}},
    }
    ctl.save_state(state)

    seen: list = []

    def fake_run_step(step):
        seen.append(step.id)
        return {"id": step.id, "status": "BLOCKED", "detail": "test double"}

    monkeypatch.setattr(ctl, "run_step", fake_run_step)
    ctl.run_pipeline(ctl.load_state(), once=True)

    assert seen, "the controller selected no work at all"
    assert seen[0] == stale.id, (
        "the controller selected %s first, so it decided what was pending BEFORE "
        "reconciling the recorded evidence; the stale PASS on %s must be the first "
        "thing it re-runs" % (seen[0], stale.id))
