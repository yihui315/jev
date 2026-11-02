#!/usr/bin/env python
"""§10 v1.1 four-agent capability matrix — MEASURE, then RE-CHECK the claim.

WHAT THIS IS
    §10 asks for a capability matrix over the four agents JCC integrates:
    WorkBuddy, Codex, Claude, Hermes. The measurement already exists --
    ``tools/probe_adapter_capabilities.py`` probes each adapter read-only and
    writes one evidence file per agent. This script turns that into the v1.1
    artifact and, more importantly, does the thing the probe cannot do for
    itself: verify that "VERIFIED" is true.

WHY A SECOND SCRIPT RATHER THAN READING THE PROBE'S WORD
    The probe reports a status per capability. Trusting a status field is how
    this project has repeatedly shipped a green light over nothing. So for every
    capability the probe calls VERIFIED this script re-derives the four facts
    that make that claim true, from the evidence file and from the FILESYSTEM:

        (a) ``record_source == "live"`` and ``mode == "live"``
            The probe's own R1 rule says "A repository fixture is NOT
            sufficient." A fixture-sourced VERIFIED is a mock wearing the real
            badge, which is exactly what §10 forbids reporting.
        (b) the named ``record_path`` is a real FILE on this disk, NOW
        (c) for ``telemetry``: a non-empty ``envelope_session_id``, so identity
            really resolved (ADR-0003: identity is session_id, never cwd)
        (d) for ``telemetry``: ``envelope_schema_ok is True``, so normalize()
            really produced a valid AEPEnvelope

    Anything that fails (a)-(d) is written as DEGRADED, not VERIFIED. That is
    what gives DEGRADED a non-theoretical meaning here instead of being a fourth
    label kept for symmetry: it means "the probe claimed it and the claim did not
    survive re-checking".

THE FOUR STATES ARE PINNED
    §10 permits exactly VERIFIED / DEGRADED / UNSUPPORTED / UNVERIFIED. Nothing
    else is writable, and a test asserts the artefact contains no other word.

WHAT IS DELIBERATELY *NOT* IN THE ARTIFACT, AND WHY IT MATTERS
    No hash of a live record file. Those sources are append-only by nature --
    ``~/.hermes/gateway_state.json`` is rewritten while this runs -- so a digest
    of them would change on every invocation and ``--check`` would fail every
    time. A check that always fails is worse than no check, because it teaches
    the operator to ignore it. The reproducibility anchor is instead a digest of
    the NORMALISED probe evidence (volatile keys stripped), which is stable, and
    ``--check`` compares normalised forms end to end.

Usage
    F:/jev/.venv/Scripts/python.exe tools/v1_1_emit_capability_matrix.py
    F:/jev/.venv/Scripts/python.exe tools/v1_1_emit_capability_matrix.py --check
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

REPO = Path(__file__).resolve().parent.parent
PROBE = REPO / "tools" / "probe_adapter_capabilities.py"
PROBE_REL = "tools/probe_adapter_capabilities.py"
EVIDENCE_DIR = REPO / "artifacts" / "acceptance" / "adapter_capability"
EVIDENCE_REL = "artifacts/acceptance/adapter_capability"
OUT = REPO / "artifacts" / "release" / "V1_1_CAPABILITY_MATRIX.json"
OUT_REL = "artifacts/release/V1_1_CAPABILITY_MATRIX.json"

SCHEMA = "jcc-v1.1-capability-matrix/1"

#: §10's vocabulary. Pinned by value: these four and nothing else is writable.
STATES: Tuple[str, ...] = ("VERIFIED", "DEGRADED", "UNSUPPORTED", "UNVERIFIED")

#: §10's probe order, spelled out rather than discovered, so a change of order
#: is a reviewed edit and the artifact records the order it was produced under.
AGENTS: Tuple[str, ...] = ("workbuddy", "codex", "claude", "hermes")

#: The probe's capability list, pinned here too so a matrix missing a column is a
#: failure rather than a shorter table.
CAPABILITIES: Tuple[str, ...] = (
    "telemetry",
    "pause",
    "verify",
    "steer",
    "retry",
    "cancel",
    "session_message",
    "approval",
)

#: §10's central claim is that nothing can be actuated. That is a statement about
#: these, so they are named explicitly rather than inferred as "not telemetry".
ACTUATION_CAPABILITIES: Tuple[str, ...] = tuple(
    c for c in CAPABILITIES if c != "telemetry"
)

#: Stripped recursively before hashing/comparing. `probe_at` and `generated_at`
#: are clocks; the rest describe the tree, not the finding. Kept as one frozen
#: set matched by KEY NAME at any depth, so a nested occurrence is not missed --
#: the §7F emitter lost a day to exactly that mistake.
VOLATILE_KEYS = frozenset({
    "probe_at",
    "generated_at",
    "repo_head",
    "repo_branch",
    "worktree_dirty",
    "tracked_file_count",
})


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(*args: str) -> str:
    try:
        p = subprocess.run(
            ["git", *args], cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (p.stdout or "").strip()


def _normalize(obj: Any) -> Any:
    """Recursively drop volatile keys so two runs are comparable."""
    if isinstance(obj, dict):
        return {
            k: _normalize(v)
            for k, v in sorted(obj.items())
            if k not in VOLATILE_KEYS
        }
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    return obj


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest_normalized(obj: Any) -> str:
    return hashlib.sha256(_canonical(_normalize(obj)).encode("utf-8")).hexdigest()


def _reverify_verified(agent: str, cap: str, detail: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Independently re-check one probe 'VERIFIED'. Returns (evidence, problems).

    ``problems`` non-empty means the probe's claim did not survive, and the
    caller must write DEGRADED rather than VERIFIED.
    """
    evidence: Dict[str, Any] = {
        "probe_mode": detail.get("mode"),
        "record_source": detail.get("record_source"),
        "record_path": detail.get("record_path"),
        "record_exists": None,
        "envelope_session_id_present": bool(detail.get("envelope_session_id")),
        "envelope_schema_ok": detail.get("envelope_schema_ok"),
    }
    problems: List[str] = []

    # (a) a fixture is not evidence -- the probe's own R1 says so.
    if evidence["record_source"] != "live":
        problems.append(
            "record_source=%r is not 'live' (a repository fixture is not evidence)"
            % (evidence["record_source"],)
        )
    if evidence["probe_mode"] != "live":
        problems.append("mode=%r is not 'live'" % (evidence["probe_mode"],))

    # (b) the record must exist on THIS disk, now.
    rp = detail.get("record_path")
    if rp:
        exists = Path(str(rp)).is_file()
        evidence["record_exists"] = exists
        if not exists:
            problems.append("record_path does not exist on disk: %s" % (rp,))
    else:
        evidence["record_exists"] = False
        problems.append("no record_path was recorded, so nothing is verifiable")

    # (c)/(d) telemetry additionally claims a normalised identity.
    if cap == "telemetry":
        if not evidence["envelope_session_id_present"]:
            problems.append("envelope_session_id is empty (identity did not resolve)")
        if evidence["envelope_schema_ok"] is not True:
            problems.append(
                "envelope_schema_ok=%r (normalize() did not produce a valid AEPEnvelope)"
                % (evidence["envelope_schema_ok"],)
            )
    return evidence, problems


