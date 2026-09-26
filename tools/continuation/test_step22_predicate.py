#!/usr/bin/env python3
"""Falsification test for the step-22 predicate.

A gate that cannot be shown to fail is not a gate. This proves the rewritten
self-check still catches every class of violation it claims to, and that a
genuinely complete record set passes.

WHY THIS FILE WAS REWRITTEN: the previous version fed the predicate a 22-element
list (the whole evidence file, step 22 flipped to PASS), so `len(steps)==22` held
and the baseline "passed". But the runner calls the predicate with the COMPLETED
PREFIX -- 21 records -- while SELF_CHECK is still in flight. The test was testing
an arity that production never produces, so it certified an unsatisfiable gate.
Every case below therefore uses the production call shape:
    self_check_problems(prefix, in_flight=1)
and case 8 is a permanent regression lock on that arity mistake.
"""
import copy, importlib.util, io, json, sys

HARNESS = r"F:\jev\artifacts\acceptance\win_scripts\run_e07_final.py"
EVID = r"F:\jev\artifacts\acceptance\win_scripts\E07_FINAL_EVIDENCE.json"

spec = importlib.util.spec_from_file_location("e07h", HARNESS)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

EXPECTED = mod.EXPECTED_STEPS
doc = json.load(io.open(EVID, encoding="utf-8"))

# Reconstruct the production prefix: everything except SELF_CHECK, all PASS.
prefix = [copy.deepcopy(x) for x in doc["steps"] if x["step"] != EXPECTED]
for s in prefix:
    s["result"] = "PASS"
prefix.sort(key=lambda s: s["step"])
assert len(prefix) == EXPECTED - 1, "fixture must be the %d-record prefix" % (EXPECTED - 1)


def case(name, mutate, expect):
    steps = copy.deepcopy(prefix)
    mutate(steps)
    bad = mod.self_check_problems(steps, in_flight=1)
    hit = expect(bad)
    print("%-56s %s   (%d problem(s))"
          % (name, "CAUGHT" if hit else "*** MISSED ***", len(bad)))
    if bad:
        print("      e.g. %s" % bad[0])
    return hit


ok = []
ok.append(case("0 baseline: production prefix (21+1 in flight)",
               lambda s: None,
               lambda b: len(b) == 0))

ok.append(case("1 script step loses stdout_sha256",
               lambda s: [x for x in s if x["step"] == 6][0].__setitem__("stdout_sha256", None),
               lambda b: any("stdout_sha256" in p for p in b)))

ok.append(case("2 a step loses its timestamp",
               lambda s: [x for x in s if x["step"] == 9][0].__setitem__("timestamp", ""),
               lambda b: any("HEALTH:timestamp" in p for p in b)))

ok.append(case("3 script step loses exit_code",
               lambda s: [x for x in s if x["step"] == 19][0].__setitem__("exit_code", None),
               lambda b: any("START_AGAIN:exit_code" in p for p in b)))

ok.append(case("4 a step is not PASS",
               lambda s: [x for x in s if x["step"] == 14][0].__setitem__("result", "FAIL"),
               lambda b: any("THROUGHPUT:FAIL" in p for p in b)))

ok.append(case("5 a script step is missing entirely",
               lambda s: s.remove([x for x in s if x["step"] == 16][0]),
               lambda b: any("script step 16" in p for p in b)))

ok.append(case("6 prefix short by one (20+1)",
               lambda s: s.pop(),
               lambda b: any("step_count 20" in p for p in b)))

ok.append(case("7 a script step loses its command",
               lambda s: [x for x in s if x["step"] == 11][0].__setitem__("command", None),
               lambda b: any("RESTART:command" in p for p in b)))

# --- regression locks for THIS defect -----------------------------------------


def _arity_mistake(s):
    """Hand the predicate all 22 records -- the shape the old test used, and the
    shape the runner must never use (step 22 cannot contain itself)."""
    s.append({"step": EXPECTED, "name": "SELF_CHECK", "timestamp": "t", "result": "PASS"})


ok.append(case("8 REGRESSION: full 22-record list (old test's arity)",
               _arity_mistake,
               lambda b: any("in flight" in p for p in b)))


def _gap_plus_duplicate(s):
    """Same length, wrong set: drop step 10, duplicate step 5. A bare count
    comparison cannot see this; the step-set check must."""
    s.remove([x for x in s if x["step"] == 10][0])
    s.append(copy.deepcopy([x for x in s if x["step"] == 5][0]))


ok.append(case("9 same length, one step dropped + one duplicated",
               _gap_plus_duplicate,
               lambda b: any("step set" in p for p in b)))


def _duplicate_only(s):
    s.append(copy.deepcopy([x for x in s if x["step"] == 3][0]))


ok.append(case("10 prefix carries a duplicated record",
               _duplicate_only,
               lambda b: any("step set" in p for p in b)))

print()
print("FALSIFICATION RESULT: %d/%d classes caught; baseline=%s" %
      (sum(ok[1:]), len(ok) - 1, "PASS (no problems)" if ok[0] else "FAIL"))
print("GATE_CAN_STILL_FAIL =", all(ok[1:]))
print("BASELINE_COMPLETE_SET_PASSES =", ok[0])
if not all(ok):
    print("FAILED CASES:", [i for i, v in enumerate(ok) if not v])
    sys.exit(1)
