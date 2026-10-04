#!/usr/bin/env python3
"""Re-pin the freeze manifest to the current HEAD, but ONLY if no frozen script moved.

PHASE 0 rule: never silently re-pin and pretend it is the same acceptance window.
If any frozen script byte differs from the PHASE 0 record -> report MOVED and exit 9.
"""
import subprocess, os, hashlib, io, json, datetime, sys

ROOT = str(Path(__file__).resolve().parents[2])
FROZEN = ["scripts/acceptance.ps1", "scripts/bootstrap.ps1", "scripts/doctor.ps1",
          "scripts/lib/JccCommon.ps1", "scripts/restart.ps1", "scripts/start.ps1",
          "scripts/status.ps1", "scripts/stop.ps1"]
MANIFEST = os.path.join(ROOT, ".workbuddy", "scratch", "continuation", "freeze_manifest.json")
PHASE0 = os.path.join(ROOT, ".workbuddy", "scratch", "continuation", "acceptance_freeze.json")

def sha256_file(p):
    if not os.path.isfile(p):
        return None
    h = hashlib.sha256()
    with io.open(p, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()

head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                      text=True).stdout.strip().splitlines()[0]
cur = {rel: sha256_file(os.path.join(ROOT, rel.replace("/", "\\"))) for rel in FROZEN}
phase0 = json.load(io.open(PHASE0, encoding="utf-8")) if os.path.isfile(PHASE0) else {}
p0 = phase0.get("FROZEN_SCRIPT_SHA256", {})

moved = [{"path": k, "phase0": p0.get(k), "now": cur.get(k)} for k in FROZEN if p0.get(k) != cur.get(k)]
print("PHASE0_HEAD  =", phase0.get("ACCEPTANCE_HEAD"))
print("CURRENT_HEAD =", head)
if moved:
    print("!!! FROZEN SCRIPTS MOVED (refusing to re-pin):")
    for m in moved:
        print("   ", m)
    sys.exit(9)
print("FROZEN SCRIPTS: byte-identical to PHASE 0 (8/8) -> safe to re-pin head_sha")
man = {"scripts_freeze_sha": head, "head_sha": head,
       "taken_at": datetime.datetime.now().isoformat(), "hashes": cur}
os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
with io.open(MANIFEST, "w", encoding="utf-8") as fh:
    json.dump(man, fh, indent=2, ensure_ascii=False)
print("manifest re-pinned ->", MANIFEST)
