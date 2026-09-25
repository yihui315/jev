#!/usr/bin/env python3
"""Generate `final_sre_go.json` from evidence. Never hand-write the verdict.

The verdict is derived, not decided: every leg and every E-07 step must be PASS,
otherwise the document is NO-GO. There is no flag, environment variable or
command line switch that can turn a NO-GO into a GO, because (a) the file is
derived from files that are themselves machine-produced, and (b) the file is now
tracked in git, so an in-place edit to make it look better shows up in
`git status` and breaks the clean-tree gate.

Usage:
    python tools/generate_final_sre_go.py [--evidence <path>] [--out <path>]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from datetime import datetime, timezone, timedelta

ROOT = r"F:\jev"
DEFAULT_EVIDENCE = os.path.join(ROOT, r"artifacts\acceptance\win_scripts\E07_FINAL_EVIDENCE.json")
DEFAULT_OUT = os.path.join(ROOT, r"artifacts\acceptance\final_sre_go.json")
ARCH_LEG = os.path.join(ROOT, r"artifacts\acceptance\final_architecture_go.json")
SEC_LEG = os.path.join(ROOT, r"artifacts\acceptance\final_security_go.json")

TZ = timezone(timedelta(hours=8))


def load_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return None


def sha256_file(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", default=DEFAULT_EVIDENCE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    problems: list[str] = []

    ev = load_json(args.evidence)
    if not ev:
        print("NO-GO: evidence missing or unreadable: %s" % args.evidence)
        return 2

    steps = ev.get("steps") or []
    non_pass = [s.get("name") for s in steps if s.get("result") != "PASS"]
    if non_pass:
        problems.append("E-07 steps not PASS: %r" % non_pass)

    doc = {
        "evidence_sha256": sha256_file(args.evidence),
        "steps_total": len(steps),
        "steps_pass": len(steps) - len(non_pass),
        "steps_not_pass": non_pass,
    }

    # the other two legs, and the release sha they were signed on
    legs = {}
    for name, path in (("architecture", ARCH_LEG), ("security", SEC_LEG)):
        leg = load_json(path) or {}
        verdict = leg.get("verdict")
        legs[name] = {
            "verdict": verdict,
            "release_sha": leg.get("release_sha"),
            "signed_at": leg.get("signed_at"),
            "sha256": sha256_file(path),
        }
        if verdict not in ("GO", "GO WITH CONDITIONS"):
            problems.append("leg %s verdict is %r" % (name, verdict))

    ev_head = ev.get("head_sha")
    for name, info in legs.items():
        if info.get("release_sha") and ev_head and not str(info["release_sha"]).startswith(
                str(ev_head)[:12]):
            problems.append("leg %s was signed on %s but E-07 ran on %s"
                            % (name, info["release_sha"], ev_head))

    aborted = ev.get("abort")
    if aborted:
        problems.append("E-07 aborted: %s" % aborted.get("reason"))

    if problems:
        doc.update({
            "verdict": "NO-GO",
            "release_sha": ev_head,
            "signed_at": datetime.now(TZ).isoformat(),
            "signer": "generate_final_sre_go.py",
            "generator": "tools/generate_final_sre_go.py (derived; do not hand-edit)",
            "problems": problems,
            "legs": legs,
            "evidence_refs": [
                {"name": s.get("name"), "result": s.get("result"),
                 "timestamp": s.get("timestamp"), "duration_s": s.get("duration_s"),
                 "measured_pid": s.get("measured_pid"),
                 "measured_port": s.get("measured_port")}
                for s in steps
            ],
        })
        tmp = args.out + ".tmp.%d" % os.getpid()
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(doc, indent=2, ensure_ascii=False))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, args.out)
        print("NO-GO (%d problem(s)) -> %s" % (len(problems), args.out))
        for p in problems:
            print("   -", p)
        return 1

    doc.update({
        "verdict": "GO",
        "release_sha": ev_head,
        "signed_at": datetime.now(TZ).isoformat(),
        "signer": "generate_final_sre_go.py",
        "generator": "tools/generate_final_sre_go.py (derived; do not hand-edit)",
        "product": "JEV COMMAND CENTER",
        "phase": "P6 / E-07 final acceptance",
        "legs": legs,
        "conditions": [],
        "evidence_refs": [
            {"name": s.get("name"), "result": s.get("result"),
             "timestamp": s.get("timestamp"), "duration_s": s.get("duration_s"),
             "measured_pid": s.get("measured_pid"),
             "measured_port": s.get("measured_port"),
             "command": s.get("command"),
             "exit_code": s.get("exit_code"),
             "stdout_sha256": s.get("stdout_sha256"),
             "stderr_sha256": s.get("stderr_sha256")}
            for s in steps
        ],
        "throughput": next(
            ({"rate_per_s": s["details"].get("rate_per_s"),
              "ok": s["details"].get("ok"),
              "failed": s["details"].get("failed"),
              "window_s": s["details"].get("window_s")}
             for s in steps if s.get("name") == "THROUGHPUT"), None),
    })

    tmp = args.out + ".tmp.%d" % os.getpid()
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(doc, indent=2, ensure_ascii=False))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, args.out)
    print("GO -> %s (%d evidence refs)"
          % (args.out, len(doc["evidence_refs"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
