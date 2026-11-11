/**
 * final10-judge.test.mjs — the FINAL-10 verdict must be about THIS run.
 *
 * WHAT THIS CLOSES
 * ----------------
 * `_ok_judge` reads `ok` and nothing else, and `ok` is written by whoever produced
 * the file. A git-tracked PASS artifact from an earlier era therefore satisfied the
 * step's verdict, and `run_step` recorded `exit_code` without ever consulting it.
 * Measured before the fix, on the real module and the real tracked file:
 *
 *     _ok_judge({"ok": true, "release_sha": "cefed661...", "platform": <absent>})
 *       -> (True, "PASS: a simulated reboot ... was recovered by ...")
 *
 * A 337-commit-old file passed. The assertions below are the five ways that must
 * now be red, plus two invariants that keep the fix from being undone quietly.
 *
 * WHY IT SPAWNS PYTHON INSTEAD OF RE-IMPLEMENTING THE RULES
 * --------------------------------------------------------
 * The thing under test is Python. Re-writing its rules in JS would test the copy,
 * not the code — two implementations agreeing is evidence about the author, not
 * about the target. The child imports the real module and calls the real
 * `_judge_artifact`; this file only asserts on what comes back.
 *
 * PYTHON ABSENT IS A FAILURE, NOT A SKIP. "The gate could not check this" and "the
 * gate checked this and it was fine" must never print the same summary line: that
 * is the F-26 shape (missing evidence read as passing evidence). The suite exits
 * non-zero and says SKIPPED with the reason, so the floor cannot count it as a pass.
 */
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..", "..", "..");

const PY_CANDIDATES = [
  process.env.JCC_PY,
  resolve(REPO, ".venv", "Scripts", "python.exe"),
  resolve(REPO, ".venv", "bin", "python"),
  "python",
  "python3",
].filter(Boolean);