def _agent_block(agent: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    self_report = raw.get("adapter_self_report") or {}
    probe_caps = raw.get("capabilities") or {}
    probe_detail = raw.get("capabilities_detail") or {}

    caps: Dict[str, Any] = {}
    for cap in CAPABILITIES:
        reported = str((probe_caps.get(cap) or {}).get("status") or "UNVERIFIED")
        reason = str((probe_caps.get(cap) or {}).get("reason") or "")
        detail = probe_detail.get(cap) or {}

        entry: Dict[str, Any] = {"reported_status": reported, "reason": reason}
        if reported == "VERIFIED":
            evidence, problems = _reverify_verified(agent, cap, detail)
            entry["evidence"] = evidence
            if problems:
                entry["status"] = "DEGRADED"
                entry["degraded_reason"] = "; ".join(problems)
            else:
                entry["status"] = "VERIFIED"
        elif reported in STATES:
            entry["status"] = reported
        else:
            # A status outside §10's vocabulary is not silently kept.
            entry["status"] = "UNVERIFIED"
            entry["unmapped_reported_status"] = reported
        caps[cap] = entry

    counts = {s: sum(1 for c in CAPABILITIES if caps[c]["status"] == s) for s in STATES}
    self_status = str(self_report.get("status") or "UNKNOWN")
    # The adapter's own probe() knows things the capability table does not (e.g.
    # a native hook runtime present but not exercised), so its DEGRADED is a
    # real signal and is carried up rather than averaged away.
    if self_status == "DEGRADED":
        adapter_state = "DEGRADED"
    elif raw.get("status") in STATES:
        adapter_state = str(raw.get("status"))
    else:
        adapter_state = "UNVERIFIED"

    return {
        "adapter_state": adapter_state,
        "adapter_self_status": self_status,
        "installed": bool(self_report.get("installed")),
        "version": self_report.get("version"),
        "config_path": self_report.get("config_path"),
        "hook_schema_kind": self_report.get("hook_schema_kind"),
        "discovery_actuation_enabled": self_report.get("discovery_actuation_enabled"),
        "public_callables_on_adapter": self_report.get("public_callables_on_adapter"),
        "status_counts": counts,
        "capabilities": caps,
    }


def derive() -> Dict[str, Any]:
    """Build the matrix from the evidence currently on disk. No probing."""
    evidence_files = {a: EVIDENCE_DIR / ("%s.json" % a) for a in AGENTS}
    missing = sorted(rel for rel, p in evidence_files.items() if not p.is_file())
    if missing:
        raise SystemExit(
            "missing probe evidence for %s under %s -- run %s first"
            % (", ".join(missing), EVIDENCE_REL, PROBE_REL)
        )

    agents: Dict[str, Any] = {}
    for agent in AGENTS:
        path = evidence_files[agent]
        raw = json.loads(path.read_text(encoding="utf-8"))
        block = _agent_block(agent, raw)
        block["probe_evidence_file"] = "%s/%s.json" % (EVIDENCE_REL, agent)
        block["probe_evidence_digest"] = "sha256:" + _digest_normalized(raw)
        block["probe_reported_status"] = raw.get("status")
        agents[agent] = block

    actuable = [
        {"agent": a, "capability": c}
        for a, blk in agents.items()
        for c in ACTUATION_CAPABILITIES
        if blk["capabilities"][c]["status"] == "VERIFIED"
    ]

    return {
        "schema": SCHEMA,
        "generated_at": _utc(),
        "repo_head": _git("rev-parse", "HEAD"),
        "repo_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "worktree_dirty": bool(_git("status", "--porcelain")),
        "section": "§10",
        "vocabulary": list(STATES),
        "probe_order": list(AGENTS),
        "capabilities": list(CAPABILITIES),
        "actuation_capabilities": list(ACTUATION_CAPABILITIES),
        "probe": {
            "script": PROBE_REL,
            "evidence_dir": EVIDENCE_REL,
            "measurement": "read-only probe; never pauses, steers or cancels a real agent",
        },
        "agents": agents,
        "actuation_summary": {
            "actuable_capability_count": len(actuable),
            "actuable": actuable,
            "note": ("A VERIFIED actuation capability would mean JCC can move a "
                     "worker. Every one must stay non-VERIFIED until the live "
                     "shadow gate opens; this counter is the check."),
        },
        "discipline_notes": [
            "Every VERIFIED here was re-derived from the evidence file AND the "
            "filesystem, not read off the probe's status field.",
            "record_source must be 'live': the probe's own rule is that a "
            "repository fixture is not evidence.",
            "No digest of a live record file is recorded: those files are "
            "append-only, so a digest would change every run and the check "
            "would be ignored.",
            "probe_evidence_digest hashes the NORMALISED evidence (volatile "
            "keys stripped), which is why it is stable across runs.",
        ],
    }


def run_probe() -> int:
    print("[matrix] running %s ..." % PROBE_REL)
    p = subprocess.run(
        [sys.executable, str(PROBE)], cwd=str(REPO),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600,
    )
    if p.stdout:
        print(p.stdout.rstrip())
    if p.stderr:
        print(p.stderr.rstrip(), file=sys.stderr)
    return int(p.returncode)


def _write(matrix: Dict[str, Any]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(matrix, fh, indent=2, sort_keys=True)
        fh.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="§10 v1.1 capability matrix")
    ap.add_argument("--check", action="store_true",
                    help="re-derive from disk and compare; does not probe or write")
    ap.add_argument("--no-probe", action="store_true",
                    help="emit from the evidence already on disk (skip the probe)")
    args = ap.parse_args(argv)

    if args.check:
        if not OUT.is_file():
            print("[matrix] STALE: %s does not exist" % OUT_REL)
            return 1
        committed = json.loads(OUT.read_text(encoding="utf-8"))
        current = derive()
        if _normalize(committed) != _normalize(current):
            print("[matrix] STALE: %s no longer matches the probe evidence" % OUT_REL)
            for agent in AGENTS:
                a = _normalize(committed.get("agents", {}).get(agent))
                b = _normalize(current.get("agents", {}).get(agent))
                if a != b:
                    print("  agent %s differs" % agent)
                    if isinstance(a, dict) and isinstance(b, dict):
                        for cap in CAPABILITIES:
                            ca = (a.get("capabilities") or {}).get(cap)
                            cb = (b.get("capabilities") or {}).get(cap)
                            if ca != cb:
                                print("    %s: committed=%r now=%r"
                                      % (cap, (ca or {}).get("status"),
                                         (cb or {}).get("status")))
            return 1
        print("[matrix] OK: %s is current (%d agents x %d capabilities)"
              % (OUT_REL, len(AGENTS), len(CAPABILITIES)))
        return 0

    if not args.no_probe:
        rc = run_probe()
        if rc != 0:
            print("[matrix] REFUSING to emit: the probe exited %d" % rc)
            return 1

    matrix = derive()
    _write(matrix)
    print("[matrix] wrote %s" % OUT_REL)
    for agent in AGENTS:
        blk = matrix["agents"][agent]
        c = blk["status_counts"]
        print("  %-10s adapter=%-11s V=%d D=%d U=%d X=%d"
              % (agent, blk["adapter_state"], c["VERIFIED"], c["DEGRADED"],
                 c["UNSUPPORTED"], c["UNVERIFIED"]))
    print("  actuable capabilities: %d (must be 0)"
          % matrix["actuation_summary"]["actuable_capability_count"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
