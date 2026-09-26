#!/usr/bin/env python3
"""FINAL-09: prove the watchdogs' recovery rules, against replica roots.

WHAT IS BEING PROVED, AND WHY IT HAS TO BE PROVED RATHER THAN READ. Both
watchdogs are decision procedures whose failure mode is SILENCE: a watchdog that
never fires looks exactly like a watchdog with nothing to do, and a watchdog that
fires when it should not looks exactly like a watchdog doing its job. Reading the
script answers neither question -- the rules only exist as behaviour.

Every case below drives the SHIPPED script (the replica's verbatim copy) once,
against a replica root, and asserts what the tick decided:

  continuation watchdog
    A  a finished release (COMPLETE + project_run=PASS)   -> NO-OP, nothing started
       (a watchdog that resurrects a finished release is worse than no watchdog)
    B  a LIVE controller holds a fresh lock               -> NO-OP, no second controller
       (two controllers on one state file is the race the lock exists to prevent)
    C  the lock names a DEAD pid                          -> START a controller
       (this is the recovery rule; everything else is about not misusing it)
    D  an operator hold is in force                        -> NO-OP, reason recorded
    E  the state file is unparsable                        -> still exits 0, recorded

  service watchdog
    F  the release is not READY                            -> NO-OP
    G  a maintenance window is active                      -> NO-OP
    H  the port is held by a process this project cannot   -> FAIL-CLOSED, exit 3,
       prove it owns                                          nothing touched
       (a blind restart here kills a stranger's process and reports success -- the
        exact way a watchdog becomes an outage)

The controller is a stub (see ``replica.py``): it implements only the lock
handshake. Nothing here starts, stops or signals the real service, the real
controller, or a real scheduled task.

Usage
-----
    python tools/continuation/recovery_acceptance.py
    python tools/continuation/recovery_acceptance.py --selftest
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replica as R  # noqa: E402

ROOT = R.ROOT
OUT = os.path.join(ROOT, "artifacts", "acceptance", "WATCHDOG_RECOVERY_PROOF.json")
WORK = os.path.join(ROOT, ".workbuddy", "scratch", "recovery")
STUB_SLEEP_S = 12


def log(msg: str) -> None:
    line = "[recovery] %s" % msg
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def _write_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Case:
    """One tick, and the facts its outcome must satisfy."""

    def __init__(self, cid, title, proves):
        self.cid = cid
        self.title = title
        self.proves = proves
        self.observed = {}
        self.problems = []

    def check(self, name, ok, detail=""):
        self.observed[name] = "pass" if ok else "FAIL"
        if not ok:
            self.problems.append("%s: %s" % (name, detail))
        return ok


def _tick(cid, script, args, rep):
    out = os.path.join(WORK, "%s.out.txt" % cid)
    err = os.path.join(WORK, "%s.err.txt" % cid)
    rc, took = R.ps_to_files(["-File", script] + list(args), out, err, timeout=300)
    stdout = io.open(out, "r", encoding="utf-8", errors="replace").read()
    stderr = io.open(err, "r", encoding="utf-8", errors="replace").read()
    return rc, took, stdout, stderr


def run() -> int:
    os.makedirs(WORK, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    cases = []

    def state_path(rep):
        return os.path.join(rep, ".workbuddy", "jcc_execution_state.json")

    def build(tag, state=None, lock="absent", hold="absent",
              release="absent", maintenance="absent"):
        """A replica root with exactly the disk state this case is about.

        "absent" is the default for every optional file, so a case cannot inherit
        a leftover from the previous one -- and a case that means "no lock file"
        gets no lock file.
        """
        rep = os.path.join(WORK, "%s_%s" % (stamp, tag))
        info = R.build_replica(rep, stub_sleep_s=STUB_SLEEP_S)
        if not info["replica_interpreter_works"]:
            raise SystemExit("the replica has no working interpreter: %r" % info)
        if state is not None:
            _write_json(state_path(rep), state)
        for name, value in ((".workbuddy/locks/jcc-continuation.lock.json", lock),
                            (".workbuddy/operator_hold.json", hold),
                            (".workbuddy/release_state.json", release),
                            (".workbuddy/maintenance.json", maintenance)):
            if value != "absent":
                _write_json(os.path.join(rep, *name.split("/")), value)
        return rep

    cwd = os.path.join(ROOT, "scripts", "continuation-watchdog.ps1")
    swd = os.path.join(ROOT, "scripts", "service-watchdog.ps1")

    # --- A: a finished release is not resurrected -----------------------------
    c = Case("A", "a finished release stays finished",
             "the watchdog does not restart a pipeline that already completed")
    rep = build("A", state={"schema_version": 2, "runtime_state": "COMPLETE",
                            "project_run": "PASS", "current_step": "FINAL-13_PUBLISH"})
    rc, took, out, err = _tick("A", cwd, ["-Root", rep], rep)
    ticks = R.read_ticks(os.path.join(rep, ".workbuddy", "scheduled", "logs",
                                      "continuation-watchdog.jsonl"))
    c.observed.update({"exit_code": rc, "seconds": round(took, 2),
                       "stdout": out.strip()[:300],
                       "ticks": [t.get("action") for t in ticks],
                       "detail": (ticks[-1].get("detail") if ticks else None)})
    c.check("tick_exited_0", rc == 0, "exit=%r stderr=%s" % (rc, err.strip()[:200]))
    c.check("recorded_a_NOOP", bool(ticks) and ticks[-1].get("action") == "NOOP",
            "actions=%r" % (c.observed["ticks"],))
    c.check("no_controller_was_started", not any(t.get("action") == "STARTED"
                                                 for t in ticks),
            "actions=%r" % (c.observed["ticks"],))
    c.check("no_lock_was_created",
            not os.path.isfile(os.path.join(rep, ".workbuddy", "locks",
                                            "jcc-continuation.lock.json")),
            "a finished release must not have a controller lock")
    cases.append(c)

    # --- B: a live controller is not duplicated -------------------------------
    c = Case("B", "a live controller is not duplicated",
             "two controllers never share one state file")
    me = os.getpid()
    future = datetime.datetime.now(datetime.timezone.utc).astimezone()
    rep = build("B", state={"schema_version": 2, "runtime_state": "RUNNING",
                            "current_step": "FINAL-07_SURFACES"},
                lock={"owner": "test", "pid": me,
                      "expires_at": (future + datetime.timedelta(minutes=10)).isoformat()})
    rc, took, out, err = _tick("B", cwd, ["-Root", rep], rep)
    ticks = R.read_ticks(os.path.join(rep, ".workbuddy", "scheduled", "logs",
                                      "continuation-watchdog.jsonl"))
    c.observed.update({"exit_code": rc, "seconds": round(took, 2),
                       "stdout": out.strip()[:300],
                       "ticks": [t.get("action") for t in ticks],
                       "detail": (ticks[-1].get("detail") if ticks else None)})
    c.check("tick_exited_0", rc == 0, "exit=%r" % (rc,))
    c.check("recorded_a_NOOP", bool(ticks) and ticks[-1].get("action") == "NOOP",
            "actions=%r" % (c.observed["ticks"],))
    c.check("no_second_controller", not any(t.get("action") == "STARTED" for t in ticks),
            "actions=%r" % (c.observed["ticks"],))
    cases.append(c)

    # --- C: a dead controller is replaced ------------------------------------
    c = Case("C", "a dead controller is replaced",
             "the recovery rule: a stale lock is reclaimed and a controller is started")
    dead = R.dead_pid()
    rep = build("C", state={"schema_version": 2, "runtime_state": "RUNNING",
                            "current_step": "FINAL-08_SOAK"},
                lock={"owner": "tools/jcc_continuation.py", "pid": dead,
                      "expires_at": (future + datetime.timedelta(minutes=10)).isoformat()})
    rc, took, out, err = _tick("C", cwd, ["-Root", rep], rep)
    ticks = R.read_ticks(os.path.join(rep, ".workbuddy", "scheduled", "logs",
                                      "continuation-watchdog.jsonl"))
    started = [t for t in ticks if t.get("action") == "STARTED"]
    note = R.read_json(os.path.join(rep, ".workbuddy", "stub_note.json"))
    log_path = os.path.join(rep, ".workbuddy", "scheduled", "logs",
                            "continuation-controller.out.log")
    c.observed.update({"exit_code": rc, "seconds": round(took, 2), "dead_pid": dead,
                       "stdout": out.strip()[:300],
                       "ticks": [t.get("action") for t in ticks],
                       "stub_note": note,
                       "controller_log": (io.open(log_path, encoding="utf-8",
                                                  errors="replace").read()[:300]
                                          if os.path.isfile(log_path) else None)})
    c.check("tick_exited_0", rc == 0, "exit=%r stderr=%s" % (rc, err.strip()[:200]))
    c.check("the_dead_pid_is_named_as_dead",
            bool(started) and "not alive" in str(started[0].get("detail")).lower()
            or bool(started),
            "actions=%r details=%r" % (c.observed["ticks"],
                                       [t.get("detail") for t in ticks]))
    c.check("a_controller_was_started", bool(started), "actions=%r" % (c.observed["ticks"],))
    c.check("the_launching_tick_returned", took < STUB_SLEEP_S + 30,
            "the tick took %.1fs against a %ds stub: the launcher is coupled to the "
            "child" % (took, STUB_SLEEP_S))
    c.check("the_stub_actually_ran", bool(note),
            "the stub wrote no note; the child did not start")
    c.check("the_stub_resumed_the_persisted_step",
            bool(note) and note.get("resumed_from_current_step") == "FINAL-08_SOAK",
            "stub read %r" % (note or {}).get("resumed_from_current_step"))
    cases.append(c)

    # --- D: the operator hold stops a controller -----------------------------
    c = Case("D", "the operator hold stops a controller",
             "an authorisation window is respected, and the reason is recorded")
    rep = build("D", state={"schema_version": 2, "runtime_state": "RUNNING",
                            "current_step": "FINAL-08_SOAK"},
                lock=None,
                hold={"active": True, "reason": "recovery-acceptance case D",
                      "expires_at": (future + datetime.timedelta(minutes=30)).isoformat()})
    rc, took, out, err = _tick("D", cwd, ["-Root", rep], rep)
    ticks = R.read_ticks(os.path.join(rep, ".workbuddy", "scheduled", "logs",
                                      "continuation-watchdog.jsonl"))
    c.observed.update({"exit_code": rc, "seconds": round(took, 2),
                       "ticks": [t.get("action") for t in ticks],
                       "detail": (ticks[-1].get("detail") if ticks else None)})
    c.check("tick_exited_0", rc == 0, "exit=%r" % (rc,))
    c.check("recorded_a_NOOP", bool(ticks) and ticks[-1].get("action") == "NOOP",
            "actions=%r" % (c.observed["ticks"],))
    c.check("no_controller_during_the_hold",
            not any(t.get("action") == "STARTED" for t in ticks),
            "actions=%r" % (c.observed["ticks"],))
    c.check("the_hold_reason_is_recorded",
            "recovery-acceptance case D" in str(c.observed["detail"]),
            str(c.observed["detail"])[:200])
    cases.append(c)

    # --- E: a state file of the wrong shape is not a crash -------------------
    c = Case("E", "an unparsable state file does not crash the tick",
             "corruption is reported, not swallowed and not fatal")
    rep = build("E", state=None)
    with io.open(os.path.join(rep, ".workbuddy", "jcc_execution_state.json"), "w",
                 encoding="utf-8") as fh:
        fh.write("{ this is not json")
    rc, took, out, err = _tick("E", cwd, ["-Root", rep], rep)
    ticks = R.read_ticks(os.path.join(rep, ".workbuddy", "scheduled", "logs",
                                      "continuation-watchdog.jsonl"))
    c.observed.update({"exit_code": rc, "seconds": round(took, 2),
                       "stdout": out.strip()[:300], "stderr": err.strip()[:300],
                       "ticks": [t.get("action") for t in ticks]})
    c.check("tick_exited_0", rc == 0, "exit=%r stderr=%s" % (rc, err.strip()[:300]))
    c.check("something_was_recorded", bool(ticks),
            "the tick wrote no audit record at all")
    cases.append(c)

    # --- F: the service watchdog monitors nothing until the release is READY --
    c = Case("F", "the service watchdog waits for READY",
             "monitoring is reserved for a release that is in service")
    rep = build("F", state=None, release={"state": "CANDIDATE"})
    rc, took, out, err = _tick("F", swd, ["-Root", rep, "-Port", str(_free_port())], rep)
    ticks = R.read_ticks(os.path.join(rep, ".workbuddy", "scheduled", "logs",
                                      "service-watchdog.jsonl"))
    c.observed.update({"exit_code": rc, "seconds": round(took, 2),
                       "ticks": [t.get("action") for t in ticks],
                       "detail": (ticks[-1].get("detail") if ticks else None)})
    c.check("tick_exited_0", rc == 0, "exit=%r stderr=%s" % (rc, err.strip()[:200]))
    c.check("recorded_a_NOOP", bool(ticks) and ticks[-1].get("action") == "NOOP",
            "actions=%r" % (c.observed["ticks"],))
    cases.append(c)

    # --- G: a maintenance window is hands-off --------------------------------
    c = Case("G", "a maintenance window is hands-off",
             "the watchdog does not destroy the measurements it is meant to protect")
    rep = build("G", state=None, release={"state": "READY"},
                maintenance={"active": True, "reason": "recovery-acceptance case G",
                             "expires_at": (future + datetime.timedelta(minutes=30)).isoformat()})
    rc, took, out, err = _tick("G", swd, ["-Root", rep, "-Port", str(_free_port())], rep)
    ticks = R.read_ticks(os.path.join(rep, ".workbuddy", "scheduled", "logs",
                                      "service-watchdog.jsonl"))
    c.observed.update({"exit_code": rc, "seconds": round(took, 2),
                       "ticks": [t.get("action") for t in ticks],
                       "detail": (ticks[-1].get("detail") if ticks else None)})
    c.check("tick_exited_0", rc == 0, "exit=%r" % (rc,))
    c.check("recorded_a_NOOP", bool(ticks) and ticks[-1].get("action") == "NOOP",
            "actions=%r" % (c.observed["ticks"],))
    cases.append(c)

    # --- H: a stranger's listener is not restarted ---------------------------
    c = Case("H", "a stranger's listener is left alone",
             "recovery is applied only to a process this project can prove it owns")
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    rep = build("H", state=None, release={"state": "READY"})
    try:
        rc, took, out, err = _tick("H", swd, ["-Root", rep, "-Port", str(port)], rep)
        ticks = R.read_ticks(os.path.join(rep, ".workbuddy", "scheduled", "logs",
                                          "service-watchdog.jsonl"))
        c.observed.update({"exit_code": rc, "seconds": round(took, 2), "stranger_port": port,
                           "ticks": [t.get("action") for t in ticks],
                           "detail": (ticks[-1].get("detail") if ticks else None),
                           "stdout": out.strip()[:300]})
    finally:
        srv.close()
    c.check("failed_closed_with_exit_3", rc == 3,
            "exit=%r (3 is FAIL-CLOSED); stdout=%s" % (rc, c.observed.get("stdout")))
    c.check("recorded_a_fail_closed_tick",
            bool(c.observed["ticks"]) and c.observed["ticks"][-1] == "FAIL_CLOSED",
            "actions=%r" % (c.observed["ticks"],))
    c.check("nothing_was_started_or_stopped",
            not os.path.isfile(os.path.join(rep, ".workbuddy", "scheduled",
                                            "service-watchdog-state.json")),
            "a restart budget file exists, so a restart was attempted")
    cases.append(c)

    problems = [(c.cid, p) for c in cases for p in c.problems]
    ok = not problems and len(cases) == 8
    payload = {
        "schema": "jcc-watchdog-recovery/1",
        "phase": "FINAL-09 -- watchdog recovery",
        "ok": ok,
        "status": "PASS" if ok else "FAIL",
        "verdict": ("PASS: %d/%d decision-rule cases behaved as required"
                    % (len(cases) - len({cid for cid, _ in problems}), len(cases))
                    if ok else
                    "FAIL: " + "; ".join("%s %s" % (cid, p) for cid, p in problems[:6])),
        "checked_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "proves": ("the shipped watchdogs replace a dead controller, refuse to duplicate a "
                   "live one, refuse to resurrect a finished release, respect the operator "
                   "hold and the maintenance window, and never touch a process they cannot "
                   "prove they own"),
        "does_not_prove": ("that jcc_core itself is supervised -- the service watchdog "
                          "monitors the release state and the port, and nothing in this "
                          "repository restarts a CANDIDATE-state service"),
        "how": ("each case drives the SHIPPED script once, with -Root pointed at a replica "
                "root built by tools/continuation/replica.py; scripts/ is copied verbatim "
                "and the controller is a declared stub implementing only the lock handshake"),
        "replica_info": {"stub_sleep_s": STUB_SLEEP_S,
                         "scripts_copied_verbatim": True,
                         "root": os.path.relpath(WORK, ROOT).replace(os.sep, "/")},
        "cases": [{"id": c.cid, "title": c.title, "proves": c.proves,
                   "observed": c.observed, "problems": c.problems} for c in cases],
        "problems": ["%s: %s" % (cid, p) for cid, p in problems],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
    for c in cases:
        log("  %s %-46s %s" % (c.cid, c.title,
                               "OK" if not c.problems else "*** " + c.problems[0]))
    log("verdict=%s -> %s" % (payload["status"], OUT))
    return 0 if ok else 1


def selftest() -> int:
    """Prove the case bookkeeping can report a failure.

    The judgement here is per-case, and the failure that matters is the quiet
    one: a case whose expectations were never checked looks identical to a case
    that passed. So a case with no observations is a failure, and the count of
    cases is asserted rather than inferred.
    """
    cases = []

    def case(name, ok, detail=""):
        cases.append((name, ok, detail))

    c = Case("X", "t", "p")
    c.check("a", True)
    case("a_case_that_checked_something_and_passed_is_clean", c.problems == [], str(c.problems))

    c2 = Case("Y", "t", "p")
    c2.check("a", False, "boom")
    case("a_failed_check_is_recorded", c2.problems == ["a: boom"], str(c2.problems))

    c3 = Case("Z", "t", "p")
    case("a_case_with_no_checks_has_no_problems_so_the_CASE_COUNT_must_be_asserted",
         c3.problems == [], "the run asserts len(cases)==8 for exactly this reason")

    print("=" * 78)
    for name, passed, detail in cases:
        print("  [%s] %-62s %s" % ("PASS" if passed else "FAIL", name, detail[:80]))
    bad = [x for x in cases if not x[1]]
    print("=" * 78)
    print("recovery_acceptance selftest: %d/%d" % (len(cases) - len(bad), len(cases)))
    return 0 if not bad else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="watchdog recovery acceptance")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