const PROBE = String.raw`
import io, json, os, sys, tempfile

repo = os.environ["JCC_REPO"]
sys.path.insert(0, os.path.join(repo, "tools"))
import jcc_continuation as m

REL = "artifacts/acceptance/REBOOT_RECOVERY.json"
GOOD_SHA = "a" * 40
RUN_START = "2026-11-12T01:00:00+08:00"
_UNSET = object()

m._LEASE["head_sha"] = GOOD_SHA
m._LEASE["run_started_at"] = RUN_START

def good_rec():
    return {
        "schema": 1,
        "ok": True,
        "status": "PASS",
        "verdict": "a simulated reboot was recovered by one unattended tick",
        "release_sha": GOOD_SHA,
        "platform": {"os": "windows", "arch": "x86_64", "node": "v22.22.2"},
        "checked_at": "2026-11-12T01:05:00+08:00",
        "cases": [],
        "problems": [],
    }

def run(name, rec, *, write=True, exit_code=0, mtime_shift=None, started_at=RUN_START,
        detail=None, omit_exit_code=False, since=_UNSET):
    root = tempfile.mkdtemp()
    m.ROOT = root
    path = os.path.join(root, *REL.split("/"))
    if write:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        if mtime_shift is not None:
            st = os.stat(path)
            os.utime(path, (st.st_atime + mtime_shift, st.st_mtime + mtime_shift))
    # The judge must come from the STEPS TABLE, not from a name written here.
    # Hard-coding m._final10_judge would make every case below pass even if the
    # table still wired _ok_judge -- the test would be checking a function that
    # nothing calls.
    wired = [s for s in m.STEPS if s.id == "FINAL-10_REBOOT_SIM"][0]
    step = m.Step("FINAL-10_REBOOT_SIM", "probe", "x.py", (), REL,
                  wired.judge, 60)
    record = {
        "id": step.id,
        "started_at": started_at,
        "freshness_since": started_at if since is _UNSET else since,
    }
    if not omit_exit_code:
        record["exit_code"] = exit_code
    if detail is not None:
        record["detail"] = detail
    out = m._judge_artifact(step, record)
    print("CASE\t%s\t%s\t%s" % (name, out.get("status"), (out.get("detail") or "").replace("\t", " ")[:400]))
    return out

def attach(name, desc, *, write=True):
    """Drive the REAL attach_to_run, not a reconstruction of it.

    This exists because the two gates failed TOGETHER on exactly this path: an
    attach record carries no exit code of its own, and the descriptor may carry no
    started_at. Asserting on attach is the only way to know the combination is
    closed, and pid=-1 makes pid_alive False deterministically instead of
    racing a real process.
    """
    root = tempfile.mkdtemp()
    m.ROOT = root
    m.RUNS_DIR = os.path.join(root, ".workbuddy", "runs")
    os.makedirs(m.RUNS_DIR, exist_ok=True)
    path = os.path.join(root, *REL.split("/"))
    if write:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(good_rec(), fh)
    step = [s for s in m.STEPS if s.id == "FINAL-10_REBOOT_SIM"][0]
    with io.open(m.run_descriptor_path(step), "w", encoding="utf-8") as fh:
        json.dump(desc, fh)
    out = m.attach_to_run(step)
    print("CASE\t%s\t%s\t%s" % (name, out.get("status"), (out.get("detail") or "").replace("\t", " ")[:400]))
    return out

# 1. the happy path must still pass, or the fix would be a blanket refusal
good_out = run("GOOD", good_rec())

# 1b. CONTROL for the detail-carry rule: on a PASS the detail must be the judge's
#     own text, not a stitched-together history. Without this, "keep the reason"
#     could be implemented as "always keep everything" and no case would notice.
print("CASE\tGOOD_DETAIL_IS_JUDGE_TEXT\t%s\t%s" % (
    "PASS" if (good_out.get("detail") or "") == good_rec()["verdict"] else "FAIL",
    "detail=%r" % (good_out.get("detail"),)))

# 2. a stale candidate SHA -> red
r = good_rec(); r["release_sha"] = "b" * 40
run("OLD_SHA", r)

# 3. platform missing -> red (a reboot claim with no platform is not a claim)
r = good_rec(); r.pop("platform")
run("NO_PLATFORM", r)

# 4. checked BEFORE this run started -> red. mtime stays fresh on purpose, so the
#    only thing that can redden this case is the checked_at comparison itself.
r = good_rec(); r["checked_at"] = "2026-09-26T22:18:43+08:00"
run("OLD_CHECKED_AT", r)

# 5. non-zero exit with a good artifact -> red (the exit code is a verdict)
run("RC_NONZERO", good_rec(), exit_code=1)

# 5b. exit_code None is NOT "no opinion". Measured on 0bb240b: a launch failure
#     ({"exit_code": None, "detail": "launch failed: [WinError 2] ..."}) came back
#     PASS, because the old predicate was "rc is not None and rc != 0". None is
#     what run_step writes on timeout, launch failure and an exit without status,
#     i.e. exactly the cases where the step did not do its job.
#
#     The detail must also SURVIVE: the old code overwrote the reason with the
#     judge's "PASS: ..." sentence, so the one line that explained the failure was
#     destroyed by the line that asserted success.
run("RC_NONE_LAUNCH_FAILED", good_rec(), exit_code=None,
    detail="launch failed: [WinError 2] python not found")

# 5c. when the JUDGE is what refuses, the earlier reason must survive too. This is
#     the case that separates "kept the reason by accident, because a gate ran
#     first" from "kept the reason on every refusal".
r = good_rec(); r["release_sha"] = "b" * 40
run("JUDGE_FAIL_KEEPS_DETAIL", r, detail="operator note: candidate was bbbb..., not aaaa...")

# 6. no artifact at all -> red
run("NO_FILE", None, write=False)

# 7. fresh-looking content, but the file predates the step's start -> red
run("STALE_MTIME", good_rec(), mtime_shift=-7200)

# 8. the exemption list must stay empty; an unargued exemption is the bug itself
print("CASE\tALLOWLIST_EMPTY\t%s\t%s" % (
    "PASS" if m.EXIT_CODE_ALLOWLIST == () else "FAIL",
    "EXIT_CODE_ALLOWLIST=%r" % (m.EXIT_CODE_ALLOWLIST,)))

# 9. the FINAL-10 step must not be judged by _ok_judge again
step = [s for s in m.STEPS if s.id == "FINAL-10_REBOOT_SIM"][0]
print("CASE\tSTEP_USES_FINAL10_JUDGE\t%s\t%s" % (
    "PASS" if step.judge is m._final10_judge else "FAIL",
    "judge=%s" % getattr(step.judge, "__name__", step.judge)))

# 10. AN ABSENT exit_code IS NOT "not applicable" (aegis, P1).
#     The previous predicate was "exit_code" in record and rc != 0, so not
#     writing the field switched GATE 1 off. The field is absent on exactly the
#     three paths where the exit code is unknown; "unknown" must refuse.
#     The prior detail is supplied so this case also covers the CARRY rule on this
#     branch: a refusal that throws away the reason it was refusing about is a
#     refusal nobody can act on.
run("GATE_1_RC_ABSENT", good_rec(), omit_exit_code=True,
    detail="launch failed: [WinError 2] python not found")

# 11. "I cannot tell when this run started" is not "the file is fine".
#     Both unknown-input branches of the freshness check used to return True.
run("FRESHNESS_SINCE_MISSING", good_rec(), since=None)
run("FRESHNESS_SINCE_UNPARSABLE", good_rec(), since="not-a-date")

# 12. the exemption list must stay empty too -- an exemption is a step judged on
#     its artifact alone, and each one would need its own argument.
print("CASE\tEXEMPT_EMPTY\t%s\t%s" % (
    "PASS" if m.EXIT_CODE_EXEMPT == () else "FAIL",
    "EXIT_CODE_EXEMPT=%r" % (m.EXIT_CODE_EXEMPT,)))

# 13. THE ATTACH PATH, driven through the real attach_to_run.
#     This is where both gates were simultaneously absent: an attach record has no
#     exit code of its own, and a descriptor written by a controller that died
#     mid-run has none either. Three cases: a knowable exit code still passes, an
#     unknowable one refuses, and a descriptor with no start time refuses.
attach("ATTACH_RC_IN_DESCRIPTOR",
       {"run_id": "r1", "pid": -1, "started_at": RUN_START, "exit_code": 0,
        "status": "FINISHED"})
attach("ATTACH_RC_ABSENT",
       {"run_id": "r2", "pid": -1, "started_at": RUN_START, "status": "RUNNING"})
attach("ATTACH_NO_STARTED_AT",
       {"run_id": "r3", "pid": -1, "exit_code": 0, "status": "FINISHED"})
`;

