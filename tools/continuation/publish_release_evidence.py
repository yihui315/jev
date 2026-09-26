#!/usr/bin/env python3
"""FINAL-13 -- publish the release evidence, with provenance a reader can recompute.

WHAT THIS IS FOR. `JEV_PROJECT_RUN=PASS` is a claim about a tree. A claim about a
tree is only checkable if every byte it rests on is named by its hash and every
hash can be recomputed from the repository. That is all this script does: it
re-derives the verdicts from disk, hashes every artifact the verdict rests on,
re-takes the `scripts/` freeze, and writes the result to
`artifacts/release/final-manifest.json`.

WHAT IT DELIBERATELY DOES NOT DO.
  * It does NOT re-run the legs. Re-running them here would mean the published
    verdict describes a *different* run than the one whose artifacts are being
    published, which is precisely the confusion this manifest exists to remove.
    It re-JUDGES them, with the same judge functions the controller used.
  * It does NOT trust `final_gate_result.json` as evidence that F-03 was
    checked. The gate's F-03 accepts the manifest on presence alone -- its own
    detail string says "regeneration check deferred to the release step" -- so
    this script performs that check (`--verify`) and reports the result itself
    rather than letting a lenient check be read as a satisfied one.
  * It does NOT create a tag, push, or publish anywhere. EXTERNAL_PUBLISH stays 0;
    "publish" here means "write the release record into the repository".

WHY THE FREEZE IS RE-TAKEN RATHER THAN REUSED, AND WHY THE OLD ONE IS KEPT.
`artifacts/release/freeze_manifest.json` recorded `scripts/` as it stood at commit
cdbb3ca7 with `scripts/lib/JccCommon.ps1` = 17016a3d…. That file has since been
repaired (the ownership marker was written with a BOM, which made the soak's
reader report a correct marker as absent -- a false negative in the FINAL-08
evidence). A freeze whose bytes no longer exist is not a freeze, so the freeze is
re-taken here and the previous one is MOVED to
`artifacts/release/freeze_manifest.previous.json` -- kept, not deleted -- and the
old -> new hash is recorded explicitly in both files. This is the same obligation
the A8 hash pin carries: changing a pinned artifact without re-baselining it
silently turns the pin into a false statement.

Usage
-----
    python tools/continuation/publish_release_evidence.py            # write
    python tools/continuation/publish_release_evidence.py --verify   # re-derive and compare
    python tools/continuation/publish_release_evidence.py --selftest

Exit 0 iff the published verdict is PASS (write) or the on-disk manifest agrees
with a fresh derivation (verify).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Tuple

ROOT = r"F:\jev"
ACC = os.path.join(ROOT, "artifacts", "acceptance")
REL = os.path.join(ROOT, "artifacts", "release")
MANIFEST = os.path.join(REL, "final-manifest.json")
MANIFEST_SHA = os.path.join(REL, "final-manifest.sha256")
FREEZE = os.path.join(REL, "freeze_manifest.json")
FREEZE_PREV = os.path.join(REL, "freeze_manifest.previous.json")
SCHEMA = "jcc/final-manifest/2"

sys.path.insert(0, os.path.join(ROOT, "artifacts", "acceptance"))
sys.path.insert(0, os.path.join(ROOT, "tools", "continuation"))

#: Everything the FINAL-phase verdict rests on, and nothing else. Explicitly
#: listed: a computed "everything in artifacts/" would silently grow when a stray
#: file appears, and this list is a claim about what the verdict is made of.
EVIDENCE: Tuple[Tuple[str, str], ...] = (
    ("FINAL-01_CONTROLLER", "artifacts/acceptance/CONTINUATION_INFRA_TEST.json"),
    ("FINAL-05_DETACHED", "artifacts/acceptance/DETACHED_SURVIVAL_TEST.json"),
    ("FINAL-07_SURFACES", "artifacts/acceptance/SURFACE_EVIDENCE.json"),
    ("FINAL-07_SURFACES", "artifacts/acceptance/control_ui_smoke.json"),
    ("FINAL-07_SURFACES", "artifacts/acceptance/demo_e2e.json"),
    ("FINAL-08_SOAK", "artifacts/acceptance/JCC_SOAK_RESULT.json"),
    ("FINAL-08_SOAK", "artifacts/acceptance/SOAK_WATCHDOG_PROOF.json"),
    ("FINAL-09_RECOVERY", "artifacts/acceptance/WATCHDOG_RECOVERY_PROOF.json"),
    ("FINAL-10_REBOOT_SIM", "artifacts/acceptance/REBOOT_RECOVERY.json"),
    ("FINAL-11_FINAL_GATE", "artifacts/acceptance/final_gate_result.json"),
    ("FINAL-11_FINAL_GATE", "artifacts/acceptance/final_gate_run.py"),
    ("FINAL-12_PROJECT_RUN", "artifacts/acceptance/project_run_gate_result.json"),
    ("E-05", "artifacts/acceptance/final_architecture_go.json"),
    ("E-06", "artifacts/acceptance/final_security_go.json"),
    ("E-07", "artifacts/acceptance/final_sre_go.json"),
    ("stop-condition", "docs/FINAL_STOP_CONDITIONS.md"),
    ("controller", "tools/jcc_continuation.py"),
    ("watchdog", "scripts/continuation-watchdog.ps1"),
    ("watchdog", "scripts/lib/JccWatchdogCommon.ps1"),
    ("watchdog", "scripts/service-watchdog.ps1"),
    ("watchdog", "scripts/lib/JccCommon.ps1"),
)

#: Produced and consumed by the run, but NOT in version control, because they
#: record moments rather than identity (same rule as gate_runs.jsonl). Named here
#: so that "the trace is missing from the manifest" cannot be read as "there was
#: no trace".
NOT_IN_VERSION_CONTROL: Tuple[str, ...] = (
    "artifacts/acceptance/JCC_SOAK_TRACE.jsonl",
    "artifacts/acceptance/gate_runs.jsonl",
)


def log(msg: str) -> None:
    sys.stdout.write("%s\n" % msg)
    sys.stdout.flush()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: str) -> Any:
    try:
        with io.open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def atomic_write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with io.open(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def git(*args: str) -> str:
    r = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=300)
    return (r.stdout or "").strip()


def tracked() -> set:
    out = git("ls-files")
    return {p for p in out.splitlines() if p.strip()}


# --------------------------------------------------------------------------- #
# The two derivations
# --------------------------------------------------------------------------- #

def derive_freeze() -> Dict[str, Any]:
    """Re-take the `scripts/` freeze. Every .ps1/.psm1/.py under scripts/, by hash."""
    hashes: Dict[str, str] = {}
    for root, dirs, files in os.walk(os.path.join(ROOT, "scripts")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.lower().endswith((".ps1", ".psm1", ".py")):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
            hashes[rel] = sha256_file(full)
    return hashes


def freeze_rebaseline(new: Dict[str, str]) -> Dict[str, Any]:
    """Compare against the freeze in the repository, naming every drift explicitly."""
    old_doc = load_json(FREEZE) or {}
    old = old_doc.get("hashes") or {}
    changed = {k: {"old": old[k], "new": new[k]}
               for k in sorted(set(old) & set(new)) if old[k] != new[k]}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    return {
        "previous_freeze": {
            "path": "artifacts/release/freeze_manifest.json",
            "taken_at": old_doc.get("taken_at"),
            "head_sha": old_doc.get("head_sha"),
            "file_count": len(old),
        },
        "changed": changed,
        "added": {k: new[k] for k in added},
        "removed": {k: old[k] for k in removed},
        "is_a_re_baseline": bool(changed or added or removed),
    }


def derive_project_run() -> Dict[str, Any]:
    """Re-derive `JEV_PROJECT_RUN` from disk with FINAL-12's own judge."""
    import importlib.util
    p = os.path.join(ACC, "project_run_gate.py")
    spec = importlib.util.spec_from_file_location("jcc_project_run_gate", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    payload = mod.collect()
    ok, problems = mod.judge(payload["legs"])
    return {
        "ok": bool(ok and payload["ok"]),
        "problems": list(problems) + list(payload["problems"]),
        "legs": payload["legs"],
        "judge": "artifacts/acceptance/project_run_gate.py (imported, not re-implemented)",
        "pending_after_this_step": payload["pending_after_this_step"],
    }


def derive_pipeline_state() -> Dict[str, Any]:
    """What the controller recorded for every step IT owns.

    Deliberately STABLE: it reports the ids that are not yet PASS *excluding this
    step*, and the total, and it does not dump raw per-step statuses. At publish
    time FINAL-13 is running and at verify time it has finished, so a raw dump
    would make --verify disagree with itself on every run -- and a check that can
    never pass is not a check.
    """
    import importlib.util
    p = os.path.join(ROOT, "tools", "jcc_continuation.py")
    spec = importlib.util.spec_from_file_location("jcc_cont_for_manifest", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    state = load_json(os.path.join(ROOT, ".workbuddy", "jcc_execution_state.json")) or {}
    steps = state.get("steps") or {}
    this_step = "FINAL-13_PUBLISH"
    not_pass = [sid for sid in mod.STEP_IDS
                if sid != this_step and (steps.get(sid) or {}).get("status") != "PASS"]
    return {
        "steps_total": len(mod.STEP_IDS),
        "steps_that_are_not_pass_excluding_this_step": not_pass,
        "this_step": this_step,
        "state_file": ".workbuddy/jcc_execution_state.json",
        "note": ("the state file is NOT in version control, so this is a record of the "
                 "run rather than a claim a clone can re-derive. Every leg's own "
                 "artifact is hashed above, and that is the part a clone re-derives; "
                 "the controller's completion is recorded here because FINAL-12's gate "
                 "stops at FINAL-11 by construction"),
    }


def derive_evidence() -> Tuple[List[Dict[str, Any]], List[str]]:
    tr = tracked()
    rows: List[Dict[str, Any]] = []
    problems: List[str] = []
    for owner, rel in EVIDENCE:
        full = os.path.join(ROOT, *rel.split("/"))
        if not os.path.isfile(full):
            problems.append("%s is in the manifest but not on disk" % rel)
            continue
        rows.append({
            "owner": owner,
            "path": rel,
            "sha256": sha256_file(full),
            "bytes": os.path.getsize(full),
            "tracked": rel in tr,
        })
    for rel in NOT_IN_VERSION_CONTROL:
        full = os.path.join(ROOT, *rel.split("/"))
        rows.append({
            "owner": "run-record",
            "path": rel,
            "sha256": sha256_file(full) if os.path.isfile(full) else None,
            "bytes": os.path.getsize(full) if os.path.isfile(full) else 0,
            "tracked": False,
            "note": ("records a MOMENT, not identity -- deliberately not in version "
                     "control; absent from a clone by design"),
        })
    return rows, problems


def gate_summary() -> Dict[str, Any]:
    g = load_json(os.path.join(ACC, "final_gate_result.json")) or {}
    return {
        "project_complete": g.get("project_complete"),
        "blocker": g.get("blocker"),
        "high": g.get("high"),
        "missing": sorted(g.get("missing") or []),
        "failed": sorted(g.get("failed") or []),
        "degraded": sorted(g.get("degraded") or []),
        "check_count": len(g.get("checks") or []),
        "note": ("degraded outside A-01..A-04 would have failed the gate; read the gate's "
                 "own result file for the per-check detail"),
    }


def derive() -> Dict[str, Any]:
    """Everything except the clock. `--verify` compares exactly this."""
    freeze = derive_freeze()
    rebase = freeze_rebaseline(freeze)
    rows, problems = derive_evidence()
    pr = derive_project_run()
    gate = gate_summary()

    if gate["project_complete"] is not True:
        problems.append("the final gate does not report project_complete=true: %r"
                        % (gate["project_complete"],))
    if gate["blocker"] not in (0, None):
        problems.append("the final gate reports blocker=%r" % (gate["blocker"],))
    if not pr["ok"]:
        problems.append("the project-run judge refuses the legs: %s"
                        % "; ".join(pr["problems"])[:400])
    pipe = derive_pipeline_state()
    if pipe["steps_that_are_not_pass_excluding_this_step"]:
        problems.append("the controller has not recorded PASS for: %s"
                        % ", ".join(pipe["steps_that_are_not_pass_excluding_this_step"]))
    if not rows:
        problems.append("no evidence row was produced at all")

    ok = not problems
    return {
        "schema": SCHEMA,
        "ok": ok,
        "JEV_PROJECT_RUN": "PASS" if ok else "FAIL",
        "verdict": ("PASS: every FINAL-phase leg is present and unchanged since it was "
                    "judged, the final gate reports PROJECT_COMPLETE with blocker=0, and "
                    "the scripts freeze was re-taken with every drift named"
                    if ok else "FAIL: " + "; ".join(problems)),
        "hash_convention": "sha256(raw bytes) for every file entry",
        "evidence": rows,
        "evidence_problems": problems,
        "project_run": pr,
        "pipeline": pipe,
        "final_gate": gate,
        "scripts_freeze": {
            "hashes": freeze,
            "file_count": len(freeze),
            "rebaseline": rebase,
            "previous_freeze_kept_at": ("artifacts/release/freeze_manifest.previous.json"
                                        if rebase["is_a_re_baseline"] else None),
        },
        "does_not_prove": [
            "that jcc_core is restarted after a reboot. No component in this repository "
            "restarts the service if it dies: scripts/service-watchdog.ps1 monitors the "
            "release state and the port, and the operator starts the service. Recorded in "
            "artifacts/acceptance/REBOOT_RECOVERY.json and in "
            "artifacts/release/PROJECT_RUN_READINESS.md, not papered over here.",
            "that F-03's regeneration requirement is checked by the gate. The gate accepts "
            "this manifest on presence alone and says so in its own detail string; the "
            "regeneration check is `--verify` in this script, and its result is recorded "
            "under `regeneration_check` below.",
            "that this tool verified the scheduled task JEV-JCC-Continuation-Watchdog through "
            "the scheduler API. The task IS registered on this host and fires every 2 minutes "
            "-- its own on-disk definition is Enabled=true with a PT2M repetition under "
            "principal S-1-5-18, and the watchdog's tick log gains exactly one line every "
            "120 s -- but the agent sandbox cannot enumerate Task Scheduler through CIM "
            "(Get-ScheduledTask fails there), so registration rests on the task's own "
            "definition plus its tick interval, not on a live query from here. This entry "
            "previously claimed the task was NOT registered, which repeated the sandbox's "
            "silence as a fact about the host.",
        ],
    }


# --------------------------------------------------------------------------- #
# Publishing
# --------------------------------------------------------------------------- #

def publish() -> int:
    payload = derive()
    payload["published_at"] = _now()
    payload["published_by"] = "tools/continuation/publish_release_evidence.py"
    payload["published_by_sha256"] = sha256_file(os.path.abspath(__file__))
    payload["regeneration_check"] = {
        "how": "run this script with --verify; it re-derives the payload and compares "
               "every substantive field, then recomputes every listed hash",
        "result": "PENDING -- not yet re-derived",
    }

    # Re-take the freeze, KEEPING the previous record.
    if os.path.isfile(FREEZE):
        with io.open(FREEZE, "rb") as fh:
            prev_bytes = fh.read()
        fd, tmp = tempfile.mkstemp(dir=REL, suffix=".tmp")
        with io.open(fd, "wb") as fh:
            fh.write(prev_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, FREEZE_PREV)
    payload_freeze = {
        "schema": "jcc/scripts-freeze/2",
        "taken_at": _now(),
        "head_sha": git("rev-parse", "HEAD"),
        "file_count": payload["scripts_freeze"]["file_count"],
        "hashes": payload["scripts_freeze"]["hashes"],
        "supersedes": payload["scripts_freeze"]["rebaseline"]["previous_freeze"],
        "rebaseline": {k: v for k, v in payload["scripts_freeze"]["rebaseline"].items()
                       if k != "previous_freeze"},
        "why": ("re-taken because scripts/lib/JccCommon.ps1 changed after the previous "
                "freeze was taken; the previous manifest is kept at "
                "freeze_manifest.previous.json and the old -> new hash is in `rebaseline`"),
    }
    atomic_write_json(FREEZE, payload_freeze)

    atomic_write_json(MANIFEST, payload)
    digest = sha256_file(MANIFEST)
    with io.open(MANIFEST_SHA, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("%s  final-manifest.json\n" % digest)

    for p in payload["evidence_problems"]:
        log("  PROBLEM %s" % p)
    log("JEV_PROJECT_RUN=%s -> %s" % (payload["JEV_PROJECT_RUN"], MANIFEST))
    log("  evidence rows: %d (%d not in version control by design)"
        % (len(payload["evidence"]),
           sum(1 for r in payload["evidence"] if not r["tracked"])))
    log("  scripts freeze: %d file(s), re-baseline=%s"
        % (payload["scripts_freeze"]["file_count"],
           payload["scripts_freeze"]["rebaseline"]["is_a_re_baseline"]))
    return 0 if payload["ok"] else 1


def _now() -> str:
    import datetime
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


#: Fields that describe the MOMENT of publication rather than the tree, and are
#: therefore not substantive. Everything else is compared field by field.
#:
#:   * ``published_at`` -- the wall clock.
#:   * ``scripts_freeze.rebaseline`` / ``previous_freeze_kept_at`` -- they answer
#:     "did ``scripts/`` differ from the freeze that was committed WHEN THIS RAN?",
#:     which is a statement about the transition, not about the tree. Measured:
#:     ``publish()`` calls ``derive()``, whose ``freeze_rebaseline()`` compares the
#:     current tree against the ALREADY-COMMITTED freeze and records ``True``; only
#:     then does ``publish()`` write the new freeze. ``verify()`` re-runs the same
#:     ``derive()``, by which time the committed freeze IS the new one, so the same
#:     question now answers ``False``. Comparing it made ``--verify`` unable to
#:     exit 0 after a publish -- and a check that can never pass is not a check.
#:
#: The SUBSTANCE of the freeze -- ``scripts_freeze.hashes`` and
#: ``scripts_freeze.file_count`` -- is still compared field by field, and every
#: listed hash is still recomputed from disk.
_MOMENT_TOP_LEVEL = frozenset({"published_at"})
_MOMENT_SUBFIELDS = {"scripts_freeze": frozenset({"rebaseline", "previous_freeze_kept_at"})}


def _substantive_field_diffs(on_disk: Dict[str, Any], fresh: Dict[str, Any]) -> List[str]:
    """Names of substantive fields that differ between the two derivations.

    Extracted from ``_verify_rules`` so ``--selftest`` and ``--verify`` run the
    SAME rule, and so the moment-only exemption is one named set rather than a
    special case buried in a loop.
    """
    problems: List[str] = []
    for k in sorted(fresh):
        if k in _MOMENT_TOP_LEVEL:
            continue
        skip = _MOMENT_SUBFIELDS.get(k)
        if skip and isinstance(fresh[k], dict):
            a = {kk: vv for kk, vv in fresh[k].items() if kk not in skip}
            b = {kk: vv for kk, vv in (on_disk.get(k) or {}).items() if kk not in skip}
            if a != b:
                problems.append(
                    "substantive field %r differs between the published manifest and "
                    "today's derivation (moment-only subfield(s) %s excluded)"
                    % (k, sorted(skip)))
            continue
        if fresh[k] != on_disk.get(k):
            problems.append("substantive field %r differs between the published manifest "
                            "and today's derivation" % k)
    return problems


def _verify_rules(on_disk: Dict[str, Any], fresh: Dict[str, Any],
                  disk_hashes: Dict[str, str], manifest_sha_file: str | None,
                  manifest_sha_actual: str | None) -> Tuple[List[str], List[str]]:
    """The regeneration rules, extracted so ``--selftest`` falsifies the SAME code
    that ``--verify`` runs. Inlining them would have made the selftest a second
    implementation of the rule it claims to check.

    ``disk_hashes`` maps a manifest path to the sha256 computed from disk now, or
    to None when the file is absent.
    """
    problems: List[str] = _substantive_field_diffs(on_disk, fresh)
    hash_problems: List[str] = []

    for row in on_disk.get("evidence") or []:
        rel = row.get("path")
        got = disk_hashes.get(rel)
        if got is None:
            if row.get("tracked"):
                hash_problems.append("%s is listed as tracked but is not on disk" % rel)
            continue
        if row.get("sha256") != got:
            hash_problems.append("%s: manifest says %s, disk says %s"
                                 % (rel, str(row.get("sha256"))[:16], got[:16]))

    for rel, want in (on_disk.get("scripts_freeze") or {}).get("hashes", {}).items():
        got = disk_hashes.get(rel)
        if got is None:
            hash_problems.append("frozen script %s is gone" % rel)
        elif got != want:
            hash_problems.append("frozen script %s changed" % rel)

    if manifest_sha_file and manifest_sha_file != manifest_sha_actual:
        hash_problems.append("final-manifest.sha256 says %s but the manifest hashes to %s"
                             % (manifest_sha_file[:16], str(manifest_sha_actual)[:16]))
    return problems, hash_problems


def verify() -> int:
    """The F-03 obligation the gate defers: today's derivation vs the published one."""
    on_disk = load_json(MANIFEST)
    if on_disk is None:
        log("VERIFY FAIL: %s is missing or unparsable" % MANIFEST)
        return 1
    fresh = derive()

    # Every path the manifest names, hashed from disk once.
    wanted = [row.get("path") for row in (on_disk.get("evidence") or [])]
    wanted += list((on_disk.get("scripts_freeze") or {}).get("hashes", {}))
    disk_hashes: Dict[str, Any] = {}
    for rel in wanted:
        full = os.path.join(ROOT, *str(rel).split("/"))
        disk_hashes[rel] = sha256_file(full) if os.path.isfile(full) else None

    recorded = None
    if os.path.isfile(MANIFEST_SHA):
        with io.open(MANIFEST_SHA, "r", encoding="utf-8") as fh:
            parts = fh.read().split()
        recorded = parts[0] if parts else None

    problems, hash_problems = _verify_rules(on_disk, fresh, disk_hashes, recorded,
                                           sha256_file(MANIFEST))
    ok = not problems and not hash_problems and fresh["ok"]
    for p in problems:
        log("  DIFF %s" % p)
    for p in hash_problems:
        log("  HASH %s" % p)
    log("regeneration check: %s (%d substantive field diff(s), %d hash problem(s))"
        % ("PASS" if ok else "FAIL", len(problems), len(hash_problems)))

    if ok:
        # Record the fact that the check ran, and that it passed. Written back into
        # the manifest, so the publication record says who checked it and how -- and
        # a reader can see it is a self-check, not an independent one.
        on_disk["regeneration_check"] = {
            "how": "tools/continuation/publish_release_evidence.py --verify",
            "result": "PASS on %s: every substantive field matches a fresh derivation and "
                      "every listed hash recomputes" % _now(),
            "independence": ("SELF-CHECK. It is the publisher verifying the publisher. "
                            "FINAL-12 re-derives the legs with the controller's judges "
                            "independently of this script, and that is the cross-check "
                            "this one is not."),
        }
        atomic_write_json(MANIFEST, on_disk)
        digest = sha256_file(MANIFEST)
        with io.open(MANIFEST_SHA, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("%s  final-manifest.json\n" % digest)
        log("  recorded the check into the manifest; final-manifest.sha256 -> %s"
            % digest[:16])
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #

def selftest() -> int:
    """Prove this step can refuse. Every case mutates a COPY of a real payload."""
    cases: List[Tuple[str, bool, str]] = []

    def case(name: str, ok: bool, detail: str = "") -> None:
        cases.append((name, ok, detail))

    # fresh and on-disk are built from ONE base, so the only difference between
    # them is the one the case injects. A fixture that differs in shape cannot
    # show that a clean pair is ever clean, and the green direction is the one
    # that has to work when the real run has to come out green.
    def check(disk_hashes, fresh_ok=True, on_disk_ok=True, sha_file="f" * 64,
              manifest_map=None, fresh_map=None):
        """Run the REAL rules against a synthetic pair of payloads."""
        base = {"schema": SCHEMA,
                "evidence": [{"path": "a.json", "sha256": "x", "tracked": True}],
                "scripts_freeze": {"hashes": {"s.ps1": "aa"}}}
        on_disk = dict(base)
        on_disk["ok"] = on_disk_ok
        fresh = dict(base)
        fresh["ok"] = fresh_ok
        on_disk.update(manifest_map or {})
        fresh.update(fresh_map or {})
        hashes = {"a.json": "x", "s.ps1": "aa"}
        hashes.update(disk_hashes or {})
        problems, hash_problems = _verify_rules(on_disk, fresh, hashes, sha_file, "f" * 64)
        return problems + hash_problems

    case("an_identical_manifest_verifies", check({}) == [], "should be clean")

    case("a_flipped_verdict_is_caught",
         any("ok" in x for x in check({}, fresh_ok=False)), "")

    case("an_on_disk_verdict_edited_to_red_is_caught",
         any("ok" in x for x in check({}, on_disk_ok=False)), "")

    # The field diff is what catches a manifest whose verdict was edited without
    # the artifacts changing -- the "hand-edited JSON to green" case.
    case("an_edited_field_is_caught",
         any("substantive field" in x for x in
             check({}, fresh_map={"verdict": "PASS"})), "")

    case("a_stale_hash_is_caught",
         any("disk says" in x for x in check({"a.json": "y"})), "")

    case("a_deleted_tracked_artifact_is_caught",
         any("not on disk" in x for x in check({"a.json": None})), "")

    case("a_wrong_manifest_sha_is_caught",
         any("sha256 says" in x for x in check({}, sha_file="e" * 64)), "")

    case("a_frozen_script_that_changed_is_caught",
         any("frozen script" in x for x in check({"s.ps1": "bb"})), "")

    case("a_frozen_script_that_is_gone_is_caught",
         any("frozen script" in x and "gone" in x for x in check({"s.ps1": None})), "")

    case("a_clean_manifest_is_not_reported_dirty", check({}) == [], "")

    # The moment-only exemption, i.e. the measured FINAL-13 defect: publish() calls
    # derive() -> freeze_rebaseline() against the ALREADY-COMMITTED freeze and gets
    # True, then overwrites the freeze; verify() re-derives and gets False. That
    # transition is provenance about the moment, not about the tree -- but the
    # SUBSTANCE of the freeze must still be compared field by field.
    publish_freeze = {
        "hashes": {"s.ps1": "aa"}, "file_count": 1,
        "rebaseline": {"is_a_re_baseline": True, "changed": {"s.ps1": {"old": "00", "new": "aa"}}},
        "previous_freeze_kept_at": "artifacts/release/freeze_manifest.previous.json",
    }
    verify_freeze = {
        "hashes": {"s.ps1": "aa"}, "file_count": 1,
        "rebaseline": {"is_a_re_baseline": False, "changed": {}},
        "previous_freeze_kept_at": None,
    }
    case("publish_then_verify_is_not_a_substantive_diff",
         check({}, manifest_map={"scripts_freeze": publish_freeze},
               fresh_map={"scripts_freeze": verify_freeze}) == [],
         "the re-baseline record is provenance about the moment, like published_at")
    case("a_changed_freeze_hash_is_still_a_substantive_diff",
         any("scripts_freeze" in x for x in
             check({}, manifest_map={"scripts_freeze": publish_freeze},
                   fresh_map={"scripts_freeze": dict(verify_freeze,
                                                     hashes={"s.ps1": "cc"})})), "")
    case("a_changed_freeze_file_count_is_still_a_substantive_diff",
         any("scripts_freeze" in x for x in
             check({}, manifest_map={"scripts_freeze": publish_freeze},
                   fresh_map={"scripts_freeze": dict(verify_freeze, file_count=2)})), "")

    for name, passed, detail in cases:
        log("  [%s] %-46s %s" % ("PASS" if passed else "FAIL", name, detail[:100]))
    good = sum(1 for _n, x, _d in cases if x)
    log("publish_release_evidence selftest: %d/%d" % (good, len(cases)))
    return 0 if good == len(cases) else 1


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="publish the FINAL release evidence")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--verify", action="store_true",
                   help="re-derive and compare against the published manifest")
    g.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.verify:
        return verify()
    return publish()


if __name__ == "__main__":
    raise SystemExit(main())
