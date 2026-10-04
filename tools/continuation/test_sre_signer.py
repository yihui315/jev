#!/usr/bin/env python3
"""Falsification harness for tools/generate_final_sre_go.py.

The signer is the last thing standing between "E-07 passed" and "the leg says so",
so it must be shown to REFUSE. Cases 1-6 pin the leg-position rule; cases 7-10 run
the whole signer against doctored evidence and assert it still answers NO-GO.

Case 0 is the regression lock for the reason this file exists: the signer used to
demand that the legs be signed at the exact commit E-07 ran on, which is stricter
than the gate it feeds and made the legitimate freeze-signed configuration
un-signable.
"""
import importlib.util, io, json, os, shutil, sys, tempfile
from pathlib import Path

# WHY THIS IS DERIVED AND NOT TYPED IN. This used to be `ROOT = r"F:\jev"`, and
# on any host without an F: volume the harness died on its own line 21 with
# FileNotFoundError: 'F:\\jev\\tools\\generate_final_sre_go.py' -- before a
# single falsification case ran. A falsifier that cannot start falsifies
# nothing; it just reports a load error that reads like a product defect.
#
# Same idiom as jcc_core/config.py (Path(__file__).resolve().parents[N]): this
# file is <repo>/tools/continuation/test_sre_signer.py, so parents[2] is the
# repo root. JCC_ROOT still overrides, so an operator can point the harness at
# a different checkout.
_ROOT_OVERRIDE = os.environ.get("JCC_ROOT")
ROOT = str(Path(_ROOT_OVERRIDE).resolve()) if _ROOT_OVERRIDE else str(
    Path(__file__).resolve().parents[2]
)
GEN = os.path.join(ROOT, "tools", "generate_final_sre_go.py")
REAL_EVID = os.path.join(ROOT, "artifacts", "acceptance", "win_scripts",
                         "E07_FINAL_EVIDENCE.json")

