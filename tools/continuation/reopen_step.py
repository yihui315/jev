#!/usr/bin/env python3
"""Re-open a step the controller has given up on -- explicitly, with a reason on record.

WHY THIS EXISTS. `run_pipeline` gives up on a step after `len(BACKOFF_LADDER)`
attempts and stops the pipeline ("leaving state for a human"). That is the right
behaviour -- a controller that retries forever hides a real blocker -- but the
remedy it names, "investigate X and re-run `--resume`", has no way to re-open the
step: `--resume` does not touch the attempt counter, so the next invocation gives
up again after one try. Without this tool the only way forward is to hand-edit the
state file, and hand-editing a state file is how a pipeline gets talked into a
PASS it did not earn.

So the re-open is a TOOL with guard rails rather than an edit:

  * a reason is REQUIRED, and it is stored in the state file, so the next reader
    sees what was re-opened and why before seeing the verdict;
  * a step recorded PASS cannot be re-opened -- there is nothing to re-open, and
    turning a finished step back into pending is how a release is assembled twice;
  * the before/after attempt counts are recorded, so the reset is visible as a
    reset rather than looking like the step had never been attempted;
  * it does NOT touch any verdict, any artifact, or any evidence hash. It only
    returns a step to the queue.

Use:
    python tools/continuation/reopen_step.py --step FINAL-05_DETACHED \
        --reason "the blocker was the operator hold, which the operator had raised"
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List

ROOT = str(Path(__file__).resolve().parents[2])
STATE = os.path.join(ROOT, ".workbuddy", "jcc_execution_state.json")


def log(msg: str) -> None:
    sys.stdout.write("%s\n" % msg)
    sys.stdout.flush()


def load_state() -> Dict[str, Any]:
    with io.open(STATE, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def save_state(state: Dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STATE), suffix=".tmp")
    try:
        with io.open(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, STATE)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def reopen(state: Dict[str, Any], step: str, reason: str, by: str,
           now: str) -> Dict[str, Any]:
    """The rule, extracted so ``--selftest`` can falsify it. Returns the entry added.

    Raises ValueError when the request must be refused; every refusal names why.
    """
    steps = state.get("steps") or {}
    if step not in steps:
        raise ValueError("%s has never run, so there is nothing to re-open" % step)
    rec = steps[step] or {}
    if rec.get("status") == "PASS":
        raise ValueError("%s is recorded PASS; re-opening a finished step would let a "
                         "release be assembled twice" % step)

    was = int((state.get("attempts") or {}).get(step) or 0)
    entry = {
        "step": step,
        "at": now,
        "by": by,
        "reason": reason,
        "attempts_before": was,
        "attempts_after": 0,
        "status_before": rec.get("status"),
        "detail_before": str(rec.get("detail"))[:300],
        "note": ("the attempt counter is a RETRY BUDGET, not evidence. Resetting it "
                 "does not touch any verdict, artifact or hash; it returns the step to "
                 "the queue so the controller may try again."),
    }
    state.setdefault("attempts", {})[step] = 0
    state.setdefault("reopened", []).append(entry)
    rec["status"] = "PENDING"
    rec["detail"] = "re-opened by %s at %s: %s" % (by, now, reason)
    if (state.get("last_error") or {}).get("step") == step:
        state["last_error"] = None
    return entry


def selftest() -> int:
    cases: List[Any] = []

    def case(name: str, ok: bool, detail: str = "") -> None:
        cases.append((name, ok, detail))

    def base(status: str = "BLOCKED") -> Dict[str, Any]:
        return {"steps": {"S": {"status": status, "detail": "d"}},
                "attempts": {"S": 7}, "last_error": {"step": "S"}}

    st = base()
    entry = reopen(st, "S", "because the cause was removed", "Administrator", "NOW")
    case("a_blocked_step_can_be_reopened", st["steps"]["S"]["status"] == "PENDING", "")
    case("the_attempt_counter_is_reset", st["attempts"]["S"] == 0, str(st["attempts"]))
    case("the_reason_is_on_record", entry["reason"] == "because the cause was removed"
         and st["reopened"][0]["reason"] == entry["reason"], "")
    case("the_reset_is_visible_as_a_reset",
         entry["attempts_before"] == 7 and entry["attempts_after"] == 0, str(entry))
    case("the_stale_last_error_is_cleared", st["last_error"] is None, "")

    try:
        reopen(base("PASS"), "S", "r", "b", "NOW")
        case("a_finished_step_cannot_be_reopened", False, "it was allowed")
    except ValueError as exc:
        case("a_finished_step_cannot_be_reopened", "PASS" in str(exc), str(exc))

    try:
        reopen({"steps": {}}, "NOPE", "r", "b", "NOW")
        case("an_unknown_step_is_refused", False, "it was allowed")
    except ValueError as exc:
        case("an_unknown_step_is_refused", "never run" in str(exc), str(exc))

    for name, passed, detail in cases:
        log("  [%s] %-44s %s" % ("PASS" if passed else "FAIL", name, detail[:100]))
    good = sum(1 for _n, p, _d in cases if p)
    log("reopen_step selftest: %d/%d" % (good, len(cases)))
    return 0 if good == len(cases) else 1


def main(argv: List[str] | None = None) -> int:
    import datetime
    # NOTE `--step` is deliberately NOT `required=True`. With `required=True`
    # argparse refuses `--selftest` at parse time (exit 2, "the following
    # arguments are required: --step") and the self-test branch is unreachable --
    # i.e. the instrument cannot be checked without also naming a step to touch.
    # A self-test that needs a real target is not a self-test. The obligation is
    # enforced below instead, on the path that actually writes.
    ap = argparse.ArgumentParser(description="re-open a step the controller gave up on")
    ap.add_argument("--step", default="", help="required (unless --selftest)")
    ap.add_argument("--reason", default="", help="required: why the blocker is gone")
    ap.add_argument("--by", default=os.environ.get("USERNAME") or "operator")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.step.strip():
        log("refusing: no --step given; name the step to return to the queue")
        return 2
    if not args.reason.strip():
        log("refusing: a re-open without a stated reason is indistinguishable from "
            "editing the state file until it agrees with you")
        return 2
    state = load_state()
    try:
        entry = reopen(state, args.step, args.reason.strip(), args.by,
                       datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
    except ValueError as exc:
        log("refusing: %s" % exc)
        return 2
    save_state(state)
    log("re-opened %s: attempts %s -> 0" % (entry["step"], entry["attempts_before"]))
    log("  reason: %s" % entry["reason"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