function findPython() {
  for (const candidate of PY_CANDIDATES) {
    const probe = spawnSync(candidate, ["-c", "import sys;print(sys.version_info[0])"], {
      encoding: "utf8",
      timeout: 30000,
    });
    if (!probe.error && probe.status === 0 && probe.stdout.trim() === "3") return candidate;
  }
  return null;
}

const python = findPython();
if (!python) {
  console.log("SKIPPED: no python3 interpreter available, so the FINAL-10 verdict");
  console.log("         rules (Python, tools/jcc_continuation.py) were NOT exercised.");
  console.log("         This is a failure, not a pass: a gate that cannot check must");
  console.log("         not print the same line as a gate that checked and was fine.");
  console.log("RESULT PASS=0 FAIL=1");
  process.exit(1);
}

const child = spawnSync(python, ["-"], {
  input: PROBE,
  encoding: "utf8",
  timeout: 120000,
  env: { ...process.env, JCC_REPO: REPO, PYTHONIOENCODING: "utf-8" },
});

if (child.error || child.status !== 0) {
  // NOT an early exit. Falling through to the enumeration below turns a dead probe
  // into one NAMED failure per case instead of a single anonymous one -- "the gate
  // broke at import" used to redden nothing by name, which made a fail-closed
  // mutation (e.g. the empty-allowlist `raise`) indistinguishable from a red test.
  console.log("SKIPPED: the python probe did not complete (status=%s error=%s)",
    child.status, child.error ? child.error.message : "none");
  if (child.stderr) console.log(child.stderr.trim().split("\n").slice(-8).join("\n"));
  console.log("         Nothing below was exercised. Every case is reported as a");
  console.log("         named failure rather than one anonymous one.");
}

// name -> the status that must come back. PASS cases must say PASS; the rest must
// NOT say PASS. Asserting "not PASS" rather than "FAIL" keeps this test about the
// rule (never accept) instead of about the wording of the refusal.
const EXPECT = new Map([
  ["GOOD", "PASS"],
  ["GOOD_DETAIL_IS_JUDGE_TEXT", "PASS"],
  ["OLD_SHA", "FAIL"],
  ["NO_PLATFORM", "FAIL"],
  ["OLD_CHECKED_AT", "FAIL"],
  ["RC_NONZERO", "FAIL"],
  ["RC_NONE_LAUNCH_FAILED", "FAIL"],
  ["JUDGE_FAIL_KEEPS_DETAIL", "FAIL"],
  ["NO_FILE", "FAIL"],
  ["STALE_MTIME", "FAIL"],
  ["ALLOWLIST_EMPTY", "PASS"],
  ["STEP_USES_FINAL10_JUDGE", "PASS"],
  ["OUT_DELETED_BEFORE_WRITE", "PASS"],
  ["GATE_1_RC_ABSENT", "FAIL"],
  ["FRESHNESS_SINCE_MISSING", "FAIL"],
  ["FRESHNESS_SINCE_UNPARSABLE", "FAIL"],
  ["EXEMPT_EMPTY", "PASS"],
  ["ATTACH_RC_IN_DESCRIPTOR", "PASS"],
  ["ATTACH_RC_ABSENT", "FAIL"],
  ["ATTACH_NO_STARTED_AT", "FAIL"],
]);