spec = importlib.util.spec_from_file_location("gen", GEN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

TMP = tempfile.mkdtemp(prefix="sre-signer-falsify-")
REAL_GIT = mod.git


def case(name, fn, expect):
    try:
        got, why = fn()
    except Exception as exc:                                  # noqa: BLE001
        got, why = False, "raised %s: %s" % (type(exc).__name__, exc)
    hit = (got == expect)
    print("%-58s %s   (%s)" % (name, "OK" if hit else "*** WRONG ***", why[:96]))
    return hit


ok = []

# ---- 1. the leg-position rule -------------------------------------------------
FREEZE = mod.RELEASE_FREEZE
HEAD = REAL_GIT("rev-parse", "HEAD")

ok.append(case("0 REGRESSION: real heads (E-07@HEAD, legs@freeze) accepted",
               lambda: mod.leg_position_ok(FREEZE, HEAD), True))
ok.append(case("1 leg signed at the very commit E-07 ran on is accepted",
               lambda: mod.leg_position_ok(HEAD, HEAD), True))
ok.append(case("2 leg signed at an unrelated commit is REFUSED",
               lambda: mod.leg_position_ok("0" * 40, HEAD), False))
ok.append(case("3 leg that names no release_sha is REFUSED",
               lambda: mod.leg_position_ok("", HEAD), False))


def _freeze_with_offender():
    mod.git = lambda *a: "artifacts/x.json packages/jcc_core/core.py"
    try:
        return mod.leg_position_ok(FREEZE, HEAD)
    finally:
        mod.git = REAL_GIT


ok.append(case("4 freeze-signed leg, a PRODUCT path moved -> REFUSED",
               _freeze_with_offender, False))


def _freeze_evidence_only():
    mod.git = lambda *a: "artifacts/x.json docs/y.md scripts/start.ps1"
    try:
        return mod.leg_position_ok(FREEZE, HEAD)
    finally:
        mod.git = REAL_GIT


ok.append(case("5 freeze-signed leg, evidence+scripts moved -> accepted",
               _freeze_evidence_only, True))
def _gitignore_is_evidence():
    hit = any(".gitignore".startswith(p) for p in mod.EVIDENCE_PREFIXES)
    return hit, "prefixes=%r" % (mod.EVIDENCE_PREFIXES,)


ok.append(case("6 .gitignore counted as release evidence",
               _gitignore_is_evidence, True))


# ---- 2. the whole signer against doctored evidence ----------------------------
def _run(evidence_obj=None, arch="GO", sec="GO", name="run"):
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    ev_path = os.path.join(d, "E07.json")
    out_path = os.path.join(d, "final_sre_go.json")
    arch_path = os.path.join(d, "arch.json")
    sec_path = os.path.join(d, "sec.json")
    if evidence_obj is None:
        evidence_obj = json.load(io.open(REAL_EVID, encoding="utf-8"))
    io.open(ev_path, "w", encoding="utf-8").write(json.dumps(evidence_obj))
    for p, v in ((arch_path, arch), (sec_path, sec)):
        io.open(p, "w", encoding="utf-8").write(json.dumps(
            {"verdict": v, "release_sha": FREEZE, "signed_at": "t",
             "conditions": ["c"] if v == "GO WITH CONDITIONS" else []}))
    saved = (mod.ARCH_LEG, mod.SEC_LEG)
    mod.ARCH_LEG, mod.SEC_LEG = arch_path, sec_path
    try:
        rc = mod.main(["--evidence", ev_path, "--out", out_path])
        doc = json.load(io.open(out_path, encoding="utf-8"))
    finally:
        mod.ARCH_LEG, mod.SEC_LEG = saved
    return rc, doc


def _expect(rc_want, verdict_want, mutate=None, **kw):
    def run():
        ev = None
        if mutate is not None:
            ev = json.load(io.open(REAL_EVID, encoding="utf-8"))
            mutate(ev)
        rc, doc = _run(evidence_obj=ev, **kw)
        got = (rc == rc_want and doc.get("verdict") == verdict_want)
        return got, "rc=%s verdict=%s problems=%s" % (
            rc, doc.get("verdict"), (doc.get("problems") or ["-"])[0][:70])
    return run


def _fail_step(ev):
    for s in ev["steps"]:
        if s["step"] == 14:
            s["result"] = "FAIL"


def _abort(ev):
    ev["abort"] = {"step": 11, "reason": "synthetic abort for the falsification harness"}


def _drop_step(ev):
    ev["steps"] = [s for s in ev["steps"] if s["step"] != 22]


ok.append(case("7 real evidence + two GO legs -> GO",
               _expect(0, "GO"), True))
ok.append(case("8 one E-07 step FAIL -> NO-GO",
               _expect(1, "NO-GO", mutate=_fail_step), True))
ok.append(case("9 E-07 aborted -> NO-GO",
               _expect(1, "NO-GO", mutate=_abort), True))
ok.append(case("10 step missing from the evidence -> NO-GO",
               _expect(1, "NO-GO", mutate=_drop_step), True))
ok.append(case("11 architecture leg NO-GO -> NO-GO",
               _expect(1, "NO-GO", arch="NO-GO"), True))
ok.append(case("12 security leg BLOCKED -> NO-GO",
               _expect(1, "NO-GO", sec="BLOCKED"), True))
ok.append(case("13 GO WITH CONDITIONS leg accepted",
               _expect(0, "GO", arch="GO WITH CONDITIONS"), True))

# ---- 3. no switch can make it lie --------------------------------------------
src = io.open(GEN, encoding="utf-8").read()
flags = [a for a in ("--force", "--go", "--verdict", "--skip", "--no-check")
         if a in src]
ok.append(case("14 no flag can force a GO (only --evidence/--out exist)",
               lambda: (flags == [], "found %r" % flags), True))

print()
print("SIGNER RESULTS: %d/%d cases as expected" % (sum(ok), len(ok)))
print("SIGNER_CAN_STILL_REFUSE =", all(ok))
shutil.rmtree(TMP, ignore_errors=True)
if not all(ok):
    print("FAILED CASES:", [i for i, v in enumerate(ok) if not v])
    sys.exit(1)
