#!/usr/bin/env python3
"""Falsifiers for the runtime exclusive lease (deliverable H).

Proves the port-owner lease can genuinely refuse a second owner and genuinely
reclaim an abandoned one, and that its read-only check writes nothing. Runs
against a scratch root only; port 4778 is referenced as a NUMBER, never bound or
connected -- the module holds no socket code at all (proved by a static scan).

Run:
    python tools/tests/runtime_lease_falsifier.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "scripts" / "lib" / "JccRuntimeLease.ps1"
PS = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / \
    "v1.0" / "powershell.exe"
PY = REPO / ".venv" / "Scripts" / "python.exe"
PORT = 4778


def _contract():
    """The proof contract, by path -- one implementation of the proof schema."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "jcc_proof_contract", str(REPO / "tools" / "continuation" / "proof_contract.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _ps(cmd, timeout=120):
    p = subprocess.run([str(PS), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-Command", cmd], capture_output=True, timeout=timeout)
    text = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode, text


def _lease_path(root: Path):
    return root / ".workbuddy" / "locks" / ("runtime-%d.lock.json" % PORT)


def _enter_cmd(root: Path, out: Path):
    return ("$ErrorActionPreference='Stop'; . '%s'; "
            "$r = Enter-JccRuntimeLease -Root '%s' -Port %d; "
            "$o = [pscustomobject]@{ ok=$r.ok; owner=$r.owner; reason=$r.reason }; "
            "[System.IO.File]::WriteAllText('%s', ($o | ConvertTo-Json -Compress), "
            "(New-Object System.Text.UTF8Encoding($false)))"
            % (LIB, root, PORT, out))


def _scratch(tmp: Path) -> Path:
    root = tmp / "root"
    (root / ".workbuddy" / "locks").mkdir(parents=True, exist_ok=True)
    return root


def _dead_pid():
    p = subprocess.Popen([str(PY), "-c", "pass"])
    pid = p.pid
    p.wait()
    time.sleep(0.5)
    return pid


def _run_case(fn, tmp):
    try:
        return fn(tmp)
    except Exception as exc:                                  # noqa: BLE001
        return False, "the case raised %s: %s" % (type(exc).__name__, exc)


def case_parse(tmp):
    """The module must parse under Windows PowerShell 5.1."""
    cmd = ("$t=$null;$e=$null;"
           "[System.Management.Automation.Language.Parser]::ParseFile('%s',[ref]$t,[ref]$e)|Out-Null;"
           "Write-Output @($e).Count" % LIB)
    rc, out = _ps(cmd)
    n = out.strip().splitlines()[-1] if out.strip() else "?"
    return (rc == 0 and n == "0"), "parse errors=%s" % n


def case_live_owner_refused(tmp):
    """A second owner is refused while a live, fresh owner holds the lease."""
    root = _scratch(tmp)
    out = tmp / "holder.json"
    holder = subprocess.Popen([str(PS), "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                               "Bypass", "-Command",
                               _enter_cmd(root, out) + "; Start-Sleep -Seconds 12; "
                               ". '%s'; Exit-JccRuntimeLease -Root '%s' -Port %d | Out-Null"
                               % (LIB, root, PORT)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        got = None
        for _ in range(60):
            if out.is_file():
                got = json.loads(out.read_text(encoding="utf-8"))
                break
            time.sleep(0.25)
        if not (got and got.get("ok")):
            return False, "the holder never acquired: %r" % got
        second = tmp / "second.json"
        rc, _ = _ps(_enter_cmd(root, second))
        res = json.loads(second.read_text(encoding="utf-8")) if second.is_file() else {}
        if res.get("ok"):
            return False, "a SECOND owner acquired the lease while the first held it"
        if "live owner" not in str(res.get("reason")):
            return False, "refused for the wrong reason: %s" % res.get("reason")
        return True, "second owner refused: %s" % res.get("reason")
    finally:
        holder.wait(timeout=30)


def case_records_identity(tmp):
    """The lease records port, pid, the REAL process start time, and a heartbeat."""
    root = _scratch(tmp)
    out = tmp / "holder.json"
    holder = subprocess.Popen([str(PS), "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                               "Bypass", "-Command",
                               _enter_cmd(root, out) + "; Start-Sleep -Seconds 6"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        lease = None
        for _ in range(60):
            if _lease_path(root).is_file():
                try:
                    lease = json.loads(_lease_path(root).read_text(encoding="utf-8-sig"))
                except ValueError:
                    lease = None
                if lease:
                    break
            time.sleep(0.25)
        if not lease:
            return False, "no lease was written"
        missing = [k for k in ("port", "pid", "process_start_time", "heartbeat_at",
                               "acquired_at", "expires_at") if not lease.get(k)]
        if missing:
            return False, "lease is missing %s" % missing
        if int(lease["port"]) != PORT:
            return False, "wrong port recorded: %s" % lease["port"]
        return True, "records %s" % ", ".join(sorted(lease.keys()))
    finally:
        holder.wait(timeout=30)


def case_readonly_check(tmp):
    """The read-only owner check must not modify the lease file."""
    root = _scratch(tmp)
    out = tmp / "holder.json"
    holder = subprocess.Popen([str(PS), "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                               "Bypass", "-Command",
                               _enter_cmd(root, out) + "; Start-Sleep -Seconds 8"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            if _lease_path(root).is_file():
                break
            time.sleep(0.25)
        before = _lease_path(root).read_bytes()
        cmd = (". '%s'; $r = Test-JccRuntimeLeaseOwner -Root '%s' -Port %d; "
               "Write-Output ('HELD=' + $r.held + ' PID=' + $r.pid)" % (LIB, root, PORT))
        rc, printed = _ps(cmd)
        after = _lease_path(root).read_bytes()
        if before != after:
            return False, "the read-only check rewrote the lease"
        if "HELD=True" not in printed:
            return False, "the read-only check did not see the live owner: %s" % printed.strip()[-120:]
        return True, "owner seen and file byte-identical"
    finally:
        holder.wait(timeout=30)


def case_stale_heartbeat_reclaimable(tmp):
    """A lease whose heartbeat has stopped must be reclaimable (do not wedge)."""
    root = _scratch(tmp)
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3600))
    _lease_path(root).write_text(json.dumps({
        "schema": "jcc.runtime-lease/1", "port": PORT, "pid": os.getpid(),
        "process_start_time": None, "acquired_at": old, "heartbeat_at": old,
        "expires_at": old}), encoding="utf-8")
    out = tmp / "reclaim.json"
    rc, _ = _ps(_enter_cmd(root, out))
    res = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    return bool(res.get("ok")), "reclaim ok=%s reason=%s" % (res.get("ok"), res.get("reason"))


def case_dead_owner_reclaimable(tmp):
    """A lease whose owner died must be reclaimable."""
    root = _scratch(tmp)
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    _lease_path(root).write_text(json.dumps({
        "schema": "jcc.runtime-lease/1", "port": PORT, "pid": _dead_pid(),
        "process_start_time": None, "acquired_at": now, "heartbeat_at": now,
        "expires_at": now}), encoding="utf-8")
    out = tmp / "reclaim2.json"
    rc, _ = _ps(_enter_cmd(root, out))
    res = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    return bool(res.get("ok")), "reclaim ok=%s reason=%s" % (res.get("ok"), res.get("reason"))


def case_concurrent_election(tmp):
    """Two simultaneous acquisitions must yield EXACTLY one owner."""
    root = _scratch(tmp)
    procs = []
    outs = []
    for i in range(2):
        o = tmp / ("race%d.json" % i)
        outs.append(o)
        procs.append(subprocess.Popen([str(PS), "-NoProfile", "-NonInteractive",
                                       "-ExecutionPolicy", "Bypass", "-Command",
                                       _enter_cmd(root, o) + "; Start-Sleep -Seconds 4"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    results = []
    for p, o in zip(procs, outs):
        p.wait(timeout=60)
        if o.is_file():
            results.append(json.loads(o.read_text(encoding="utf-8")))
    winners = [r for r in results if r.get("ok")]
    return len(winners) == 1, "winners=%d of %d (%s)" % (
        len(winners), len(results), [r.get("reason") for r in results])


def case_no_socket(tmp):
    """The module must hold no socket code: exclusivity is a FILE, not a port grab."""
    text = LIB.read_text(encoding="utf-8")
    banned = [b for b in ("TcpListener", "TcpClient", "System.Net.Sockets",
                          "Connect(", "Start-Job") if b in text]
    return not banned, "banned constructs=%s" % (banned or "none")


CASES = (
    ("parse under PowerShell 5.1", case_parse),
    ("a live owner is refused a second owner", case_live_owner_refused),
    ("the lease records pid identity and a heartbeat", case_records_identity),
    ("the read-only owner check writes nothing", case_readonly_check),
    ("a stopped heartbeat is reclaimable", case_stale_heartbeat_reclaimable),
    ("a dead owner is reclaimable", case_dead_owner_reclaimable),
    ("concurrent election has exactly one winner", case_concurrent_election),
    ("the lease holds no socket code", case_no_socket),
)


def main() -> int:
    import tempfile
    results = []
    for name, fn in CASES:
        with tempfile.TemporaryDirectory(prefix="jcc_rlease_") as d:
            ok, detail = _run_case(fn, Path(d))
        results.append((name, ok, detail))
        print("  [%s] %-48s %s" % ("PASS" if ok else "FAIL", name, detail[:100]))
    bad = [r for r in results if not r[1]]
    print("=" * 84)
    print("runtime-lease falsifiers: %d/%d" % (len(results) - len(bad), len(results)))

    # The §L claim is only CHECKED if FINAL-12 can read a machine-readable verdict,
    # so the runner writes one. That is the whole point of this file existing in
    # the pipeline rather than only on the machine of whoever ran it.
    contract = _contract()
    proof = contract.build_proof("RUNTIME_LEASE_PROOF", results,
                                 extra={"port": PORT, "cases": [c[0] for c in CASES]})
    out = contract.write_proof(str(REPO), "RUNTIME_LEASE_PROOF", proof)
    print("proof: %s (%s)" % (out, proof["status"]))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
