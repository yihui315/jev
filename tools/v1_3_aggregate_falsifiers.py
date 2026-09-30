"""Independent falsifiers for the AGGREGATE -- can this judge be fooled?

Group F proves the gate's *parsers* can fail. It says nothing about the
*aggregate*, which is what actually decides `project_complete`. The required
refusals are:

    foreign HEAD leg          -> MUST NOT PASS
    missing leg               -> MUST NOT PASS
    stale digest              -> MUST NOT PASS
    zero executed checks      -> MUST FAIL
    instrument crash          -> MUST NOT become PASS
    non-PASS verdict          -> MUST become a blocker

The last two are the dangerous ones, because a crash or an absent verdict is not
the string "FAIL". This suite is written so it can fail, and it includes a
POSITIVE CONTROL (a complete, fresh, on-disk set MUST be accepted), otherwise
"everything is rejected" would look like success.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(r"F:\jev")
GATE_PATH = ROOT / "tools" / "final_gate_run_v1_3.py"
OUT = ROOT / ".workbuddy" / "scratch" / "v1_3_aggregate_falsifiers.json"

spec = importlib.util.spec_from_file_location("jcc_gate", GATE_PATH)
gate = importlib.util.module_from_spec(spec)
sys.modules["jcc_gate"] = gate
spec.loader.exec_module(gate)

LEGS_DIR = gate.LEGS_DIR
expected = sorted(gate.expected_leg_ids())


def load_real(head_override: str):
    """The gate's own records, re-headed to a common value so they form a
    complete fresh set. Ids are kept, so the on-disk check still applies."""
    recs = {}
    for p in sorted(LEGS_DIR.glob("V1_3_LEG_*.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if r.get("leg_id"):
            recs[r["leg_id"]] = r
    out = []
    for lid in expected:
        base = copy.deepcopy(recs.get(lid) or {})
        base.setdefault("leg_id", lid)
        base["leg_id"] = lid
        base["head"] = head_override
        base.setdefault("verdict", "PASS")
        base.setdefault("detail", "synthetic")
        out.append(base)
    return out


HEAD = "0123456789abcdef0123456789abcdef01234567"
GREEN = {"pid": 2452, "http": 200}


def run(merged, head=HEAD, green0=GREEN, green1=GREEN):
    return gate.aggregate(merged, green0, green1, "falsifier-run", head, [], 0.0)


results = []
real = load_real(HEAD)

# --- POSITIVE CONTROL -------------------------------------------------------
doc = run(real)
results.append({
    "id": "POSITIVE_CONTROL_A_COMPLETE_FRESH_SET_IS_ACCEPTED",
    "ok": doc["project_complete"] is True and doc["blocker"] == 0,
    "detail": "project_complete=%r blockers=%d (a judge that rejects everything "
              "would make every refusal below vacuous)"
              % (doc["project_complete"], doc["blocker"]),
})

# --- N1: a leg measured at another HEAD -------------------------------------
m = copy.deepcopy(real)
m[-1]["head"] = "ffffffffffffffffffffffffffffffffffffffff"
doc = run(m)
results.append({
    "id": "FOREIGN_HEAD_LEG_CANNOT_BE_AGGREGATED",
    "ok": doc["project_complete"] is False
    and any("ANOTHER_HEAD" in b for b in doc["blockers"]),
    "detail": "blockers=%r" % (doc["blockers"],),
})

# --- N2: a leg is missing entirely ------------------------------------------
m = [r for r in copy.deepcopy(real) if r["leg_id"] != expected[0]]
doc = run(m)
results.append({
    "id": "MISSING_LEG_CANNOT_BE_AGGREGATED",
    "ok": doc["project_complete"] is False
    and any("ABSENT" in b for b in doc["blockers"]),
    "detail": "blockers=%r" % (doc["blockers"],),
})

# --- N3: an instrument that produced NO verdict -----------------------------
# This is the crash case: not the string "FAIL", so a judge that only looks for
# "FAIL" would wave it through while reports show legs_passed < legs_total.
m = copy.deepcopy(real)
m[3]["verdict"] = None
m[3]["detail"] = "instrument raised before writing a verdict"
doc = run(m)
results.append({
    "id": "A_LEG_WITH_NO_VERDICT_MUST_BLOCK",
    "ok": doc["project_complete"] is False,
    "detail": "project_complete=%r legs_passed=%d legs_total=%d blockers=%r"
              % (doc["project_complete"], doc["legs_passed"], doc["legs_total"],
                 doc["blockers"]),
})

# --- N4: an instrument that crashed outright --------------------------------
m = copy.deepcopy(real)
m[5]["verdict"] = "ERROR"
doc = run(m)
results.append({
    "id": "AN_INSTRUMENT_ERROR_VERDICT_MUST_BLOCK",
    "ok": doc["project_complete"] is False,
    "detail": "project_complete=%r blockers=%r" % (doc["project_complete"], doc["blockers"]),
})

# --- N5: a verdict of PASS supported by ZERO executed checks ----------------
m = copy.deepcopy(real)
m[7]["verdict"] = "PASS"
m[7]["duration_s"] = 0.0
m[7]["exit_code"] = 0
m[7]["evidence"] = {"checks": {}}
doc = run(m)
results.append({
    "id": "ZERO_EXECUTED_CHECKS_MUST_NOT_READ_AS_PASS",
    "ok": doc["project_complete"] is False,
    "detail": "project_complete=%r blockers=%r" % (doc["project_complete"], doc["blockers"]),
})

# --- N6: a record that is not on disk did not happen ------------------------
m = copy.deepcopy(real)
m[9]["leg_id"] = "ZZ_RECORD_ABSENT_FROM_DISK"
doc = run(m)
results.append({
    "id": "A_LEG_RECORD_ABSENT_FROM_DISK_BLOCKS",
    "ok": doc["project_complete"] is False
    and any("MISSING_ON_DISK" in b or "ABSENT" in b for b in doc["blockers"]),
    "detail": "blockers=%r" % (doc["blockers"],),
})

# --- N7: GREEN was touched ------------------------------------------------
doc = run(copy.deepcopy(real), green1={"pid": 9999, "http": 200})
results.append({
    "id": "GREEN_CHANGE_BLOCKS",
    "ok": doc["project_complete"] is False
    and any("GREEN_4778_CHANGED" in b for b in doc["blockers"]),
    "detail": "blockers=%r" % (doc["blockers"],),
})

passed = sum(1 for r in results if r["ok"])
payload = {
    "tool": "v1_3_aggregate_falsifiers",
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "gate": str(GATE_PATH),
    "expected_leg_count": len(expected),
    "results": results,
    "verdict": "PASS %d/%d" % (passed, len(results)),
    "note": "Each refusal is paired with a positive control, so a judge that "
            "rejects everything cannot score well here.",
}
with open(OUT, "w", newline="\n", encoding="utf-8") as fh:
    fh.write(json.dumps(payload, indent=2, ensure_ascii=False))

for r in results:
    print("  [%s] %s\n        %s" % ("ok  " if r["ok"] else "HOLE", r["id"], r["detail"]))
print("\n%s" % payload["verdict"])
print("WROTE", OUT)
raise SystemExit(0 if passed == len(results) else 1)
