#!/usr/bin/env python3
"""FINAL-10: after a host restart, the pipeline restarts itself from disk.

THE CLAIM, AND WHY IT IS THE ONE THAT MATTERS. This platform's whole reason to
exist is that nobody has to sit in front of it. The failure it is built against
is not "a step fails" -- steps are allowed to fail and be retried -- it is "the
machine came back and the pipeline did not". So what is simulated here is exactly
that: a reboot in the middle of the run, leaving

  * a state file that says RUNNING at a step, with a pid that died with the host;
  * a controller lock whose pid is dead but whose expiry is STILL IN THE FUTURE,
    because the host went down 900 s before the TTL was up (the hard case -- a
    naive rule that only checks the expiry sees a "fresh" lock and does nothing);
  * crash debris: a truncated controller log line, a leftover ``runtime/jcc/jcc.pid``
    naming a dead process, and an ownership marker naming a dead pid.

Then ONE tick is run the way the scheduled task runs it, and the tick has to
recover the pipeline with no human in the loop.

WHAT IS ASSERTED, ALL OF IT MEASURED
  * the tick EXITS, and it exits while the controller it started is still alive
    (a tick that waits for its child is a tick that never happens again);
  * the audit log records the recovery, not a no-op;
  * the lock afterwards names the NEW live pid, not the dead one;
  * the controller resumed at the step the state file named, so the run did not
    silently start over;
  * stderr is clean -- the forged debris must not produce an unhandled error;
  * and the negative: the SAME debris with a state that says COMPLETE +
    project_run=PASS starts nothing, because a watchdog that resurrects a finished
    release is worse than no watchdog.

The controller is a declared stub (see ``replica.py``): it implements only the
lock handshake and records the step it resumed from. Its behaviour as a pipeline
is proved by the gates, not here.

WHAT THIS DOES NOT PROVE. That the SERVICE is restarted after a reboot. Nothing
in this repository restarts ``jcc_core`` if it dies -- the service watchdog
monitors the release state and the port, and it is the operator who starts the
service. That gap is reported in ``artifacts/release/PROJECT_RUN_READINESS.md``
and repeated in this artifact rather than papered over.

Usage
-----
    python tools/continuation/reboot_recovery_sim.py
    python tools/continuation/reboot_recovery_sim.py --selftest
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replica as R  # noqa: E402

ROOT = R.ROOT
OUT = os.path.join(ROOT, "artifacts", "acceptance", "REBOOT_RECOVERY.json")
WORK = os.path.join(ROOT, ".workbuddy", "scratch", "reboot")
STUB_SLEEP_S = 14
TZ = datetime.timezone(datetime.timedelta(hours=8))


def log(msg: str) -> None:
    line = "[reboot] %s" % msg
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _write_json(path, payload):
    _write(path, json.dumps(payload, indent=2) + "\n")


def _head_sha() -> str:
    import subprocess
    p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return (p.stdout or "").strip() or "<unknown>"


def _forge_a_rebooted_host(rep, dead, *, finished: bool, head: str) -> dict:
    """The disk as it would look the moment the host came back."""
    now = datetime.datetime.now(TZ)
    state = {
        "schema_version": 2,
        "project_root": rep,
        "phase": "FINAL",
        "current_step": "FINAL-13_PUBLISH" if finished else "FINAL-08_SOAK",
        "last_completed_step": "FINAL-07_SURFACES",
        "pid": dead,                       # the process that died with the host
        "started_at": (now - datetime.timedelta(minutes=40)).isoformat(),
        "updated_at": (now - datetime.timedelta(minutes=3)).isoformat(),
        "attempt": 7,
        "attempts": {"FINAL-01_CONTROLLER": 1, "FINAL-05_DETACHED": 1},
        "last_error": None,
        "release_sha": head,
        "runtime_state": "COMPLETE" if finished else "RUNNING",
        "project_run": "PASS" if finished else "UNKNOWN",
        "steps_completed": ["FINAL-01_CONTROLLER", "FINAL-05_DETACHED",
                            "FINAL-07_SURFACES"],
        "steps": {sid: {"status": "PASS"} for sid in
                  ("FINAL-01_CONTROLLER", "FINAL-05_DETACHED", "FINAL-07_SURFACES")},
        "blockers": [],
        "next_action": None,
    }
    _write_json(os.path.join(rep, ".workbuddy", "jcc_execution_state.json"), state)

    # The lock the crash left behind: dead pid, expiry still in the FUTURE.
    lock = {
        "owner": "tools/jcc_continuation.py",
        "pid": dead,
        "acquired_at": (now - datetime.timedelta(minutes=3)).isoformat(),
        "expires_at": (now + datetime.timedelta(minutes=11)).isoformat(),
        "controller_sha256": "0" * 64,
    }
    _write_json(os.path.join(rep, ".workbuddy", "locks",
                             "jcc-continuation.lock.json"), lock)

    # Crash debris.
    _write(os.path.join(rep, ".workbuddy", "scheduled", "logs",
                        "continuation-controller.out.log"),
           "[%s] STEP START FINAL-08_SOAK -- 40-minute soak on the frozen runtime"
           % (now - datetime.timedelta(minutes=3)).isoformat())
    _write(os.path.join(rep, "runtime", "jcc", "jcc.pid"), "%d\n" % dead)
    _write_json(os.path.join(rep, "runtime", "jcc", "jcc.ownership.json"),
                {"schema": "jcc.ownership/1", "pid": dead, "port": 4778,
                 "command_line": "%s -m jcc_core.run" % os.path.join(rep, ".venv",
                                                                     "Scripts",
                                                                     "python.exe"),
                 "written_at": (now - datetime.timedelta(hours=1)).isoformat()})
    return {"state": state, "lock": lock}


def _read_lock(rep):
    return R.read_json(os.path.join(rep, ".workbuddy", "locks",
                                    "jcc-continuation.lock.json"))


def run() -> int:
    os.makedirs(WORK, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    head = _head_sha()
    wd = os.path.join(ROOT, "scripts", "continuation-watchdog.ps1")
    cases = []

    for tag, finished in (("self_heal", False), ("finished_release", True)):
        rep = os.path.join(WORK, "%s_%s" % (stamp, tag))
        info = R.build_replica(rep, stub_sleep_s=STUB_SLEEP_S)
        if not info["replica_interpreter_works"]:
            raise SystemExit("the replica has no working interpreter: %r" % info)
        dead = R.dead_pid()
        forged = _forge_a_rebooted_host(rep, dead, finished=finished, head=head)

        out_p = os.path.join(WORK, "%s.out.txt" % tag)
        err_p = os.path.join(WORK, "%s.err.txt" % tag)
        log("tick against %s (state=%s, lock pid=%d dead)"
            % (tag, forged["state"]["runtime_state"], dead))
        rc, took = R.ps_to_files(["-File", wd, "-Root", rep], out_p, err_p, timeout=600)
        returned = time.time()
        stdout = io.open(out_p, "r", encoding="utf-8", errors="replace").read()
        stderr = io.open(err_p, "r", encoding="utf-8", errors="replace").read()
        time.sleep(1.0)

        ticks = R.read_ticks(os.path.join(rep, ".workbuddy", "scheduled", "logs",
                                          "continuation-watchdog.jsonl"))
        note = R.read_json(os.path.join(rep, ".workbuddy", "stub_note.json"))
        lock_after = _read_lock(rep) or {}
        started = [t for t in ticks if t.get("action") == "STARTED"]
        ctl_pid = (note or {}).get("pid") or 0
        ctl_alive_after_tick = R.alive(ctl_pid) if ctl_pid else False

        obs = {
            "role": "negative control" if finished else "the recovery",
            "forged": {"runtime_state": forged["state"]["runtime_state"],
                       "current_step": forged["state"]["current_step"],
                       "project_run": forged["state"]["project_run"],
                       "lock_pid": dead,
                       "lock_expires_at": forged["lock"]["expires_at"],
                       "lock_expiry_was_in_the_future": True,
                       "leftover_service_pid_file": dead,
                       "leftover_ownership_marker_pid": dead},
            "tick": {"exit_code": rc, "seconds": round(took, 2),
                     "stdout": stdout.strip()[:400], "stderr": stderr.strip()[:400],
                     "actions": [t.get("action") for t in ticks],
                     "details": [t.get("detail") for t in ticks]},
            "after": {"lock_pid": lock_after.get("pid"),
                      "lock_expires_at": lock_after.get("expires_at"),
                      "controller_pid": ctl_pid,
                      "controller_note": note,
                      "controller_alive_after_the_tick_returned": ctl_alive_after_tick},
        }
        problems = []
        if rc != 0:
            problems.append("the tick exited %r" % rc)
        if stderr.strip():
            problems.append("the tick wrote to stderr: %s" % stderr.strip()[:200])
        if not ticks:
            problems.append("the tick recorded nothing at all")

        if finished:
            if started:
                problems.append("a FINISHED release was resurrected: %r"
                                % obs["tick"]["actions"])
            if lock_after.get("pid") not in (dead, None):
                problems.append("the lock of a finished release was rewritten: %r"
                                % lock_after.get("pid"))
        else:
            if not started:
                problems.append("no controller was started: %r" % obs["tick"]["actions"])
            if took > STUB_SLEEP_S:
                problems.append("the tick took %.1fs and the stub lives %ds, so the tick "
                                "waited for its child" % (took, STUB_SLEEP_S))
            if not ctl_pid:
                problems.append("no controller ever acquired the lock")
            elif not ctl_alive_after_tick:
                problems.append("controller pid %d had already exited when the tick "
                                "returned: the launch was not detached" % ctl_pid)
            if lock_after.get("pid") != ctl_pid:
                problems.append("the lock still names %r, not the new controller %r"
                                % (lock_after.get("pid"), ctl_pid))
            if (note or {}).get("resumed_from_current_step") != "FINAL-08_SOAK":
                problems.append("the controller resumed at %r instead of the step the "
                                "state file named"
                                % (note or {}).get("resumed_from_current_step"))
        obs["problems"] = problems
        cases.append({"id": tag, "ok": not problems, "observed": obs})
        log("  %-18s %s" % (tag, "OK" if not problems else "*** " + problems[0]))

    problems = [(c["id"], p) for c in cases for p in c["observed"]["problems"]]
    ok = not problems and len(cases) == 2
    payload = {
        "schema": "jcc-reboot-recovery/1",
        "phase": "FINAL-10 -- host-restart recovery simulation",
        "ok": ok,
        "status": "PASS" if ok else "FAIL",
        "verdict": ("PASS: a simulated reboot (dead pid in a lock that had not expired, "
                    "RUNNING state, crash debris) was recovered by one unattended tick, "
                    "and a finished release was left finished"
                    if ok else "FAIL: " + "; ".join("%s %s" % (i, p) for i, p in problems[:6])),
        "checked_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "release_sha": head,
        "question": ("the host came back in the middle of the run -- does the platform "
                     "resume itself from disk with nobody in the loop?"),
        "proves": ("the shipped watchdog recognises a dead-pid lock even when its expiry "
                   "is still in the future, starts a replacement controller, leaves the "
                   "launching tick free to return, and the replacement resumes at the "
                   "persisted step; a release that has COMPLETE + project_run=PASS is not "
                   "resurrected"),
        "does_not_prove": ("that the SERVICE is restarted after a reboot. No component in "
                          "this repository restarts jcc_core if it dies: the service "
                          "watchdog monitors the release state and the port, and the "
                          "operator starts the service. This is the documented gap in "
                          "artifacts/release/PROJECT_RUN_READINESS.md, repeated here "
                          "rather than papered over."),
        "how": ("the tick is the shipped scripts/continuation-watchdog.ps1, run exactly as "
                "the scheduled task runs it except for -Root, which points at a replica "
                "root (tools/continuation/replica.py). scripts/ is copied verbatim; the "
                "controller is a declared stub implementing only the lock handshake and "
                "recording the step it resumed from"),
        "cases": cases,
        "problems": ["%s: %s" % (i, p) for i, p in problems],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
    log("verdict=%s -> %s" % (payload["status"], OUT))
    return 0 if ok else 1


def verdict_of(cases) -> bool:
    """The rule, extracted so ``--selftest`` can falsify it."""
    return bool(cases) and all(c.get("ok") for c in cases)


def selftest() -> int:
    cases = []

    def case(name, ok, detail=""):
        cases.append((name, ok, detail))

    case("a_green_pair_is_green", verdict_of([{"ok": True}, {"ok": True}]), "")
    case("one_red_case_is_red", not verdict_of([{"ok": True}, {"ok": False}]), "")
    case("an_empty_run_is_not_green", not verdict_of([]), "")

    # The negative control must be able to fail in both directions: a finished
    # release that gets a controller started, and a live pipeline that is not
    # recovered. Both are encoded as a case being not-ok.
    case("a_finished_release_that_was_resurrected_is_red",
         not verdict_of([{"ok": False}]), "")
    case("a_live_pipeline_that_was_not_recovered_is_red",
         not verdict_of([{"ok": False}]), "")

    print("=" * 78)
    for name, passed, detail in cases:
        print("  [%s] %-58s %s" % ("PASS" if passed else "FAIL", name, detail[:80]))
    bad = [x for x in cases if not x[1]]
    print("=" * 78)
    print("reboot_recovery_sim selftest: %d/%d" % (len(cases) - len(bad), len(cases)))
    return 0 if not bad else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="host-restart recovery simulation")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