// Some cases must also keep a piece of text alive. "The status is right" and "the
// reason survived" are two different claims; the second one is what makes the
// first one usable by whoever reads the record next. A list, because a refusal
// that names both the branch and the cause is worth more than one that names one.
const MUST_MENTION = new Map([
  ["RC_NONE_LAUNCH_FAILED", ["launch failed"]],
  ["JUDGE_FAIL_KEEPS_DETAIL", ["operator note"]],
  // The token must be one that the gate's OWN message cannot contain, or the
  // assertion "the reason survived" is satisfied by the refusal text itself.
  // "launch failed" would already be present here ("(launch failed, timeout, ...)"),
  // so this case uses the error text instead.
  ["GATE_1_RC_ABSENT", ["EXIT_CODE_UNKNOWN", "WinError 2"]],
  ["FRESHNESS_SINCE_MISSING", ["FRESHNESS_SINCE_MISSING"]],
  ["FRESHNESS_SINCE_UNPARSABLE", ["FRESHNESS_SINCE_UNPARSABLE"]],
  ["ATTACH_RC_ABSENT", ["EXIT_CODE_UNKNOWN"]],
  ["ATTACH_NO_STARTED_AT", ["RUN_LOST"]],
]);

let PASS = 0;
let FAIL = 0;
const seen = new Set();

/**
 * 10. The producer must delete its artifact before writing it. The judge can only
 * refuse a stale file if "no file" is a reachable state, and this is the only thing
 * that makes it reachable when the script never runs.
 *
 * LIMIT, stated so it is not over-read: this is a text-order check. A rewrite that
 * deletes the file through another API (`shutil`, a context manager, `unlink`) would
 * pass without being guarded, exactly like the name-scan limits noted for Y9. It
 * catches the specific regression it was written for — dropping the guard — and
 * nothing more.
 *
 * RETIREMENT CONDITION (same family as Y9's "name scanning cannot see indirect
 * calls"): the day `reboot_recovery_sim.py` deletes its output via `unlink`,
 * `shutil`, or a context manager, this assertion stops meaning anything while
 * still passing. Whoever makes that change must replace this case with one that
 * observes the deletion (e.g. plant the file, run the producer, assert it is gone
 * before the write) rather than deleting the case.
 */
{
  const simPath = resolve(REPO, "tools", "continuation", "reboot_recovery_sim.py");
  const simSrc = readFileSync(simPath, "utf8");
  const rmAt = simSrc.indexOf("os.remove(OUT)");
  const writeAt = simSrc.indexOf('io.open(OUT, "w"');
  const ok = rmAt >= 0 && writeAt >= 0 && rmAt < writeAt;
  console.log(
    `${ok ? "PASS" : "FAIL"}  OUT_DELETED_BEFORE_WRITE — ${ok ? "PASS" : "FAIL"}` +
      ` · remove_at=${rmAt} write_at=${writeAt}`
  );
  if (ok) PASS += 1;
  else FAIL += 1;
  seen.add("OUT_DELETED_BEFORE_WRITE");
}

for (const line of child.stdout.split("\n")) {
  if (!line.startsWith("CASE\t")) continue;
  const [, name, status, detail] = line.split("\t");
  seen.add(name);
  const want = EXPECT.get(name);
  if (want === undefined) {
    FAIL += 1;
    console.log(`FAIL  ${name} — the probe emitted a case this test does not expect`);
    continue;
  }
  const ok = want === "PASS" ? status === "PASS" : status !== "PASS";
  const mustMention = MUST_MENTION.get(name) || [];
  const missing = mustMention.filter((m) => !String(detail || "").includes(m));
  const mentionOk = missing.length === 0;
  if (ok && mentionOk) {
    PASS += 1;
    console.log(`PASS  ${name} — ${status}${detail ? " · " + detail : ""}`);
  } else if (ok && !mentionOk) {
    FAIL += 1;
    console.log(`FAIL  ${name} — status was right but the reason was erased; detail must contain ${JSON.stringify(missing)}, got: ${detail || "(empty)"}`);
  } else {
    FAIL += 1;
    console.log(`FAIL  ${name} — wanted ${want === "PASS" ? "PASS" : "not PASS"}, got ${status}${detail ? " · " + detail : ""}`);
  }
}

for (const name of EXPECT.keys()) {
  if (!seen.has(name)) {
    FAIL += 1;
    console.log(`FAIL  ${name} — the probe never reported this case (silence is not a pass)`);
  }
}

console.log(`RESULT PASS=${PASS} FAIL=${FAIL}`);
process.exit(FAIL === 0 ? 0 : 1);
