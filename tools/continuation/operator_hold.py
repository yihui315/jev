#!/usr/bin/env python3
"""Operator hold: tell the continuation watchdog to stand down, on the record.

WHY THIS IS NOT A SILENCING SWITCH
----------------------------------
The continuation watchdog restarts the controller whenever the lock is stale,
and its only other idle condition is "the release is COMPLETE with
project_run=PASS". So the cheap way to stop it while a human edits the platform
would be to write those two fields -- which makes a FALSE STATEMENT ABOUT THE
RELEASE in order to quiet a script. This project refuses that class of move
everywhere else, so the mechanism is instead a separate, explicit flag that
records the truth: a person is working on the platform right now.

TWO PROPERTIES IT MUST HAVE
---------------------------
1. **It expires.** ``--set`` requires a duration and refuses anything over an
   hour. A hold without an end is a platform that has stopped running and
   cannot say why: the watchdog would be indistinguishable from one with
   nothing to do.
2. **It is observable.** The watchdog logs a NOOP naming the reason and the
   expiry, ``scripts/continuation-watchdog-status.ps1`` reports it, and this
   file prints it. A hold that nobody can see is a hold that gets forgotten.

A hold whose expiry is unreadable is NOT honoured -- the watchdog says so and
resumes. That asymmetry is deliberate: honouring a corrupt hold freezes the
release forever and silently, while ignoring it costs one wasted tick.

Usage
-----
    python tools/continuation/operator_hold.py --set --minutes 20 --reason "editing scripts"
    python tools/continuation/operator_hold.py --show
    python tools/continuation/operator_hold.py --clear
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime, timedelta

ROOT = r"F:\jev"
HOLD = os.path.join(ROOT, ".workbuddy", "operator_hold.json")
MAX_MINUTES = 60


def now() -> datetime:
    return datetime.now().astimezone()


def read() -> dict:
    if not os.path.isfile(HOLD):
        return {}
    try:
        with io.open(HOLD, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write(payload: dict) -> None:
    os.makedirs(os.path.dirname(HOLD), exist_ok=True)
    with io.open(HOLD, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def in_force(payload: dict) -> bool:
    """True when the hold is active AND its expiry parses AND it is in the future.

    The same rule the watchdog applies, kept here so ``status`` cannot report a
    hold as in force when the watchdog would ignore it -- a status command that
    disagrees with the thing it reports on is worse than no status command.
    """
    if payload.get("active") is not True:
        return False
    expires = payload.get("expires_at")
    if not expires:
        return False
    try:
        return datetime.fromisoformat(str(expires)) > now()
    except ValueError:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="operator hold for the JCC watchdogs")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--set", action="store_true")
    group.add_argument("--clear", action="store_true")
    group.add_argument("--show", action="store_true")
    ap.add_argument("--minutes", type=int, default=0,
                    help="how long the hold lasts; required with --set, max %d" % MAX_MINUTES)
    ap.add_argument("--reason", default="", help="what the operator is doing; required with --set")
    args = ap.parse_args(argv)

    if args.set:
        if not args.reason.strip():
            print("refusing: --reason is required. A hold that does not say why is "
                  "a platform that has stopped running for no recorded reason.")
            return 2
        if args.minutes <= 0:
            print("refusing: --minutes is required and must be positive. A hold with "
                  "no end is indistinguishable from a dead watchdog.")
            return 2
        if args.minutes > MAX_MINUTES:
            print("refusing: --minutes %d exceeds the %d-minute cap. Re-raise the hold "
                  "if the work is not finished -- an unbounded hold is how a platform "
                  "silently stops." % (args.minutes, MAX_MINUTES))
            return 2
        payload = {
            "active": True,
            "reason": args.reason.strip(),
            "set_at": now().isoformat(timespec="seconds"),
            "expires_at": (now() + timedelta(minutes=args.minutes)).isoformat(timespec="seconds"),
            "set_by": os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
            "note": "the continuation watchdog will not start a controller while this "
                    "hold is in force; the service watchdog is unaffected",
        }
        write(payload)
        print("hold raised until %s" % payload["expires_at"])
        print("  reason: %s" % payload["reason"])
        return 0

    if args.clear:
        before = read()
        write({"active": False, "reason": before.get("reason") or args.reason,
               "cleared_at": now().isoformat(timespec="seconds"),
               "set_at": before.get("set_at"), "expires_at": None})
        print("hold cleared (the file is kept, with active=false, so the history of "
              "who stopped the platform and when is not erased)")
        return 0

    payload = read()
    print(json.dumps({
        "path": HOLD,
        "present": os.path.isfile(HOLD),
        "in_force": in_force(payload),
        "payload": payload,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
