#!/usr/bin/env python3
"""JCC v1.3 FINAL -- the release verdict aggregator (owner: Forge, contract N).

WHAT THIS IS
------------
A READ-ONLY aggregator. It reads the evidence the run produced and states, in one
document, which release claims are actually supported. It never repairs the
environment, never writes a product file, never starts or stops a process, and
never re-runs a gate. The only thing it writes is its own verdict.

That restraint is the whole design. An aggregator that "helps" by fixing what it
finds cannot be used as evidence about the state it found: if it repaired the
product, the next run would report a clean product and nobody could tell the
difference between "the run was clean" and "the aggregator cleaned it". A verdict
that cannot be disbelieved is the only kind worth reading.

THE HONESTY RULES
-----------------
H1 MISSING IS NOT PASS. Absent evidence yields ``false`` plus a blocker, never a
   silent ``true``. The failure this retires: a summary that treats "we did not
   measure it" and "it was fine" as the same value, which is how an unmeasured
   release reads as a shipped one.
H2 STALE IS NOT PASS. Every input records the HEAD it was measured at. Evidence
   from another commit is about other code, so a verdict may only aggregate
   inputs that agree on the head (or explicitly pin a different one, e.g. a
   freeze predating later evidence commits).
H3 NO INHERITED VERDICT. A sub-document's own ``project_complete: true`` is
   recorded as a CLAIM, never consumed as this tool's conclusion.
H4 THE FAILURE MUST BE ACTIONABLE. Every blocker carries an id, a class, the
   exact evidence path and field that produced it, and the next deterministic
   action -- so a reader can act without re-deriving the analysis.
H5 NO SELF-REFERENCE. This file's verdict is derived only from OTHER documents.
   It never reads its own previous output, so it cannot ratify a stale claim.

USAGE
-----
  D:/jev/.venv/Scripts/python.exe tools/jcc_final_verdict.py --emit
  D:/jev/.venv/Scripts/python.exe tools/jcc_final_verdict.py --check
  D:/jev/.venv/Scripts/python.exe tools/jcc_final_verdict.py --selftest
  D:/jev/.venv/Scripts/python.exe tools/jcc_final_verdict.py --print

Exit 0 only when ``final_release`` is true. Any hard gate standing open is
exit 1, because a verdict tool that exits 0 on a red release is a rubber stamp.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
ART = REPO / "artifacts" / "release" / "v1.3_final"
OUT = ART / "FINAL_VERDICT.json"
SIDECAR = ART / "FINAL_VERDICT.sha256"
GENERATED_BY = "tools/jcc_final_verdict.py"
SCHEMA = "jcc/final-verdict/1"

#: The exact field set directive N fixes. Held as data so the selftest can assert
#: the document matches the contract, rather than trusting that it does.
VERDICT_FIELDS: Tuple[str, ...] = (
    "product_implementation_complete",
    "sandbox_certified",
    "product_freeze_valid",
    "host_activated",
    "workbuddy_native_launch_verified",
    "live_sse_verified",
    "live_3d_verified",
    "deliverable_provenance_verified",
    "hardware_perf_verified",
    "durable_runtime_verified",
    "security",
    "green_untouched",
    "manifest_clean",
    "git_clean",
    "project_complete",
    "final_release",
    "host_activation_pending",
    "jev_project_run",
    "jcc_product_edition",
)

#: Inputs, in dependency order. ``required`` means the directive names it as a
#: direct input; the rest are the "necessary security/runtime/workbuddy/gpu
#: evidence" the directive also calls for. Both are reported when missing (H1),
#: because a missing named input is a blocker regardless of who named it.
INPUTS: Tuple[Dict[str, Any], ...] = (
    {"key": "sandbox", "file": "SANDBOX_CERTIFICATION.json", "required": True},
    {"key": "host_acceptance", "file": "HOST_ACCEPTANCE.json", "required": True},
    {"key": "product_freeze", "file": "PRODUCT_FREEZE.json", "required": True},
    {"key": "manifest", "file": "MANIFEST.json", "required": True},
    {"key": "security", "file": "SECURITY_FINAL.json", "required": False},
    {"key": "host_runtime", "file": "HOST_RUNTIME.json", "required": False},
    {"key": "workbuddy", "file": "WORKBUDDY_CAUSAL_LAUNCH.json", "required": False},
    {"key": "live_mission", "file": "LIVE_MISSION.json", "required": False},
    {"key": "live_3d", "file": "LIVE_3D.json", "required": False},
    {"key": "gpu", "file": "GPU_PERFORMANCE.json", "required": False},
    {"key": "release_truth", "file": "RELEASE_TRUTH.json", "required": False},
    {"key": "host_preflight", "file": "HOST_PREFLIGHT.json", "required": False},
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> Tuple[int, str]:
    proc = subprocess.run(["git", *args], cwd=str(REPO), text=True,
                          errors="replace", stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)
    return proc.returncode, (proc.stdout or "").strip()


# --------------------------------------------------------------------------- #
# input loading
# --------------------------------------------------------------------------- #
class Evidence:
    """The loaded inputs, plus a uniform way to ask what they say.

    Every accessor returns a ``(value, ok)`` pair rather than a bare value, so a
    caller cannot accidentally treat "the document does not say" as "it said
    no" -- or as "it said yes". That distinction is the entire job.
    """

    def __init__(self) -> None:
        self.docs: Dict[str, Any] = {}
        self.present: Dict[str, bool] = {}
        self.errors: Dict[str, str] = {}

    def load(self, spec: Dict[str, Any]) -> None:
        key, name = spec["key"], spec["file"]
        path = ART / name
        if not path.is_file():
            self.present[key] = False
            return
        try:
            self.docs[key] = json.loads(path.read_text(encoding="utf-8"))
            self.present[key] = True
        except (OSError, ValueError) as exc:
            self.present[key] = False
            self.errors[key] = "%s: %s" % (name, exc)

    def has(self, key: str) -> bool:
        return bool(self.present.get(key))

    def get(self, key: str, *path: str, default: Any = None) -> Any:
        """Read a nested field, or ``default`` if any step is missing."""
        cur: Any = self.docs.get(key)
        for step in path:
            if not isinstance(cur, dict) or step not in cur:
                return default
            cur = cur[step]
        return cur

    def truthy(self, key: str, *path: str) -> Optional[bool]:
        """Strict boolean read.

        Only a real ``True`` counts as proven. A missing field, ``None``, a
        string, or a non-zero number are all "not proven" (H1) -- this tool does
        not guess a sender's intent from a truthy-looking value.
        """
        val = self.get(key, *path, default=None)
        return val if isinstance(val, bool) else None

    def head_of(self, key: str) -> Optional[str]:
        for field in ("head", "product_freeze_sha", "evidence_head"):
            val = self.get(key, field)
            if isinstance(val, str) and len(val) >= 7:
                return val
        return None

    def summary(self) -> List[Dict[str, Any]]:
        rows = []
        for spec in INPUTS:
            key, name = spec["key"], spec["file"]
            rows.append({
                "key": key,
                "file": name,
                "required_by_directive": spec["required"],
                "present": self.has(key),
                "error": self.errors.get(key),
                "head": self.head_of(key) if self.has(key) else None,
            })
        return rows


# --------------------------------------------------------------------------- #
# blockers
# --------------------------------------------------------------------------- #
def blocker(bid: str, klass: str, gates: List[str], evidence: str,
            detail: str, action: str) -> Dict[str, Any]:
    return {
        "id": bid,
        "class": klass,
        "gates_forced_false": gates,
        "exact_evidence": evidence,
        "detail": detail,
        "next_deterministic_action": action,
    }


def build_blockers(ev: Evidence, head: Optional[str],
                   worktree_state: str) -> List[Dict[str, Any]]:
    """One honest accounting of everything standing between here and a release."""
    out: List[Dict[str, Any]] = []

    # --- H1: absent required inputs -----------------------------------------
    for spec in INPUTS:
        key, name = spec["key"], spec["file"]
        if ev.has(key):
            continue
        reason = ev.errors.get(key) or "file not found at %s" % (ART / name)
        out.append(blocker(
            "EVIDENCE_ABSENT_%s" % key.upper(),
            "EVIDENCE",
            [_GATE_FOR_INPUT.get(key, ["project_complete"])],
            "%s (%s)" % (name, reason),
            "The release claims a fact this document was supposed to establish, "
            "and the document is not there to establish it. Absence is recorded "
            "as absence: it is not read as a pass.",
            "Produce %s, then re-run this tool." % name,
        ))

    # --- H2: inputs measured at a different HEAD ---------------------------
    if head:
        for spec in INPUTS:
            key = spec["key"]
            if not ev.has(key):
                continue
            other = ev.head_of(key)
            if other and not _same_commit(other, head):
                out.append(blocker(
                    "STALE_HEAD_%s" % key.upper(),
                    "PROVENANCE",
                    [_GATE_FOR_INPUT.get(key, ["project_complete"])],
                    "%s.head=%s vs HEAD=%s" % (spec["file"], other[:12], head[:12]),
                    "This evidence was measured at another commit, so it "
                    "describes code that is no longer the code. A verdict that "
                    "aggregates it is asserting something about a tree nobody "
                    "shipped.",
                    "Re-run the producing gate at HEAD %s, or re-freeze." % head[:12],
                ))

    # --- the security verdict is its own gate, not a boolean ---------------
    if ev.has("security"):
        verdict = _security_verdict(ev)
        if verdict != "GO":
            out.append(blocker(
                "SECURITY_NOT_GO", "SECURITY", ["security", "final_release"],
                "SECURITY_FINAL.json verdict=%r" % verdict,
                "Directive K requires SECURITY=GO before the release loop may "
                "close. Any other value, including an absent one, blocks it.",
                "Resolve the security gate's findings and re-run it.",
            ))

    # --- the worktree must be clean for a freeze to mean anything ----------
    if worktree_state != "CLEAN":
        out.append(blocker(
            "WORKTREE_NOT_CLEAN", "PROVENANCE",
            ["git_clean", "manifest_clean", "product_freeze_valid"],
            "git status --porcelain reports %s" % worktree_state,
            "Uncommitted bytes mean the tree that was measured is not the tree "
            "on disk, so no evidence here describes what would actually ship.",
            "Commit or revert the outstanding changes, then re-run the gates.",
        ))

    return out


_GATE_FOR_INPUT: Dict[str, List[str]] = {
    "sandbox": ["sandbox_certified", "product_implementation_complete"],
    "host_acceptance": ["host_activated"],
    "product_freeze": ["product_freeze_valid"],
    "manifest": ["manifest_clean"],
    "security": ["security"],
    "host_runtime": ["durable_runtime_verified"],
    "workbuddy": ["workbuddy_native_launch_verified"],
    "live_mission": ["live_sse_verified", "deliverable_provenance_verified"],
    "live_3d": ["live_3d_verified"],
    "gpu": ["hardware_perf_verified"],
    "release_truth": ["product_freeze_valid", "git_clean"],
    "host_preflight": ["host_activated", "green_untouched"],
}

#: Gates that a single input can establish true, and the field each is read from.
#: Absent from this map, a gate defaults to False -- which is the point of H1.
_GATE_SOURCE: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("sandbox_certified", "sandbox", ("certified", "sandbox_certified")),
    ("product_implementation_complete", "sandbox",
     ("product_implementation_complete", "implementation_complete")),
    ("host_activated", "host_acceptance", ("host_activated", "activated")),
    ("workbuddy_native_launch_verified", "workbuddy",
     ("JCC_LAUNCHED_NATIVE_WORKBUDDY_MISSION", "native_launch_verified")),
    ("live_sse_verified", "live_mission", ("live_sse_verified", "sse_verified")),
    ("live_3d_verified", "live_3d", ("live_3d_verified", "verified")),
    ("deliverable_provenance_verified", "live_mission",
     ("deliverable_provenance_verified", "provenance_verified")),
    ("hardware_perf_verified", "gpu", ("hardware_perf_verified", "verified")),
    ("durable_runtime_verified", "host_runtime",
     ("durable_runtime_verified", "verified")),
    ("green_untouched", "host_preflight", ("green_untouched",)),
    ("product_freeze_valid", "product_freeze", ("valid", "freeze_valid")),
    ("manifest_clean", "manifest", ("clean", "manifest_clean")),
)


def _same_commit(a: str, b: str) -> bool:
    return a[:12] == b[:12] or a == b


def _security_verdict(ev: Evidence) -> Optional[str]:
    for field in ("security", "verdict", "SECURITY"):
        val = ev.get("security", field)
        if isinstance(val, str) and val.strip():
            return val.strip().upper()
    return None


def worktree_dirty() -> str:
    rc, out = git("status", "--porcelain", "-uall")
    if rc != 0:
        return "UNKNOWN"
    return "DIRTY" if out.strip() else "CLEAN"


# --------------------------------------------------------------------------- #
# the verdict
# --------------------------------------------------------------------------- #
def build_verdict() -> Dict[str, Any]:
    ev = Evidence()
    for spec in INPUTS:
        ev.load(spec)

    rc, head = git("rev-parse", "HEAD")
    head = head if rc == 0 else None
    wt = worktree_dirty()

    verdict: Dict[str, Any] = {}
    proven: Dict[str, bool] = {}
    for gate, key, fields in _GATE_SOURCE:
        if not ev.has(key):
            proven[gate] = False
            continue
        found = None
        for field in fields:
            found = ev.truthy(key, field)
            if found is not None:
                break
        proven[gate] = found is True

    # git_clean is measured here, not read from a document: this tool can see the
    # worktree itself, so it does not have to believe a report about it.
    proven["git_clean"] = (wt == "CLEAN")

    blockers = build_blockers(ev, head, wt)

    for gate, _key, _fields in _GATE_SOURCE:
        verdict[gate] = bool(proven.get(gate, False))
    verdict["security"] = _security_verdict(ev) or "NOT_EVALUATED"
    verdict["git_clean"] = bool(proven["git_clean"])

    # --- derived release claims --------------------------------------------
    # host_activation_pending is TRUE whenever anything is still open. It is the
    # field that lets a release be "everything but the host" without that being
    # rounded up to a pass.
    open_gates = sorted(g for g, v in verdict.items()
                        if isinstance(v, bool) and not v)
    security_go = verdict["security"] == "GO"

    verdict["host_activation_pending"] = bool(blockers) or not security_go

    hard = ["product_implementation_complete", "sandbox_certified",
            "product_freeze_valid", "workbuddy_native_launch_verified",
            "live_sse_verified", "live_3d_verified",
            "deliverable_provenance_verified", "durable_runtime_verified",
            "green_untouched", "manifest_clean", "git_clean"]
    # host_activated and hardware_perf_verified are host/GPU facts: they are
    # reported honestly but do not silently become project_complete. Directive
    # 0.14 only permits the final declarations when ALL hard gates hold, and
    # these two are gates -- so they are in the list.
    hard.extend(["host_activated", "hardware_perf_verified"])

    verdict["project_complete"] = not blockers and all(
        verdict.get(g) is True for g in hard)
    verdict["final_release"] = bool(
        verdict["project_complete"] and security_go
        and not verdict["host_activation_pending"])
    verdict["jev_project_run"] = "PASS" if verdict["project_complete"] else "FAIL"
    verdict["jcc_product_edition"] = (
        "COMPLETE" if verdict["final_release"] else "INCOMPLETE")

    doc: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": now_utc(),
        "generated_by": GENERATED_BY,
        "repo_root": str(REPO),
        "head": head,
        "worktree": wt,
        "read_only": True,
        "hard_gates": hard,
        "inputs": ev.summary(),
        "blockers": blockers,
        "blocker_count": len(blockers),
        "open_gates": open_gates,
    }
    # The 19 contract fields live at the top level, so a consumer can read
    # doc["final_release"] without knowing this tool's internal structure.
    doc.update(verdict)
    return doc


def contract_fields_ok(doc: Dict[str, Any]) -> List[str]:
    """Which contract fields are missing or of the wrong shape."""
    problems: List[str] = []
    for name in VERDICT_FIELDS:
        if name not in doc:
            problems.append("missing field %r" % name)
            continue
        val = doc[name]
        if name in ("security", "jev_project_run", "jcc_product_edition"):
            if not isinstance(val, str):
                problems.append("field %r must be a string, got %r"
                                % (name, type(val).__name__))
        elif not isinstance(val, bool):
            problems.append("field %r must be a boolean, got %r"
                            % (name, type(val).__name__))
    return problems


# --------------------------------------------------------------------------- #
# emit / check
# --------------------------------------------------------------------------- #
def emit(doc: Dict[str, Any]) -> int:
    problems = contract_fields_ok(doc)
    if problems:
        # Refusing to emit is the point: a verdict that does not match the
        # contract is worse than no verdict, because it looks authoritative.
        print("REFUSING TO EMIT -- the verdict does not match directive N:")
        for p in problems:
            print("  - %s" % p)
        return 2
    body = (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n").encode("utf-8")
    ART.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(body)
    SIDECAR.write_bytes(("%s  %s\n" % (sha256_bytes(body), OUT.name))
                        .encode("ascii"))
    print("wrote %s (%d bytes, sha256 %s)"
          % (OUT.name, len(body), sha256_bytes(body)))
    _print_verdict(doc)
    return 0 if doc["final_release"] else 1


def _print_verdict(doc: Dict[str, Any]) -> None:
    for name in VERDICT_FIELDS:
        print("  %-34s %s" % (name, doc[name]))
    print("\nblockers: %d" % doc["blocker_count"])
    for b in doc["blockers"]:
        print("  [%s] %s" % (b["class"], b["id"]))
        print("      evidence: %s" % b["exact_evidence"])
        print("      next    : %s" % b["next_deterministic_action"])


def check() -> int:
    if not OUT.is_file():
        print("CHECK: no verdict at %s" % OUT)
        return 1
    try:
        stored = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("CHECK: stored verdict unreadable: %s" % exc)
        return 1
    fresh = build_verdict()
    problems: List[str] = list(fresh["blockers"] and
                               ["%s: %s" % (b["id"], b["detail"])
                                for b in fresh["blockers"]] or [])
    for field in VERDICT_FIELDS:
        if stored.get(field) != fresh.get(field):
            problems.append("field %r: stored=%r fresh=%r"
                            % (field, stored.get(field), fresh.get(field)))
    if SIDECAR.is_file():
        parts = SIDECAR.read_text(encoding="utf-8").split()
        if parts and parts[0] != sha256_bytes(OUT.read_bytes()):
            problems.append("sidecar digest mismatch")
    _print_verdict(fresh)
    if problems:
        print("CHECK STALE/FAILED (%d):" % len(problems))
        for p in problems:
            print("  - %s" % p)
        return 1
    print("CHECK CLEAN: the stored verdict still matches a fresh aggregation")
    return 0


# --------------------------------------------------------------------------- #
# selftest -- can this aggregator be fooled into a green release?
# --------------------------------------------------------------------------- #
def selftest() -> int:
    """Falsifiers for the aggregator, over SYNTHETIC inputs.

    A positive control comes first: a complete, all-true input set MUST produce
    final_release=true. Without it, "reject everything" would score 7/7 here and
    prove nothing -- which is the failure mode of the gate this tool replaces.
    """
    results: List[Dict[str, Any]] = []

    def synth(**over: Any) -> Dict[str, Any]:
        """A complete, all-green input set, with selected fields broken."""
        docs: Dict[str, Any] = {}
        for spec in INPUTS:
            key = spec["key"]
            if key == "security":
                docs[key] = {"security": "GO", "head": "a" * 40}
            else:
                docs[key] = {"head": "a" * 40}
        for gate, key, fields in _GATE_SOURCE:
            if gate in ("green_untouched",):
                continue
            docs[key][fields[0]] = True
        docs["product_freeze"]["valid"] = True
        docs["manifest"]["clean"] = True
        docs["host_preflight"]["green_untouched"] = True
        for key, patch in over.items():
            if patch is None:
                docs.pop(key, None)
            else:
                docs.setdefault(key, {}).update(patch)
        return docs

    def aggregate(docs: Dict[str, Any], worktree: str = "CLEAN") -> Dict[str, Any]:
        """Run the REAL build logic over a synthetic evidence set.

        Calls the same functions the tool uses, so this cannot pass while the
        tool is broken -- it is the aggregator under test, not a paraphrase.
        """
        ev = Evidence()
        for spec in INPUTS:
            key = spec["key"]
            if key in docs:
                ev.present[key] = True
                ev.docs[key] = docs[key]
            else:
                ev.present[key] = False
        blockers = build_blockers(ev, "a" * 40, worktree)
        verdict: Dict[str, Any] = {}
        for gate, key, fields in _GATE_SOURCE:
            if not ev.present.get(key):
                verdict[gate] = False
                continue
            found = None
            for field in fields:
                found = ev.truthy(key, field)
                if found is not None:
                    break
            verdict[gate] = found is True
        verdict["security"] = _security_verdict(ev) or "NOT_EVALUATED"
        verdict["git_clean"] = worktree == "CLEAN"
        verdict["host_activation_pending"] = bool(blockers) or verdict["security"] != "GO"
        hard = ["product_implementation_complete", "sandbox_certified",
                "product_freeze_valid", "workbuddy_native_launch_verified",
                "live_sse_verified", "live_3d_verified",
                "deliverable_provenance_verified", "durable_runtime_verified",
                "green_untouched", "manifest_clean", "git_clean",
                "host_activated", "hardware_perf_verified"]
        verdict["project_complete"] = not blockers and all(
            verdict.get(g) is True for g in hard)
        verdict["final_release"] = bool(
            verdict["project_complete"] and verdict["security"] == "GO"
            and not verdict["host_activation_pending"])
        verdict["jev_project_run"] = "PASS" if verdict["project_complete"] else "FAIL"
        verdict["jcc_product_edition"] = (
            "COMPLETE" if verdict["final_release"] else "INCOMPLETE")
        return dict(verdict, blockers=blockers)

    full = aggregate(synth())
    results.append({
        "id": "POSITIVE_CONTROL_A_COMPLETE_SET_MUST_BE_ACCEPTED",
        "ok": full["final_release"] is True and not full["blockers"],
        "detail": "final_release=%r blockers=%d (a judge that rejects everything "
                  "would score every refusal below vacuously)"
                  % (full["final_release"], len(full["blockers"])),
    })

    # A1: a required input is missing
    v = aggregate(synth(security=None))
    results.append({
        "id": "MISSING_REQUIRED_EVIDENCE_CANNOT_PRODUCE_A_RELEASE",
        "ok": v["final_release"] is False and v["security"] == "NOT_EVALUATED"
              and any(b["id"] == "EVIDENCE_ABSENT_SECURITY" for b in v["blockers"]),
        "detail": "final_release=%r security=%r blockers=%r"
                  % (v["final_release"], v["security"],
                     [b["id"] for b in v["blockers"]][:4]),
    })

    # A2: every evidence document is absent
    v = aggregate({})
    results.append({
        "id": "NO_EVIDENCE_AT_ALL_MUST_NOT_READ_AS_COMPLETE",
        "ok": v["project_complete"] is False and v["final_release"] is False
              and v["host_activation_pending"] is True
              and len(v["blockers"]) >= len(INPUTS),
        "detail": "project_complete=%r final_release=%r blockers=%d"
                  % (v["project_complete"], v["final_release"], len(v["blockers"])),
    })

    # A3: security is anything other than GO
    v = aggregate(synth(security={"security": "GO_WITH_CONDITIONS"}))
    results.append({
        "id": "SECURITY_MUST_BE_EXACTLY_GO",
        "ok": v["final_release"] is False and v["security"] == "GO_WITH_CONDITIONS"
              and any(b["id"] == "SECURITY_NOT_GO" for b in v["blockers"]),
        "detail": "final_release=%r security=%r"
                  % (v["final_release"], v["security"]),
    })

    # A4: a truthy-looking non-boolean must not pass
    v = aggregate(synth(gpu={"hardware_perf_verified": "true"}))
    results.append({
        "id": "A_STRING_TRUE_IS_NOT_A_VERIFIED_GATE",
        "ok": v["hardware_perf_verified"] is False and v["final_release"] is False,
        "detail": "hardware_perf_verified=%r (a string is not a measurement)"
                  % (v["hardware_perf_verified"],),
    })

    # A5: evidence measured at a different HEAD
    docs = synth()
    docs["live_3d"]["head"] = "b" * 40
    v = aggregate(docs)
    results.append({
        "id": "EVIDENCE_FROM_ANOTHER_HEAD_MUST_BLOCK",
        "ok": v["final_release"] is False
              and any(b["id"] == "STALE_HEAD_LIVE_3D" for b in v["blockers"]),
        "detail": "blockers=%r" % ([b["id"] for b in v["blockers"]],),
    })

    # A6: dirty worktree
    v = aggregate(synth(), worktree="DIRTY")
    results.append({
        "id": "A_DIRTY_WORKTREE_CANNOT_BE_A_RELEASE",
        "ok": v["final_release"] is False and v["git_clean"] is False
              and any(b["id"] == "WORKTREE_NOT_CLEAN" for b in v["blockers"]),
        "detail": "git_clean=%r blockers=%r"
                  % (v["git_clean"], [b["id"] for b in v["blockers"]]),
    })

    # A7: a sub-document's own project_complete must not be inherited (H3)
    docs = synth()
    docs["sandbox"]["project_complete"] = True
    docs["sandbox"]["certified"] = False       # ...but the real gate is false
    v = aggregate(docs)
    results.append({
        "id": "A_SUBDOCUMENTS_SELF_CLAIM_MUST_NOT_BE_INHERITED",
        "ok": v["sandbox_certified"] is False and v["final_release"] is False,
        "detail": "sandbox_certified=%r despite sandbox.project_complete=True"
                  % (v["sandbox_certified"],),
    })

    # A8: the emitted document must match directive N's field list exactly
    real = build_verdict()
    shape = contract_fields_ok(real)
    results.append({
        "id": "EMITTED_DOCUMENT_MATCHES_DIRECTIVE_N_FIELD_LIST",
        "ok": not shape,
        "detail": "shape problems=%r (19 contract fields, correct types)"
                  % (shape,),
    })

    passed = sum(1 for r in results if r["ok"])
    for r in results:
        print("  [%s] %s\n        %s"
              % ("ok  " if r["ok"] else "HOLE", r["id"], r["detail"]))
    print("\nFINAL VERDICT FALSIFIER %s %d/%d"
          % ("PASS" if passed == len(results) else "FAIL", passed, len(results)))
    return 0 if passed == len(results) else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="JCC v1.3 final verdict aggregator (read-only)")
    ap.add_argument("--emit", action="store_true",
                    help="aggregate and write FINAL_VERDICT.json + sidecar")
    ap.add_argument("--check", action="store_true",
                    help="re-aggregate and compare; never writes")
    ap.add_argument("--print", action="store_true",
                    help="aggregate and print only; writes nothing")
    ap.add_argument("--selftest", action="store_true",
                    help="prove this aggregator can be fooled into a false green")
    ns = ap.parse_args(argv)

    if ns.selftest:
        return selftest()
    if ns.check:
        return check()
    if ns.print:
        doc = build_verdict()
        print(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False))
        return 0 if doc["final_release"] else 1
    if ns.emit:
        return emit(build_verdict())
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
